"""SlotFlow 第一批安全内置工具。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

from app.harness.clarification import (
    build_clarification_payload,
    clarification_answer_text,
)


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

    # HITL via LangGraph native interrupt(): pause the graph and surface the clarification
    # payload to the UI; the user's answer is fed back with Command(resume=<answer>) and IS
    # this tool's result. No "rewrite the answered tool message" step is needed because the
    # resume value lands here directly. See app/harness/clarification.py and HARNESS_NOTES.md.
    payload = build_clarification_payload(
        {
            "name": "ask_clarification",
            "args": {
                "question": question,
                "clarification_type": clarification_type,
                "context": context,
                "options": options,
            },
        }
    )
    answer = interrupt(payload)
    return f"用户对该澄清问题的回答是：{clarification_answer_text(answer)}"
