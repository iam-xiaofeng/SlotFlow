"""Host execution tool safety helpers.

SlotFlow allows model-driven code execution only through ``sandbox_exec``. These
helpers centralize the unsafe host tool-name detection so registry filtering and
ToolNode error messages stay consistent.
"""

from __future__ import annotations


UNSAFE_HOST_EXECUTION_TOOL_NAMES = {
    "bash",
    "shell",
    "terminal",
    "exec",
    "execute",
    "run",
    "run_command",
    "execute_command",
    "python",
    "python_repl",
    "python_exec",
    "node",
    "npm",
    "pip",
}
UNSAFE_HOST_EXECUTION_NAME_FRAGMENTS = (
    "bash",
    "shell",
    "terminal",
    "run_command",
    "execute_command",
    "python_repl",
)


def is_unsafe_host_execution_tool_name(name: str) -> bool:
    normalized_name = name.strip().lower()
    if normalized_name == "sandbox_exec":
        return False
    if normalized_name in UNSAFE_HOST_EXECUTION_TOOL_NAMES:
        return True
    normalized = normalized_name.replace("-", "_")
    return any(fragment in normalized for fragment in UNSAFE_HOST_EXECUTION_NAME_FRAGMENTS)


def unsafe_host_execution_tool_message(name: str) -> str:
    return (
        f"Host execution tool {name!r} is blocked. Run shell/bash/python/node/npm/pip "
        "commands, generated scripts, dependency installs, and Skill helper scripts with "
        "sandbox_exec instead. If Docker itself is missing, use docker_engine_setup to "
        "check or install Docker Engine with SlotFlow's fixed host setup flow."
    )
