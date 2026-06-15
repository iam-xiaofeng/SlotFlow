"""Repair dangling tool calls before the next model invocation."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from app.chat.models import RunContext
from app.harness.middleware.tool_safety import (
    build_error_tool_message,
    tool_call_id,
    tool_call_name,
)
from app.harness.state import SlotFlowAgentState


SLOTFLOW_DANGLING_TOOL_CALL_SOURCE = "slotflow_dangling_tool_call"


@dataclass(frozen=True, slots=True)
class PendingToolCall:
    """A model tool call that still needs a matching ToolMessage."""

    call_id: str
    name: str


class SlotFlowDanglingToolCallMiddleware(
    AgentMiddleware[SlotFlowAgentState, RunContext]
):
    """Insert synthetic ToolMessages for unresolved historical tool calls."""

    def wrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(repair_model_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(repair_model_request(request))


def repair_model_request(request: ModelRequest[RunContext]) -> ModelRequest[RunContext]:
    """Return a model request whose messages have no dangling tool calls."""

    repaired_messages = repair_dangling_tool_calls(request.messages)
    if repaired_messages == request.messages:
        return request
    return request.override(messages=repaired_messages)


def repair_dangling_tool_calls(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Insert synthetic error ToolMessages before unresolved calls reach a model."""

    repaired: list[AnyMessage] = []
    pending: OrderedDict[str, PendingToolCall] = OrderedDict()

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
    """Extract tool calls with usable IDs from all AIMessage tool-call shapes."""

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
    """Yield normalized, raw, and invalid tool-call records from an AIMessage."""

    yield from message.tool_calls
    yield from getattr(message, "invalid_tool_calls", []) or []

    raw_tool_calls = message.additional_kwargs.get("tool_calls", [])
    if isinstance(raw_tool_calls, list):
        yield from raw_tool_calls
