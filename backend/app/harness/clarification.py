"""Shared clarification payload construction for SlotFlow harness HITL.

澄清现在**只有一条路**:模型自己调 ``ask_clarification`` 工具(``harness/tools/builtins.py``)。
2026-08-14 删掉了 ``triage_gate`` 强制门——它在每个新用户轮额外跑一次模型去判断"这个请求够不够
清楚",既是首字延迟,也常常在请求本来就明确时打断用户;模型自己判断该不该问已经够用。

Mechanism note: clarification is delivered through LangGraph native ``interrupt()``/resume.
The tool calls ``interrupt(build_clarification_payload(...))`` to pause the graph; the
user's answer is fed back via ``Command(resume=<answer>)`` and becomes the tool result
directly — so there is no separate "rewrite the answered tool message" step. See HARNESS_NOTES.md.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.chat.models import RunContext


CLARIFICATION_SOURCE = "slotflow_clarification"
CLARIFICATION_TYPE = "clarification"
_ALLOWED_TYPES = {
    "missing_info",
    "ambiguous_requirement",
    "approach_choice",
    "risk_confirmation",
    "suggestion",
}


def build_clarification_payload(
    tool_call: Any,
    *,
    run_context: RunContext | None = None,
) -> dict[str, Any]:
    """Build the structured clarification payload surfaced to the UI picker."""

    args = _tool_args(tool_call)
    question = _clean_text(args.get("question")) or "请补充需要确认的信息。"
    context = _clean_text(args.get("context"))
    options = _append_freeform_option(_normalize_options(args.get("options")))
    clarification_type = _clean_text(args.get("clarification_type")) or "missing_info"
    if clarification_type not in _ALLOWED_TYPES:
        clarification_type = "missing_info"

    base_payload: dict[str, Any] = {
        "type": CLARIFICATION_TYPE,
        "id": "",
        "question": question,
        "clarification_type": clarification_type,
        "context": context,
        "options": options,
        "source": CLARIFICATION_SOURCE,
    }
    if run_context is not None:
        base_payload["thread_id"] = run_context.thread_id
        base_payload["run_id"] = run_context.run_id

    call_id = _tool_call_id(tool_call)
    base_payload["id"] = (
        f"clarification:{call_id}"
        if call_id != "missing_tool_call_id"
        else f"clarification:{_payload_digest(base_payload)}"
    )
    return base_payload


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        raw_id = tool_call.get("id")
    else:
        raw_id = getattr(tool_call, "id", None)
    return str(raw_id or "missing_tool_call_id")


def _tool_args(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, dict):
        raw_args = tool_call.get("args") or {}
    else:
        raw_args = getattr(tool_call, "args", {}) or {}
    if isinstance(raw_args, str):
        try:
            loaded = json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return raw_args if isinstance(raw_args, dict) else {}


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = [raw]
        raw = loaded
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, list):
        items = raw
    else:
        items = [raw]

    options: list[dict[str, str]] = []
    for item in items[:8]:
        label = _clean_text(item)
        if not label:
            continue
        option_id = chr(ord("A") + len(options))
        options.append({"id": option_id, "label": label})
    return options


_FREEFORM_TERMS = ("其他", "请说明", "other", "specify")


def _append_freeform_option(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """Always offer a free-text escape as the LAST option.

    The user must be able to answer in their own words when none of the guessed options fit;
    the frontend renders any option whose label contains 其他/other/specify as a text input.
    """

    for option in options:
        if any(term in option.get("label", "").lower() for term in _FREEFORM_TERMS):
            return options
    option_id = chr(ord("A") + len(options))
    return [*options, {"id": option_id, "label": "其他（自己输入）"}]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        label = value.get("label") or value.get("text") or value.get("value")
        return _clean_text(label)
    return " ".join(str(value).split())


def _payload_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def clarification_answer_text(answer: Any) -> str:
    """Normalize a resume value into the user's answer text for the tool result."""

    if isinstance(answer, str):
        return answer.strip()
    if isinstance(answer, dict):
        for key in ("label", "text", "value", "answer"):
            value = answer.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(answer, ensure_ascii=False)
    return str(answer)
