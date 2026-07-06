"""Persistent Docker sandbox for code/script execution.

设计(2026-07-04,与用户共同决定):**全程共用一个具名容器**、空闲只停不删——

- 容器名 ``slotflow-sandbox-<workspace哈希>``,所有对话共享:镜像只存一份,
  agent 装的依赖(pip/apt)跨对话保留,不再"每次空闲回收后一切从零"。
- 空闲超时执行 ``docker stop``(内容保留),下次使用 ``docker start`` 秒级恢复;
  永不自动 ``rm``。磁盘增长只来自 agent 实际安装的内容,是最省盘的方案。
- 挂载改为工作区级:``/workspace/uploads``(只读)、``/workspace/artifacts``(全部
  线程可写)、``/workspace/work``(读写 scratch)、``/workspace/skills``(只读);
  每次 exec 的工作目录仍按线程隔离在 ``/workspace/work/<thread>``。
- 守护进程不可达时先尝试 ``DockerEngineSetup.ensure_daemon()`` 自动拉起
  (systemctl → service → rc-service → 直接 dockerd;非 root 时走 ``sudo -n``),再重试一次。

Docker is touched only when a command is actually executed.
"""

from __future__ import annotations

import atexit
import hashlib
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.docker_engine import DockerEngineSetup
from app.harness.sandbox.workspace import build_slotflow_workspace


class DockerSandboxError(RuntimeError):
    """Raised when the Docker sandbox cannot start or execute a command."""


_DAEMON_DOWN_MARKERS = (
    "cannot connect to the docker daemon",
    "docker.sock",
    "is the docker daemon running",
    "error during connect",
)


def _looks_like_daemon_down(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _DAEMON_DOWN_MARKERS)


@dataclass(frozen=True, slots=True)
class DockerSandboxMounts:
    uploads: Path
    artifacts_root: Path
    work_root: Path
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
        engine: DockerEngineSetup | None = None,
    ) -> None:
        self.config = config or SlotFlowSandboxConfig()
        self.thread_id = thread_id
        self.skills_root = skills_root
        self._runner = runner
        self._timer_factory = timer_factory
        self._engine = engine or DockerEngineSetup(config=self.config)
        self._workspace = build_slotflow_workspace(self.config)
        self._running = False
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.RLock()

    @property
    def container_name(self) -> str:
        """共享容器名:同一 workspace 恒定;workspace 变更时自然换新容器。"""

        root = str(self.config.resolved_workspace_root())
        digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:8]
        return f"slotflow-sandbox-{digest}"

    @property
    def started(self) -> bool:
        return self._running

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Execute a shell command inside the shared persistent Docker container."""

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
            self._ensure_started()
            timeout = _effective_timeout(
                requested=timeout_seconds,
                default=self.config.docker_timeout_seconds,
            )
            thread_key = _safe_thread_key(self.thread_id)
            thread_workdir = f"/workspace/work/{thread_key}"
            thread_artifacts = f"/workspace/artifacts/{self.thread_id or 'default'}"
            try:
                result = self._runner(
                    [
                        "docker",
                        "exec",
                        # 共享容器内按 thread 目录隔离:工作目录/HOME 锁定本线程,
                        # 产物目录经环境变量指向本线程,避免对话之间串台。
                        "-w",
                        thread_workdir,
                        "--env",
                        f"HOME={thread_workdir}",
                        "--env",
                        f"SLOTFLOW_THREAD_ARTIFACTS={thread_artifacts}",
                        self.container_name,
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
            "container": self.container_name,
            "mounts": self.mount_summary(),
            "source": "slotflow_docker_sandbox",
        }

    def copy_to_artifacts(
        self,
        *,
        source_path: str,
        artifact_path: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy one container file into this thread's user-visible artifact folder."""

        if not self.config.code_execution_enabled:
            return {
                "ok": False,
                "error": "code execution sandbox is disabled",
                "source": "slotflow_docker_artifact_copy",
            }
        if not self.config.writes_enabled:
            return {
                "ok": False,
                "error": "workspace writes are disabled",
                "source": "slotflow_docker_artifact_copy",
            }

        try:
            thread_key = _safe_thread_key(self.thread_id)
            source = _normalize_copy_source_path(
                source_path,
                thread_id=self.thread_id,
                thread_key=thread_key,
            )
            destination_tail = _normalize_artifact_destination_tail(
                artifact_path,
                source_path=source,
                thread_id=self.thread_id,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "source": "slotflow_docker_artifact_copy",
            }

        artifact_root = _container_artifact_root(self.thread_id)
        destination = f"{artifact_root}/{destination_tail}"
        script = _copy_artifact_script(
            source=source,
            destination=destination,
            max_bytes=self.config.max_write_bytes,
            overwrite=overwrite,
        )

        with self._lock:
            self._cancel_idle_close_locked()
            try:
                self._ensure_started()
                result = self._runner(
                    [
                        "docker",
                        "exec",
                        self.container_name,
                        "sh",
                        "-lc",
                        script,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.config.docker_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._schedule_idle_close_locked()
                return {
                    "ok": False,
                    "timed_out": True,
                    "timeout_seconds": self.config.docker_timeout_seconds,
                    "stdout": _truncate_text(exc.stdout or "", self.config.max_read_bytes),
                    "stderr": _truncate_text(exc.stderr or "", self.config.max_read_bytes),
                    "source": "slotflow_docker_artifact_copy",
                }
            except OSError as exc:
                self._schedule_idle_close_locked()
                return {
                    "ok": False,
                    "error": str(exc),
                    "hint": "Docker CLI is required for sandbox_artifact_copy.",
                    "source": "slotflow_docker_artifact_copy",
                }
            except DockerSandboxError as exc:
                self._schedule_idle_close_locked()
                return {
                    "ok": False,
                    "error": str(exc),
                    "hint": "Install/start Docker or disable code execution with SLOTFLOW_CODE_EXECUTION_ENABLED=false.",
                    "source": "slotflow_docker_artifact_copy",
                }
            self._schedule_idle_close_locked()

        stdout = (result.stdout or "").strip()
        bytes_copied = int(stdout) if stdout.isdigit() else None
        artifact_relative_path = _workspace_artifact_path(
            destination_tail=destination_tail,
            thread_id=self.thread_id,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "source_path": source,
            "path": artifact_relative_path,
            "bytes_copied": bytes_copied,
            "stdout": _truncate_text(result.stdout, self.config.max_read_bytes),
            "stderr": _truncate_text(result.stderr, self.config.max_read_bytes),
            "container": self.container_name,
            "mounts": self.mount_summary(),
            "source": "slotflow_docker_artifact_copy",
        }

    def mount_summary(self) -> dict[str, str | None]:
        mounts = self._mounts()
        thread_key = _safe_thread_key(self.thread_id)
        return {
            "/workspace/uploads": "read-only user uploads",
            "/workspace/artifacts": (
                f"read-write all-thread artifacts -> {mounts.artifacts_root}; "
                f"current thread: /workspace/artifacts/{self.thread_id or 'default'}"
            ),
            "/workspace/work": (
                f"read-write scratch -> {mounts.work_root}; "
                f"current thread cwd: /workspace/work/{thread_key}"
            ),
            "/workspace/skills": (
                f"read-only installed skills -> {mounts.skills}"
                if mounts.skills is not None
                else None
            ),
        }

    def close(self) -> None:
        """空闲/退出时只 stop 不 rm:容器内容(已装依赖等)全部保留。"""

        with self._lock:
            self._cancel_idle_close_locked()
            if not self._running:
                return
            self._running = False
        self._runner(
            ["docker", "stop", self.container_name],
            capture_output=True,
            text=True,
            check=False,
        )

    def _schedule_idle_close_locked(self) -> None:
        if not self._running:
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

    def _ensure_started(self) -> None:
        if self._running:
            return

        mounts = self._mounts()
        mounts.uploads.mkdir(parents=True, exist_ok=True)
        mounts.artifacts_root.mkdir(parents=True, exist_ok=True)
        mounts.work_root.mkdir(parents=True, exist_ok=True)
        (mounts.work_root / _safe_thread_key(self.thread_id)).mkdir(parents=True, exist_ok=True)
        if self.thread_id:
            (mounts.artifacts_root / self.thread_id).mkdir(parents=True, exist_ok=True)

        self._ensure_container(mounts, daemon_start_attempted=False)
        self._running = True
        atexit.register(self.close)

    def _ensure_container(self, mounts: DockerSandboxMounts, *, daemon_start_attempted: bool) -> None:
        state = self._container_state()
        if state == "running":
            return
        if state == "stopped":
            result = self._run_docker(["docker", "start", self.container_name])
            if result.returncode == 0:
                return
            message = result.stderr.strip() or result.stdout.strip() or "docker start failed"
            if _looks_like_daemon_down(message) and not daemon_start_attempted:
                self._try_start_daemon()
                return self._ensure_container(mounts, daemon_start_attempted=True)
            raise DockerSandboxError(message)

        if state == "daemon_down":
            if not daemon_start_attempted:
                self._try_start_daemon()
                return self._ensure_container(mounts, daemon_start_attempted=True)
            raise DockerSandboxError(
                "Docker daemon is not reachable and automatic start failed; "
                "call docker_engine_setup(action='check') for diagnostics"
            )

        # missing -> create the persistent container (NO --rm; contents survive stops)
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
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
            _bind_mount(mounts.artifacts_root, "/workspace/artifacts", readonly=False),
            "--mount",
            _bind_mount(mounts.work_root, "/workspace/work", readonly=False),
        ]
        if mounts.skills is not None:
            command.extend(
                [
                    "--mount",
                    _bind_mount(mounts.skills, "/workspace/skills", readonly=True),
                ]
            )
        command.extend([self.config.docker_image, "sleep", "infinity"])

        result = self._run_docker(command, timeout=max(300, self.config.docker_timeout_seconds))
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "docker run failed"
            if _looks_like_daemon_down(message) and not daemon_start_attempted:
                self._try_start_daemon()
                return self._ensure_container(mounts, daemon_start_attempted=True)
            raise DockerSandboxError(message)

    def _container_state(self) -> str:
        """Return one of: running / stopped / missing / daemon_down."""

        result = self._run_docker(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
        )
        if result.returncode == 0:
            return "running" if result.stdout.strip().lower() == "true" else "stopped"
        message = (result.stderr or result.stdout or "").strip()
        if _looks_like_daemon_down(message):
            return "daemon_down"
        return "missing"

    def _run_docker(self, command: list[str], *, timeout: int | None = None):
        try:
            return self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.config.docker_timeout_seconds,
                check=False,
            )
        except OSError as exc:
            raise DockerSandboxError("Docker CLI is required for sandbox_exec") from exc

    def _try_start_daemon(self) -> None:
        try:
            self._engine.ensure_daemon()
        except Exception:  # noqa: BLE001 - best effort; the retry surfaces the real error
            pass

    def _mounts(self) -> DockerSandboxMounts:
        artifacts_root = self._workspace.resolve_path("artifacts")
        work_root = self._workspace.resolve_path(".sandbox")
        uploads_path = self._workspace.resolve_path("uploads")
        skills_path = (
            self.skills_root.expanduser().resolve(strict=False)
            if self.skills_root is not None
            else None
        )
        return DockerSandboxMounts(
            uploads=uploads_path,
            artifacts_root=artifacts_root,
            work_root=work_root,
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


def _container_artifact_root(thread_id: str | None) -> str:
    cleaned = (thread_id or "").strip().strip("/")
    return f"/workspace/artifacts/{cleaned}" if cleaned else "/workspace/artifacts"


def _workspace_artifact_path(*, destination_tail: str, thread_id: str | None) -> str:
    cleaned = (thread_id or "").strip().strip("/")
    return (
        f"artifacts/{cleaned}/{destination_tail}"
        if cleaned
        else f"artifacts/{destination_tail}"
    )


def _normalize_copy_source_path(
    source_path: str,
    *,
    thread_id: str | None,
    thread_key: str,
) -> str:
    raw = source_path.strip()
    if not raw:
        raise ValueError("source_path must not be blank")
    if "\x00" in raw:
        raise ValueError("source_path contains a null byte")
    if "\\" in raw:
        raise ValueError("source_path must use forward slashes")

    path = PurePosixPath(raw)
    if path.is_absolute():
        normalized = _normalize_posix_path(path)
    else:
        normalized = _normalize_posix_path(
            PurePosixPath("/workspace/work") / thread_key / path
        )

    allowed_roots = (f"/workspace/work/{thread_key}", "/tmp")
    current_artifact_root = _container_artifact_root(thread_id)
    if not any(
        root and _is_same_or_child_posix(normalized, root)
        for root in (*allowed_roots, current_artifact_root)
    ):
        raise ValueError(
            "source_path must be relative to this thread workdir or under "
            "/workspace/work/<thread>, /tmp, or the current thread artifact folder"
        )
    return normalized


def _normalize_artifact_destination_tail(
    artifact_path: str,
    *,
    source_path: str,
    thread_id: str | None,
) -> str:
    raw = artifact_path.strip()
    if not raw:
        raw = PurePosixPath(source_path).name or "artifact"
    if "\x00" in raw:
        raise ValueError("artifact_path contains a null byte")
    if "\\" in raw:
        raise ValueError("artifact_path must use forward slashes")

    current_artifact_root = _container_artifact_root(thread_id)
    if raw.startswith("/"):
        normalized = _normalize_posix_path(PurePosixPath(raw))
        if not _is_same_or_child_posix(normalized, current_artifact_root):
            raise ValueError("absolute artifact_path must stay inside the current thread artifact folder")
        raw = normalized[len(current_artifact_root) :].lstrip("/")
    else:
        raw = raw.lstrip("/")
        if raw.startswith("artifacts/"):
            raw = raw[len("artifacts/") :].lstrip("/")
        cleaned_thread = (thread_id or "").strip().strip("/")
        if cleaned_thread and (raw == cleaned_thread or raw.startswith(f"{cleaned_thread}/")):
            raw = raw[len(cleaned_thread) :].lstrip("/")

    tail = _normalize_posix_path(PurePosixPath(raw), require_relative=True)
    if tail in {"", "."}:
        raise ValueError("artifact_path must include a file name")
    return tail


def _normalize_posix_path(path: PurePosixPath, *, require_relative: bool = False) -> str:
    if require_relative and path.is_absolute():
        raise ValueError("path must be relative")
    safe_parts: list[str] = []
    for part in path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            raise ValueError("path must not contain '..'")
        safe_parts.append(part)
    if path.is_absolute() and not require_relative:
        return "/" + "/".join(safe_parts)
    return "/".join(safe_parts)


def _is_same_or_child_posix(path: str, root: str) -> bool:
    clean_root = root.rstrip("/")
    return path == clean_root or path.startswith(f"{clean_root}/")


def _copy_artifact_script(
    *,
    source: str,
    destination: str,
    max_bytes: int,
    overwrite: bool,
) -> str:
    quoted_source = shlex.quote(source)
    quoted_destination = shlex.quote(destination)
    quoted_max_bytes = shlex.quote(str(max(0, max_bytes)))
    overwrite_flag = "1" if overwrite else "0"
    return "\n".join(
        [
            "set -eu",
            f"src={quoted_source}",
            f"dst={quoted_destination}",
            f"max_bytes={quoted_max_bytes}",
            f"overwrite={overwrite_flag}",
            'if [ ! -f "$src" ]; then echo "source_path is not a file: $src" >&2; exit 11; fi',
            'size=$(wc -c < "$src" | tr -d " ")',
            'if [ "$size" -gt "$max_bytes" ]; then echo "source file exceeds max_write_bytes: $size > $max_bytes" >&2; exit 12; fi',
            'mkdir -p "$(dirname "$dst")"',
            'if [ -e "$dst" ] && [ "$overwrite" != "1" ]; then echo "artifact_path already exists: $dst" >&2; exit 13; fi',
            'cp "$src" "$dst"',
            'printf "%s" "$size"',
        ]
    )


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
