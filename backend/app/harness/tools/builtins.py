"""SlotFlow 第一批安全内置工具。"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import tool


@tool("ask_clarification")
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """Ask the user to choose or provide missing information before continuing.

    Use this when a request has several plausible meanings, required details are
    missing, the next action is risky, or multiple good approaches need user
    preference. Provide concise model-generated options when choices are clear.
    """

    return json.dumps(
        {
            "question": question,
            "clarification_type": clarification_type,
            "context": context,
            "options": options or [],
            "source": "slotflow_clarification_tool",
        },
        ensure_ascii=False,
    )


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
