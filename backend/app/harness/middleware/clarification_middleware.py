"""Human-in-the-loop clarification middleware for SlotFlow harness runs."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState


CLARIFICATION_SOURCE = "slotflow_clarification"
CLARIFICATION_TYPE = "clarification"
_ALLOWED_TYPES = {
    "missing_info",
    "ambiguous_requirement",
    "approach_choice",
    "risk_confirmation",
    "suggestion",
}


class SlotFlowClarificationMiddleware(
    AgentMiddleware[SlotFlowAgentState, RunContext]
):
    """Convert ask_clarification tool calls into a user-facing interruption."""

    name = "SlotFlowClarificationMiddleware"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if _tool_name(request.tool_call) != "ask_clarification":
            return handler(request)
        return _clarification_command(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if _tool_name(request.tool_call) != "ask_clarification":
            return await handler(request)
        return _clarification_command(request)


def _clarification_command(request: ToolCallRequest) -> Command[Any]:
    payload = build_clarification_payload(
        request.tool_call,
        run_context=getattr(request.runtime, "context", None),
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    id=payload["id"],
                    content=json.dumps(payload, ensure_ascii=False),
                    name="ask_clarification",
                    tool_call_id=_tool_call_id(request.tool_call),
                )
            ]
        },
        goto=END,
    )


def build_clarification_payload(
    tool_call: Any,
    *,
    run_context: RunContext | None = None,
) -> dict[str, Any]:
    args = _tool_args(tool_call)
    question = _clean_text(args.get("question")) or "请补充需要确认的信息。"
    context = _clean_text(args.get("context"))
    options = _normalize_options(args.get("options"))
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


def _tool_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


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
