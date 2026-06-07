"""SlotFlow workspace file tools."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace


def build_workspace_tools(
    config: SlotFlowSandboxConfig | None = None,
) -> list[BaseTool]:
    """Build file tools bound to the current SlotFlow workspace config."""

    workspace = build_slotflow_workspace(config)

    @tool("workspace_list")
    def workspace_list(path: str = ".") -> str:
        """List immediate files and directories under a SlotFlow workspace path."""

        entries = workspace.list_entries(path)
        return json.dumps(
            {
                "path": path,
                "entries": [
                    {
                        "path": entry.path,
                        "kind": entry.kind,
                        "size_bytes": entry.size_bytes,
                    }
                    for entry in entries
                ],
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    @tool("workspace_read")
    def workspace_read(path: str) -> str:
        """Read a UTF-8 text file from the SlotFlow workspace."""

        content = workspace.read_text(path)
        return json.dumps(
            {
                "path": path,
                "content": content,
                "size_bytes": len(content.encode("utf-8")),
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    tools: list[BaseTool] = [workspace_list, workspace_read]

    if workspace.config.writes_enabled:

        @tool("workspace_write")
        def workspace_write(path: str, content: str) -> str:
            """Write a UTF-8 text file into the SlotFlow workspace."""

            target = workspace.write_text(path, content)
            return json.dumps(
                {
                    "path": target.relative_to(workspace.root).as_posix(),
                    "bytes_written": len(content.encode("utf-8")),
                    "source": "slotflow_workspace",
                },
                ensure_ascii=False,
            )

        tools.append(workspace_write)

    return tools
