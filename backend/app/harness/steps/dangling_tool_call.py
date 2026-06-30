"""Step: repair dangling tool calls before the next model invocation.

Extracted from ``SlotFlowDanglingToolCallMiddleware`` / ``repair_dangling_tool_calls``. The
pure message-repair function already exists and is widely imported; this module re-exports it
from the canonical location and keeps the middleware as a thin wrapper.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from app.harness.steps.tool_safety import (
    build_error_tool_message,
    tool_call_id,
    tool_call_name,
)

SLOTFLOW_DANGLING_TOOL_CALL_SOURCE = "slotflow_dangling_tool_call"


@dataclass(frozen=True, slots=True)
class PendingToolCall:
    call_id: str
    name: str


def repair_model_request(request: ModelRequest) -> ModelRequest:
    repaired_messages = repair_dangling_tool_calls(request.messages)
    if repaired_messages == request.messages:
        return request
    return request.override(messages=repaired_messages)


def repair_dangling_tool_calls(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    repaired: list[AnyMessage] = []
    pending: dict[str, PendingToolCall] = {}

    def flush_pending() -> None:
        for pending_call in pending.values():
            repaired.append(
                build_error_tool_message(
                    {
                        "id": pending_call.call_id,
                        "name": pending_call.name,
                        "args": {},
                    },
                    error_type="dangling_tool_call",
                    message="model tool call had no matching ToolMessage before the next model call",
                    source=SLOTFLOW_DANGLING_TOOL_CALL_SOURCE,
                )
            )
        pending.clear()

    for message in messages:
        if pending and not isinstance(message, ToolMessage):
            flush_pending()
        repaired.append(message)
        if isinstance(message, AIMessage):
            for pending_call in pending_tool_calls_from_ai_message(message):
                pending[pending_call.call_id] = pending_call
        elif isinstance(message, ToolMessage):
            pending.pop(str(message.tool_call_id), None)
    if pending:
        flush_pending()
    return repaired


def pending_tool_calls_from_ai_message(message: AIMessage) -> list[PendingToolCall]:
    pending: list[PendingToolCall] = []
    seen: set[str] = set()
    for tool_call in iter_ai_tool_call_candidates(message):
        call_id = tool_call_id(tool_call)
        if call_id is None or call_id in seen:
            continue
        pending.append(PendingToolCall(call_id=call_id, name=tool_call_name(tool_call)))
        seen.add(call_id)
    return pending


def iter_ai_tool_call_candidates(message: AIMessage) -> Iterable[Any]:
    yield from message.tool_calls
    yield from getattr(message, "invalid_tool_calls", []) or []
    raw_tool_calls = message.additional_kwargs.get("tool_calls", [])
    if isinstance(raw_tool_calls, list):
        yield from raw_tool_calls
