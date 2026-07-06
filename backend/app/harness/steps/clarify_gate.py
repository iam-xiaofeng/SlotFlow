"""Step: deterministic clarify gate for pro/ultra runs (first model step).

triage + interrupt + answer-injection 纯函数，由 graph 的 ``triage_gate`` 节点直接调用。
HITL 暂停用 LangGraph 原生 ``interrupt()``；恢复经 ``Command(resume=...)``。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.types import interrupt

from app.chat.models import RunContext
from app.harness.clarification import (
    build_clarification_payload,
    clarification_answer_text,
)
from app.harness.utils import (
    latest_user_message_text,
    message_content_text,
    message_role,
)

TriageFn = Callable[[str], dict[str, Any] | None]

_CLARIFY_MODES = {"pro", "ultra"}
_DIRECT_REQUEST_MARKERS = (
    "不要澄清",
    "不要问",
    "不用问",
    "无需确认",
    "不需要确认",
    "直接做",
    "直接回答",
    "直接开始",
    "按当前信息",
    "no clarification",
    "don't ask",
    "do not ask",
    "just do",
    "go ahead",
)
_LONG_REQUEST_FAST_PATH_CHARS = 120
_SHORT_UNDERSPECIFIED_TASK_MAX_CHARS = 48
_SHORT_UNDERSPECIFIED_TASK_MARKERS = (
    "做个",
    "做一个",
    "搞个",
    "弄个",
    "生成",
    "创建",
    "设计",
    "写一",
    "写个",
    "写封",
    "整理成",
    "输出",
    "导出",
    "表格",
    "计划",
    "方案",
    "报告",
    "页面",
    "网页",
    "应用",
    "图片",
    "make ",
    "create ",
    "generate ",
    "write ",
    "design ",
    "build ",
)

_TRIAGE_SYSTEM = (
    "You are a routing classifier for an AI agent — NOT the agent. Given the user's latest "
    "request, decide whether the agent can proceed or must first ask ONE clarifying question. "
    "A request is NOT actionable when a blocking ambiguity, a missing required input, or an "
    "unstated key preference would force the agent to GUESS a materially different result. "
    "Prefer actionable=true whenever a reasonable default exists — do NOT over-ask. When you DO "
    "ask, the `options` MUST be your 2-4 BEST-GUESS concrete directions the user can one-click "
    "(the UI also gives the user a free-text box for 'none of these'). "
    "Respond with ONLY a compact JSON object, no prose, no markdown fences:\n"
    '{"actionable": bool, "clarification_type": '
    '"missing_info|ambiguous_requirement|approach_choice|risk_confirmation|suggestion", '
    '"question": "the single most blocking question", "options": ["2-4 best-guess directions"]}'
)


def clarify_mode_enabled(mode: str | None) -> bool:
    return mode in _CLARIFY_MODES


def is_fresh_user_turn(messages: list[Any]) -> bool:
    if not messages:
        return False
    return message_role(messages[-1]) in ("user", "human")


def already_clarified(messages: list[Any]) -> bool:
    for message in messages:
        if isinstance(message, ToolMessage) and getattr(message, "name", None) == "ask_clarification":
            return True
        if isinstance(message, dict) and message.get("name") == "ask_clarification":
            return True
    return False


def should_skip_triage_model_call(user_text: str) -> bool:
    """Deterministically skip the pre-token triage LLM call for most normal requests."""

    normalized = " ".join(user_text.split()).lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in _DIRECT_REQUEST_MARKERS):
        return True
    if len(normalized) >= _LONG_REQUEST_FAST_PATH_CHARS:
        return True
    return not _looks_like_short_underspecified_task(normalized)


def _looks_like_short_underspecified_task(normalized_text: str) -> bool:
    """Return true only for short creation/output requests likely to need one choice."""

    if len(normalized_text) > _SHORT_UNDERSPECIFIED_TASK_MAX_CHARS:
        return False
    return any(marker in normalized_text for marker in _SHORT_UNDERSPECIFIED_TASK_MARKERS)


async def run_triage(
    *,
    messages: list[Any],
    model: Any = None,
    triage_fn: TriageFn | None = None,
) -> dict[str, Any] | None:
    user_text = latest_user_message_text(messages)
    if not user_text:
        return None
    if triage_fn is not None:
        return triage_fn(user_text)
    if model is None or not hasattr(model, "ainvoke"):
        return None
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=_TRIAGE_SYSTEM),
                HumanMessage(content=user_text[:4000]),
            ],
            config={"callbacks": []},
        )
    except Exception:  # noqa: BLE001 - triage failure must fail open
        return None
    return parse_triage(message_content_text(getattr(response, "content", "")))


def clarify_via_interrupt(
    triage: dict[str, Any],
    ctx: RunContext | None,
) -> dict[str, Any]:
    """Pause the graph to ask the user, then inject their answer as a HumanMessage.

    ``interrupt()`` raises on first run; on resume it returns the user's answer, injected
    verbatim (no meta-frame so the model will not echo a wrapper).
    """

    question = _clean(triage.get("question")) or "请补充需要确认的信息,我才能继续。"
    clarification_type = _clean(triage.get("clarification_type")) or "missing_info"
    options = [opt for opt in (_clean(item) for item in _as_list(triage.get("options"))) if opt][:4]

    seed = f"{getattr(ctx, 'run_id', '')}:{question}"
    call_id = f"clarifygate-{sha256(seed.encode('utf-8')).hexdigest()[:12]}"
    tool_call = {
        "name": "ask_clarification",
        "args": {"question": question, "clarification_type": clarification_type, "options": options},
        "id": call_id,
        "type": "tool_call",
    }
    payload = build_clarification_payload(tool_call, run_context=ctx)
    answer = interrupt(payload)
    return {"messages": [HumanMessage(content=clarification_answer_text(answer))]}


def parse_triage(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return _clean(value.get("label") or value.get("text") or value.get("value"))
    return " ".join(str(value).split())


__all__ = [
    "clarify_mode_enabled",
    "is_fresh_user_turn",
    "already_clarified",
    "run_triage",
    "clarify_via_interrupt",
    "parse_triage",
    "should_skip_triage_model_call",
]
