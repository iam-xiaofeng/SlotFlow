"""Tools for executing code inside a lazy Docker sandbox."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.docker import DockerSandboxError, LazyDockerSandbox


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

    @tool("sandbox_exec")
    def sandbox_exec(command: str, timeout_seconds: int | None = None) -> str:
        """Run code or scripts inside a lazy Docker sandbox.

        Use this for untrusted code, generated scripts, Skill helper scripts, package
        experiments, and commands that should not run on the host. The sandbox starts
        only on first use. Paths inside the container:
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

    return [sandbox_exec]
