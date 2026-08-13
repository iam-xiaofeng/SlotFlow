"""Persistent Docker sandbox for code/script execution.

设计(2026-07-04,与用户共同决定):**全程共用一个具名容器**、空闲只停不删——

- 容器名 ``slotflow-sandbox-<workspace哈希>``,所有对话共享:镜像只存一份,
  agent 装的依赖(pip/apt)跨对话保留,不再"每次空闲回收后一切从零"。
- 空闲超时执行 ``docker stop``(内容保留),下次使用 ``docker start`` 秒级恢复;
  永不自动 ``rm``。磁盘增长只来自 agent 实际安装的内容,是最省盘的方案。
- 挂载(2026-08-09 改为按对话聚合,见 ``sandbox/layout.py``):工作区整根读写挂到
  ``/workspace``,``docker exec`` 的工作目录锁定在 ``/workspace/<thread>``——模型在里面
  ``ls`` 一次就能看到 ``work / artifacts / uploads`` 三个目录,不必再靠环境变量或
  system prompt 去猜路径。上传原件目录额外叠一层只读挂载防改;skills 挂在
  ``/skills``(**不能**挂进 ``/workspace`` 下:嵌套挂载点会在宿主工作区根目录留下一个
  空目录,破坏"根下只有对话目录")。
- 守护进程不可达时先尝试 ``DockerEngineSetup.ensure_daemon()`` 自动拉起
  (systemctl → service → rc-service → 直接 dockerd;非 root 时走 ``sudo -n``),再重试一次。

Docker is touched only when a command is actually executed.
"""

from __future__ import annotations

import atexit
import hashlib
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.docker_engine import DockerEngineSetup
from app.harness.sandbox.layout import (
    ARTIFACTS_DIR_NAME,
    CONTAINER_SKILLS_ROOT,
    CONTAINER_WORKSPACE_ROOT,
    LAYOUT_VERSION,
    THREAD_SUBDIR_NAMES,
    UPLOAD_ORIGINALS_DIR,
    container_artifacts_dir,
    container_thread_dir,
    thread_artifacts_dir,
    thread_dir,
    thread_dir_name,
)
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
    workspace_root: Path
    upload_originals: Path
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
        """共享容器名:同一 workspace 恒定;workspace 或**布局版本**变更时自然换新容器。

        布局版本必须进哈希——挂载结构是在 ``docker run`` 时固定的,换了布局却复用旧容器,
        跑的还是旧挂载,现象是"代码改了但容器里看到的还是老目录"。
        """

        root = str(self.config.resolved_workspace_root())
        digest = hashlib.sha256(f"{root}\0{LAYOUT_VERSION}".encode("utf-8")).hexdigest()[:8]
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
            thread_workdir = container_thread_dir(self.thread_id)
            thread_artifacts = container_artifacts_dir(self.thread_id)
            try:
                result = self._runner(
                    [
                        "docker",
                        "exec",
                        # 共享容器内按对话目录隔离:工作目录/HOME 锁定本对话目录,
                        # 进去就能 `ls` 到 work/artifacts/uploads;产物目录同时经环境
                        # 变量给出绝对路径,方便脚本直接写。
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
            source = _normalize_copy_source_path(source_path, thread_id=self.thread_id)
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
        thread_root = container_thread_dir(self.thread_id)
        return {
            CONTAINER_WORKSPACE_ROOT: (
                f"read-write SlotFlow workspace -> {mounts.workspace_root}; "
                f"current conversation directory: {thread_root}"
            ),
            f"{thread_root}/{ARTIFACTS_DIR_NAME}": "read-write user-visible artifacts",
            f"{thread_root}/uploads": "read-write per-run copies of user uploads",
            f"{thread_root}/work": "read-write scratch working directory (the exec cwd)",
            f"{CONTAINER_WORKSPACE_ROOT}/{UPLOAD_ORIGINALS_DIR}": (
                "read-only original uploads kept by SlotFlow"
            ),
            CONTAINER_SKILLS_ROOT: (
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
        # 目录必须先在宿主建好:bind 挂载的是整个工作区根,容器里 `mkdir` 也能建,
        # 但 cwd 不存在时 `docker exec -w` 会直接失败,所以三个子目录先落地。
        mounts.workspace_root.mkdir(parents=True, exist_ok=True)
        mounts.upload_originals.mkdir(parents=True, exist_ok=True)
        thread_root = mounts.workspace_root / thread_dir(self.thread_id)
        for subdir in THREAD_SUBDIR_NAMES:
            (thread_root / subdir).mkdir(parents=True, exist_ok=True)

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
            CONTAINER_WORKSPACE_ROOT,
            "--mount",
            _bind_mount(mounts.workspace_root, CONTAINER_WORKSPACE_ROOT, readonly=False),
            # 嵌套只读挂载:整根是可写的,单独把上传原件目录盖成只读,
            # 模型改不了用户的原始文件(每次 run 的副本仍在对话目录里可写)。
            "--mount",
            _bind_mount(
                mounts.upload_originals,
                f"{CONTAINER_WORKSPACE_ROOT}/{UPLOAD_ORIGINALS_DIR}",
                readonly=True,
            ),
        ]
        if mounts.skills is not None:
            command.extend(
                [
                    "--mount",
                    _bind_mount(mounts.skills, CONTAINER_SKILLS_ROOT, readonly=True),
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
        workspace_root = self._workspace.root
        upload_originals = self._workspace.resolve_path(UPLOAD_ORIGINALS_DIR)
        skills_path = (
            self.skills_root.expanduser().resolve(strict=False)
            if self.skills_root is not None
            else None
        )
        return DockerSandboxMounts(
            workspace_root=workspace_root,
            upload_originals=upload_originals,
            skills=skills_path if skills_path is not None and skills_path.exists() else None,
        )


def _bind_mount(source: Path, target: str, *, readonly: bool) -> str:
    payload = ["type=bind", f"source={source}", f"target={target}"]
    if readonly:
        payload.append("readonly=true")
    return ",".join(payload)


def _container_artifact_root(thread_id: str | None) -> str:
    return container_artifacts_dir(thread_id)


def _workspace_artifact_path(*, destination_tail: str, thread_id: str | None) -> str:
    return f"{thread_artifacts_dir(thread_id)}/{destination_tail}"


def _normalize_copy_source_path(source_path: str, *, thread_id: str | None) -> str:
    raw = source_path.strip()
    if not raw:
        raise ValueError("source_path must not be blank")
    if "\x00" in raw:
        raise ValueError("source_path contains a null byte")
    if "\\" in raw:
        raise ValueError("source_path must use forward slashes")

    thread_root = container_thread_dir(thread_id)
    path = PurePosixPath(raw)
    if path.is_absolute():
        normalized = _normalize_posix_path(path)
    else:
        # 相对路径按 exec 的 cwd(= 对话目录)解释,和模型看到的 `ls` 一致。
        normalized = _normalize_posix_path(PurePosixPath(thread_root) / path)

    # 整个对话目录都可以作为来源:work/ 里的中间产物、uploads/ 里的原始数据都算。
    allowed_roots = (thread_root, "/tmp")
    if not any(_is_same_or_child_posix(normalized, root) for root in allowed_roots):
        raise ValueError(
            f"source_path must stay inside this conversation directory ({thread_root}) or /tmp"
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
        # 模型经常把自己所在的目录一起写进来。宽容地剥掉 "<thread>/" 和 "artifacts/"
        # 两层前缀(顺序不限),剩下的才是产物目录内的相对名。
        raw = raw.lstrip("/")
        for _ in range(2):
            for prefix in (f"{thread_dir_name(thread_id)}/", f"{ARTIFACTS_DIR_NAME}/"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix) :].lstrip("/")

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
