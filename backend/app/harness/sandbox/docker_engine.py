"""Controlled host-side Docker Engine setup helper.

This is intentionally not a generic host shell. It only checks Docker status, returns a
fixed install script, or (when explicitly enabled by env/config) executes a fixed package-manager
Docker Engine install flow for supported Linux hosts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.harness.sandbox.config import SlotFlowSandboxConfig


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]

SUPPORTED_INSTALL_MANAGERS = {"apt", "dnf", "pacman"}

# 依次尝试的守护进程启动方式:systemd 主机 → sysvinit 包装 → 无 init 管理的
# WSL 等环境直接后台拉起 dockerd。全部 `sudo -n` 非交互,失败即跳下一种。
_DAEMON_START_COMMANDS: list[tuple[str, list[str]]] = [
    ("systemctl", ["sudo", "-n", "systemctl", "start", "docker"]),
    ("service", ["sudo", "-n", "service", "docker", "start"]),
    (
        "dockerd",
        [
            "sudo",
            "-n",
            "sh",
            "-c",
            "nohup dockerd > /var/log/slotflow-dockerd.log 2>&1 &",
        ],
    ),
]


@dataclass(slots=True)
class DockerEngineSetup:
    config: SlotFlowSandboxConfig
    runner: Runner = subprocess.run
    which: Which = shutil.which
    os_release_path: Path = Path("/etc/os-release")
    sleep: Callable[[float], None] = time.sleep

    def run(
        self,
        *,
        action: str = "check",
        confirm_host_install: bool = False,
    ) -> dict[str, Any]:
        normalized = action.strip().lower()
        if normalized == "check":
            return self.check()
        if normalized == "start":
            return self.ensure_daemon()
        if normalized == "install_script":
            return self.install_script()
        if normalized == "install":
            return self.install(confirm_host_install=confirm_host_install)
        return {
            "ok": False,
            "error": f"unsupported action: {action!r}",
            "allowed_actions": ["check", "start", "install_script", "install"],
            "source": "slotflow_docker_engine_setup",
        }

    def check(self) -> dict[str, Any]:
        host = self.host_info()
        docker_path = self.which("docker")
        if docker_path is None:
            return {
                "ok": False,
                "installed": False,
                "daemon_running": False,
                "error": "docker_cli_missing",
                "hint": "Use docker_engine_setup(action='install_script') for manual install commands, or action='install' with confirm_host_install=true after explicit user approval.",
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        server_version = self._server_version()
        if server_version is not None:
            return {
                "ok": True,
                "installed": True,
                "daemon_running": True,
                "docker_path": docker_path,
                "server_version": server_version,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        # 已安装但守护进程没起(WSL 无 systemd 的典型死角):自动尝试拉起,
        # 不再把"跑一条 systemctl"甩给用户。
        started = self.ensure_daemon()
        if started.get("ok"):
            return {
                "ok": True,
                "installed": True,
                "daemon_running": True,
                "daemon_autostarted_via": started.get("method"),
                "docker_path": docker_path,
                "server_version": self._server_version(),
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        return {
            "ok": False,
            "installed": True,
            "daemon_running": False,
            "docker_path": docker_path,
            "error": started.get("error") or "docker daemon is not reachable",
            "autostart_attempts": started.get("attempts"),
            "hint": "Docker CLI exists but the daemon could not be started automatically. Check `sudo dockerd` output or restart Docker Desktop.",
            "host": host,
            "source": "slotflow_docker_engine_setup",
        }

    def ensure_daemon(self) -> dict[str, Any]:
        """确保 dockerd 可达;不可达时按三级回退自动拉起并轮询等待。"""

        if self._daemon_reachable():
            return {
                "ok": True,
                "already_running": True,
                "source": "slotflow_docker_engine_setup",
            }
        if self.which("docker") is None:
            return {
                "ok": False,
                "error": "docker_cli_missing",
                "source": "slotflow_docker_engine_setup",
            }

        attempts: list[dict[str, Any]] = []
        for method, command in _DAEMON_START_COMMANDS:
            try:
                result = self.runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                attempts.append({"method": method, "error": str(exc)})
                continue
            attempts.append(
                {
                    "method": method,
                    "returncode": result.returncode,
                    "stderr": _truncate_text(result.stderr, 500),
                }
            )
            # 启动命令本身成功与否都轮询一下:dockerd 分支即使命令返回也需等待就绪。
            if self._wait_for_daemon(seconds=12):
                return {
                    "ok": True,
                    "method": method,
                    "attempts": attempts,
                    "source": "slotflow_docker_engine_setup",
                }
        return {
            "ok": False,
            "error": "failed to start docker daemon automatically",
            "attempts": attempts,
            "source": "slotflow_docker_engine_setup",
        }

    def _daemon_reachable(self) -> bool:
        try:
            result = self.runner(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _wait_for_daemon(self, *, seconds: int) -> bool:
        for _ in range(max(1, seconds)):
            if self._daemon_reachable():
                return True
            self.sleep(1)
        return False

    def _server_version(self) -> str | None:
        try:
            result = self.runner(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def install_script(self) -> dict[str, Any]:
        host = self.host_info()
        script = self._install_script_for_host(host)
        return {
            "ok": script is not None,
            "script": script,
            "host": host,
            "notes": [
                "The script is generated from /etc/os-release and uses a fixed package-manager flow.",
                "After usermod, the user may need to log out and back in before non-sudo docker works.",
            ],
            "hint": (
                None
                if script is not None
                else "Automatic script generation supports Debian/Ubuntu, Fedora/RHEL-like, and Arch-like Linux hosts. Install Docker Engine manually for this host."
            ),
            "source": "slotflow_docker_engine_setup",
        }

    def install(self, *, confirm_host_install: bool) -> dict[str, Any]:
        host = self.host_info()
        script = self._install_script_for_host(host)
        if not self.config.allow_host_docker_install:
            return {
                "ok": False,
                "error": "host Docker Engine install is disabled",
                "hint": "Host Docker Engine install is disabled by SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=false. Ask the user to run the returned install_script manually.",
                "script": script,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        if not confirm_host_install:
            return {
                "ok": False,
                "error": "confirm_host_install=true is required",
                "hint": "Installing Docker Engine modifies the host OS. Call again with confirm_host_install=true only after the user explicitly asks to install it.",
                "script": script,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        manager = host.get("install_manager")
        if not isinstance(manager, str) or manager not in SUPPORTED_INSTALL_MANAGERS:
            return {
                "ok": False,
                "error": "unsupported host OS for automatic Docker install",
                "hint": "Automatic install supports Debian/Ubuntu, Fedora/RHEL-like, and Arch-like Linux hosts. Use install_script if available or give OS-specific manual instructions.",
                "script": script,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        manager_binary = self._manager_binary(manager)
        if manager_binary is None or self.which(manager_binary) is None:
            return {
                "ok": False,
                "error": f"{manager_binary or manager} is not available",
                "hint": "The detected package manager is not available on PATH. Return install_script or give OS-specific manual instructions.",
                "script": script,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }
        if self.which("sudo") is None and os.geteuid() != 0:
            return {
                "ok": False,
                "error": "sudo is not available",
                "hint": "Run the install_script manually as root.",
                "script": script,
                "host": host,
                "source": "slotflow_docker_engine_setup",
            }

        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        user_name = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        steps = self._install_commands(manager, prefix=prefix)
        if user_name:
            steps.append([*prefix, "usermod", "-aG", "docker", user_name])

        completed: list[dict[str, Any]] = []
        for command in steps:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=max(120, self.config.docker_timeout_seconds * 5),
                check=False,
            )
            completed.append(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "stdout": _truncate_text(result.stdout, 4000),
                    "stderr": _truncate_text(result.stderr, 4000),
                }
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": "Docker Engine install command failed",
                    "failed_command": command,
                    "completed": completed,
                    "hint": "If sudo requires a password, run docker_engine_setup(action='install_script') and execute it manually in a terminal.",
                    "source": "slotflow_docker_engine_setup",
                }

        status = self.check()
        return {
            "ok": bool(status.get("ok")),
            "completed": completed,
            "status": status,
            "host": host,
            "hint": "If docker still requires sudo, log out and back in so the docker group membership takes effect.",
            "source": "slotflow_docker_engine_setup",
        }

    def host_info(self) -> dict[str, Any]:
        values = self._os_release_values()
        os_id = values.get("ID", "")
        id_like = values.get("ID_LIKE", "")
        os_ids = {os_id, *id_like.split()}
        manager = self._install_manager_for_ids(os_ids)
        return {
            "os_id": os_id or None,
            "id_like": id_like or None,
            "pretty_name": values.get("PRETTY_NAME") or None,
            "install_manager": manager,
            "install_supported": manager in SUPPORTED_INSTALL_MANAGERS,
        }

    def _os_release_values(self) -> dict[str, str]:
        try:
            content = self.os_release_path.read_text(encoding="utf-8")
        except OSError:
            return {}
        values: dict[str, str] = {}
        for line in content.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"').lower()
        return values

    def _install_manager_for_ids(self, os_ids: set[str]) -> str | None:
        if os_ids & {"debian", "ubuntu"}:
            return "apt"
        if os_ids & {"fedora", "rhel", "centos", "rocky", "almalinux"}:
            return "dnf"
        if os_ids & {"arch", "manjaro"}:
            return "pacman"
        return None

    def _manager_binary(self, manager: str) -> str | None:
        return {
            "apt": "apt-get",
            "dnf": "dnf",
            "pacman": "pacman",
        }.get(manager)

    def _install_script_for_host(self, host: dict[str, Any]) -> str | None:
        manager = host.get("install_manager")
        if not isinstance(manager, str) or manager not in SUPPORTED_INSTALL_MANAGERS:
            return None
        prefix = ["sudo"]
        commands = self._install_commands(manager, prefix=prefix)
        commands.append([*prefix, "usermod", "-aG", "docker", "$USER"])
        commands.extend([["docker", "--version"], ["docker", "run", "--rm", "hello-world"]])
        lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
        lines.extend(_shell_join(command) for command in commands)
        return "\n".join(lines) + "\n"

    def _install_commands(self, manager: str, *, prefix: list[str]) -> list[list[str]]:
        if manager == "apt":
            return [
                [*prefix, "apt-get", "update"],
                [*prefix, "apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
                [*prefix, "systemctl", "enable", "--now", "docker"],
            ]
        if manager == "dnf":
            return [
                [*prefix, "dnf", "install", "-y", "moby-engine", "docker-compose-plugin"],
                [*prefix, "systemctl", "enable", "--now", "docker"],
            ]
        if manager == "pacman":
            return [
                [*prefix, "pacman", "-Sy", "--noconfirm", "docker", "docker-compose"],
                [*prefix, "systemctl", "enable", "--now", "docker"],
            ]
        return []


def _truncate_text(value: str | bytes | None, max_chars: int) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n...[truncated]"


def _shell_join(command: list[str]) -> str:
    parts: list[str] = []
    for item in command:
        if item == "$USER":
            parts.append('"$USER"')
        elif item.replace("-", "").replace("_", "").replace(".", "").replace("/", "").isalnum():
            parts.append(item)
        else:
            parts.append("'" + item.replace("'", "'\"'\"'") + "'")
    return " ".join(parts)
