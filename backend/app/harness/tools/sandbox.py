"""Tools for executing code inside a lazy Docker sandbox."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.docker import DockerSandboxError, LazyDockerSandbox
from app.harness.sandbox.docker_engine import DockerEngineSetup


def build_sandbox_tools(
    config: SlotFlowSandboxConfig | None = None,
    *,
    thread_id: str | None = None,
    skills_root: Path | None = None,
) -> list[BaseTool]:
    """Build code-execution tools bound to this run's workspace and thread."""

    sandbox_config = config or SlotFlowSandboxConfig()
    if not sandbox_config.code_execution_enabled:
        return []

    docker_sandbox = LazyDockerSandbox(
        config=sandbox_config,
        thread_id=thread_id,
        skills_root=skills_root,
    )
    docker_engine_setup = DockerEngineSetup(config=sandbox_config)

    @tool("sandbox_exec")
    def sandbox_exec(command: str, timeout_seconds: int | None = None) -> str:
        """Run code or scripts inside a lazy Docker sandbox.

        Use this for ALL shell/bash/python/node/package commands, untrusted code,
        generated scripts, Skill helper scripts, dependency installation, package
        experiments, and commands that must not run on the host. The sandbox starts
        only on first use. The default image includes Python, pip, bash, and common
        build tooling; use `python -m pip install ...` inside the sandbox when a
        script needs Python dependencies. Paths inside the container:
        `/workspace/uploads` = read-only user uploads,
        `/workspace/artifacts` = read/write current conversation artifacts,
        `/workspace/work` = read/write scratch working directory,
        `/workspace/skills` = read-only installed Skills when available.
        Write user-visible outputs to `/workspace/artifacts`; they are bind-mounted
        back to SlotFlow's local workspace automatically.
        """

        try:
            result = docker_sandbox.exec(
                command,
                timeout_seconds=timeout_seconds,
            )
        except DockerSandboxError as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "hint": "Install/start Docker or disable code execution with SLOTFLOW_CODE_EXECUTION_ENABLED=false.",
                "source": "slotflow_docker_sandbox",
            }
        return json.dumps(result, ensure_ascii=False)

    @tool("docker_engine_setup")
    def docker_engine_setup_tool(
        action: str = "check",
        confirm_host_install: bool = False,
    ) -> str:
        """Check or install the host Docker Engine needed by sandbox_exec.

        This is a controlled host setup tool, not a shell. Use action="check"
        when sandbox_exec reports Docker is unavailable — check now also tries to
        AUTO-START an installed-but-stopped daemon (systemctl/service/dockerd via
        non-interactive sudo), so a stopped daemon usually self-heals here.
        Use action="start" to only start the daemon. Use action="install_script"
        to return the exact manual install script for the detected Linux host. Use action="install"
        only after the user explicitly asks to install Docker Engine; it runs a fixed
        package-manager install flow for supported Linux hosts and works only when
        SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=true and confirm_host_install=true.
        Do not use this for arbitrary host commands.
        """

        result = docker_engine_setup.run(
            action=action,
            confirm_host_install=confirm_host_install,
        )
        return json.dumps(result, ensure_ascii=False)

    return [sandbox_exec, docker_engine_setup_tool]
