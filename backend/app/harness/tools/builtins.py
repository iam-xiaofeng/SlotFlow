"""SlotFlow 第一批安全内置工具。"""

from __future__ import annotations

import json

from langchain_core.tools import tool


@tool("slotflow_context")
def slotflow_context_tool(thread_id: str, run_id: str, mode: str) -> str:
    """Return a compact SlotFlow run context summary.

    This tool is intentionally read-only and side-effect free. It exists first to prove
    that the harness can bind tools into the LangGraph agent before we expose tools that
    touch files, networks, MCP servers, or sandbox backends.
    """

    return json.dumps(
        {
            "thread_id": thread_id,
            "run_id": run_id,
            "mode": mode,
            "source": "slotflow_context_tool",
        },
        ensure_ascii=False,
    )
