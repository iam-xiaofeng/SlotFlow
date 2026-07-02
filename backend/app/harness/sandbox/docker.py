"""Lazy Docker sandbox for code/script execution.

The sandbox is intentionally mounted to SlotFlow's existing workspace boundary instead
of copying files in/out:

- `/workspace/uploads` is read-only user input.
- `/workspace/artifacts` is read-write for the current thread's generated files.
- `/workspace/work` is read-write scratch for code/scripts.
- `/workspace/skills` is read-only installed Skills content when configured.

Docker is touched only when a command is actually executed.
"""

from __future__ import annotations

import atexit
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.workspace import build_slotflow_workspace
from app.harness.tools.workspace import artifact_dir_for_thread


class DockerSandboxError(RuntimeError):
    """Raised when the Docker sandbox cannot start or execute a command."""


@dataclass(frozen=True, slots=True)
class DockerSandboxMounts:
    uploads: Path
    artifacts: Path
    work: Path
    skills: Path | None


class LazyDockerSandbox:
    """Small, dependency-free Docker sandbox wrapper using the Docker CLI."""

    def __init__(
        self,
        *,
        config: SlotFlowSandboxConfig | None = None,
        thread_id: str | None = None,
        skills_root: Path | None = None,
        runner=subprocess.run,
        timer_factory: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
    ) -> None:
        self.config = config or SlotFlowSandboxConfig()
        self.thread_id = thread_id
        self.skills_root = skills_root
        self._runner = runner
        self._timer_factory = timer_factory
        self._workspace = build_slotflow_workspace(self.config)
        self._container_id: str | None = None
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.RLock()

    @property
    def started(self) -> bool:
        return self._container_id is not None

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Execute a shell command inside the lazily-created Docker container."""

        if not self.config.code_execution_enabled:
            return {
                "ok": False,
                "error": "code execution sandbox is disabled",
                "source": "slotflow_docker_sandbox",
            }
        stripped = command.strip()
        if not stripped:
            return {
                "ok": False,
                "error": "command is empty",
                "source": "slotflow_docker_sandbox",
            }

        with self._lock:
            self._cancel_idle_close_locked()
            container_id = self._ensure_started()
            timeout = _effective_timeout(
                requested=timeout_seconds,
                default=self.config.docker_timeout_seconds,
            )
            try:
                result = self._runner(
                    [
                        "docker",
                        "exec",
                        "-w",
                        "/workspace/work",
                        container_id,
                        "sh",
                        "-lc",
                        stripped,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._schedule_idle_close_locked()
                return {
                    "ok": False,
                    "timed_out": True,
                    "timeout_seconds": timeout,
                    "stdout": _truncate_text(exc.stdout or "", self.config.max_read_bytes),
                    "stderr": _truncate_text(exc.stderr or "", self.config.max_read_bytes),
                    "source": "slotflow_docker_sandbox",
                }
            except OSError as exc:
                self._schedule_idle_close_locked()
                return {
                    "ok": False,
                    "error": str(exc),
                    "hint": "Docker CLI is required for sandbox_exec.",
                    "source": "slotflow_docker_sandbox",
                }
            self._schedule_idle_close_locked()

        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": _truncate_text(result.stdout, self.config.max_read_bytes),
            "stderr": _truncate_text(result.stderr, self.config.max_read_bytes),
            "timeout_seconds": timeout,
            "idle_timeout_seconds": self.config.docker_idle_timeout_seconds,
            "mounts": self.mount_summary(),
            "source": "slotflow_docker_sandbox",
        }

    def mount_summary(self) -> dict[str, str | None]:
        mounts = self._mounts()
        return {
            "/workspace/uploads": "read-only user uploads",
            "/workspace/artifacts": f"read-write current thread artifacts -> {mounts.artifacts}",
            "/workspace/work": f"read-write scratch -> {mounts.work}",
            "/workspace/skills": (
                f"read-only installed skills -> {mounts.skills}"
                if mounts.skills is not None
                else None
            ),
        }

    def close(self) -> None:
        with self._lock:
            self._cancel_idle_close_locked()
            container_id = self._container_id
            if container_id is None:
                return
            self._container_id = None
        self._runner(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            text=True,
            check=False,
        )

    def _schedule_idle_close_locked(self) -> None:
        if self._container_id is None:
            return
        timeout = max(1, self.config.docker_idle_timeout_seconds)
        timer = self._timer_factory(timeout, self.close)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _cancel_idle_close_locked(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def _ensure_started(self) -> str:
        if self._container_id is not None:
            return self._container_id

        mounts = self._mounts()
        mounts.uploads.mkdir(parents=True, exist_ok=True)
        mounts.artifacts.mkdir(parents=True, exist_ok=True)
        mounts.work.mkdir(parents=True, exist_ok=True)

        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--init",
            "--network",
            "bridge" if self.config.docker_network_enabled else "none",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PIP_DISABLE_PIP_VERSION_CHECK=1",
            "-w",
            "/workspace/work",
            "--mount",
            _bind_mount(mounts.uploads, "/workspace/uploads", readonly=True),
            "--mount",
            _bind_mount(mounts.artifacts, "/workspace/artifacts", readonly=False),
            "--mount",
            _bind_mount(mounts.work, "/workspace/work", readonly=False),
        ]
        if mounts.skills is not None:
            command.extend(
                [
                    "--mount",
                    _bind_mount(mounts.skills, "/workspace/skills", readonly=True),
                ]
            )
        command.extend([self.config.docker_image, "sleep", "3600"])

        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.docker_timeout_seconds,
                check=False,
            )
        except OSError as exc:
            raise DockerSandboxError("Docker CLI is required for sandbox_exec") from exc

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "docker run failed"
            raise DockerSandboxError(message)

        container_id = result.stdout.strip().splitlines()[-1]
        if not container_id:
            raise DockerSandboxError("docker run did not return a container id")

        self._container_id = container_id
        atexit.register(self.close)
        return container_id

    def _mounts(self) -> DockerSandboxMounts:
        thread_key = _safe_thread_key(self.thread_id)
        artifact_path = self._workspace.resolve_path(artifact_dir_for_thread(self.thread_id))
        work_path = self._workspace.resolve_path(f".sandbox/{thread_key}")
        uploads_path = self._workspace.resolve_path("uploads")
        skills_path = (
            self.skills_root.expanduser().resolve(strict=False)
            if self.skills_root is not None
            else None
        )
        return DockerSandboxMounts(
            uploads=uploads_path,
            artifacts=artifact_path,
            work=work_path,
            skills=skills_path if skills_path is not None and skills_path.exists() else None,
        )


def _bind_mount(source: Path, target: str, *, readonly: bool) -> str:
    payload = ["type=bind", f"source={source}", f"target={target}"]
    if readonly:
        payload.append("readonly=true")
    return ",".join(payload)


def _safe_thread_key(thread_id: str | None) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (thread_id or "default").strip())
    return cleaned.strip(".-") or "default"


def _effective_timeout(*, requested: int | None, default: int) -> int:
    if requested is None:
        return max(1, default)
    return max(1, min(requested, max(1, default)))


def _truncate_text(value: str | bytes | None, max_bytes: int) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n...[truncated]"
