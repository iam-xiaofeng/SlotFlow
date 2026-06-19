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
    """Ask the user to clarify before continuing, instead of guessing.

    Prefer asking over assuming whenever a request is ambiguous, underspecified, risky,
    or needs a user preference. Examples:
    - ambiguous: "做个表格" — CSV / Excel / HTML / Markdown?
    - missing info: "分析我的数据" but no file was uploaded
    - risky: "删除这些文件" — confirm before acting
    - preference: "给我做个页面" — style / 配色 / 单页还是多页?
    Provide 2-4 concise, actionable options when the choices are clear. Use it only when
    the answer genuinely changes what you do — do not ask when a reasonable default exists.
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
