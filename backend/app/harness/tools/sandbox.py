"""Tools for executing code inside a lazy Docker sandbox."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

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

    def sandbox_exec(command: str, timeout_seconds: int | None = None) -> str:
        """Run code or scripts inside a lazy Docker sandbox.

        Use this for ALL shell/bash/python/node/package commands, untrusted code,
        generated scripts, Skill helper scripts, dependency installation, package
        experiments, and commands that must not run on the host. The sandbox starts
        only on first use. The default image includes Python, pip, bash, and common
        build tooling; use `python -m pip install ...` inside the sandbox when a
        script needs Python dependencies. The working directory is this conversation's
        own folder `/workspace/<thread>`, and `ls` there shows everything you need:
        `work/` = read/write scratch, `artifacts/` = read/write user-visible outputs,
        `uploads/` = this conversation's copies of user uploads.
        `/skills` holds read-only installed Skills when available.
        Write user-visible outputs to `artifacts/` (also available as the absolute path
        in `$SLOTFLOW_THREAD_ARTIFACTS`); they are bind-mounted back to SlotFlow's local
        workspace automatically.
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

    async def asandbox_exec(command: str, timeout_seconds: int | None = None) -> str:
        return await asyncio.to_thread(sandbox_exec, command, timeout_seconds)

    def sandbox_artifact_copy(
        source_path: str,
        artifact_path: str = "",
        overwrite: bool = False,
    ) -> str:
        """Publish a file created inside Docker to the visible artifact panel.

        Use this after sandbox_exec creates a file in the current conversation's scratch
        directory or /tmp and the user should be able to open it. `source_path` may be
        relative to the sandbox workdir (this conversation's folder), anywhere inside
        that folder, or under /tmp. `artifact_path` is a destination filename/path
        relative to this conversation's artifact folder. The tool copies one file only,
        enforces max_write_bytes, and refuses to overwrite unless overwrite=true.
        """

        try:
            result = docker_sandbox.copy_to_artifacts(
                source_path=source_path,
                artifact_path=artifact_path,
                overwrite=overwrite,
            )
        except DockerSandboxError as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "hint": "Install/start Docker or disable code execution with SLOTFLOW_CODE_EXECUTION_ENABLED=false.",
                "source": "slotflow_docker_artifact_copy",
            }
        return json.dumps(result, ensure_ascii=False)

    async def asandbox_artifact_copy(
        source_path: str,
        artifact_path: str = "",
        overwrite: bool = False,
    ) -> str:
        return await asyncio.to_thread(
            sandbox_artifact_copy,
            source_path,
            artifact_path,
            overwrite,
        )

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

    async def adocker_engine_setup_tool(
        action: str = "check",
        confirm_host_install: bool = False,
    ) -> str:
        return await asyncio.to_thread(
            docker_engine_setup_tool,
            action,
            confirm_host_install,
        )

    return [
        StructuredTool.from_function(
            func=sandbox_exec,
            coroutine=asandbox_exec,
            name="sandbox_exec",
        ),
        StructuredTool.from_function(
            func=sandbox_artifact_copy,
            coroutine=asandbox_artifact_copy,
            name="sandbox_artifact_copy",
        ),
        StructuredTool.from_function(
            func=docker_engine_setup_tool,
            coroutine=adocker_engine_setup_tool,
            name="docker_engine_setup",
        ),
    ]
