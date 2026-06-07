"""Tool-call safety middleware for SlotFlow harness."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.types import Command

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState


SLOTFLOW_TOOL_ERROR_SOURCE = "slotflow_tool_safety"


@dataclass(frozen=True, slots=True)
class PendingToolCall:
    """A model tool call that still needs a matching ToolMessage."""

    call_id: str
    name: str


class SlotFlowToolSafetyMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Turn unsafe tool-call failures into model-readable error ToolMessages."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if request.tool is None:
            return build_error_tool_message(
                request.tool_call,
                error_type="unknown_tool",
                message=f"tool is not registered: {tool_call_name(request.tool_call)!r}",
            )

        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001 - tool exceptions must become ToolMessage errors
            return build_error_tool_message(
                request.tool_call,
                error_type="tool_execution_error",
                message=str(exc),
                exception_type=exc.__class__.__name__,
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if request.tool is None:
            return build_error_tool_message(
                request.tool_call,
                error_type="unknown_tool",
                message=f"tool is not registered: {tool_call_name(request.tool_call)!r}",
            )

        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001 - tool exceptions must become ToolMessage errors
            return build_error_tool_message(
                request.tool_call,
                error_type="tool_execution_error",
                message=str(exc),
                exception_type=exc.__class__.__name__,
            )

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
    """Insert synthetic error ToolMessages before unresolved tool calls reach a model."""

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
            pending.pop(message.tool_call_id, None)

    if pending:
        flush_pending()

    return repaired


def pending_tool_calls_from_ai_message(message: AIMessage) -> list[PendingToolCall]:
    """Extract tool calls with usable IDs from an AIMessage."""

    pending: list[PendingToolCall] = []
    for tool_call in message.tool_calls:
        call_id = tool_call_id(tool_call)
        if call_id is None:
            continue
        pending.append(
            PendingToolCall(
                call_id=call_id,
                name=tool_call_name(tool_call),
            )
        )
    return pending


def build_error_tool_message(
    tool_call: Any,
    *,
    error_type: str,
    message: str,
    exception_type: str | None = None,
) -> ToolMessage:
    """Build a consistent error ToolMessage for a failed tool call."""

    call_id = tool_call_id(tool_call) or "missing_tool_call_id"
    name = tool_call_name(tool_call)
    payload: dict[str, Any] = {
        "error": {
            "type": error_type,
            "message": message,
            "tool_name": name,
            "tool_call_id": call_id,
            "source": SLOTFLOW_TOOL_ERROR_SOURCE,
        }
    }
    if exception_type is not None:
        payload["error"]["exception_type"] = exception_type

    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=name,
        tool_call_id=call_id,
        status="error",
    )


def tool_call_id(tool_call: Any) -> str | None:
    """Read a tool call ID from LangChain's dict-like ToolCall shape."""

    if isinstance(tool_call, dict):
        raw_id = tool_call.get("id")
    else:
        raw_id = getattr(tool_call, "id", None)
    if raw_id is None:
        return None
    return str(raw_id)


def tool_call_name(tool_call: Any) -> str:
    """Read a tool name from LangChain's dict-like ToolCall shape."""

    if isinstance(tool_call, dict):
        raw_name = tool_call.get("name")
    else:
        raw_name = getattr(tool_call, "name", None)
    return str(raw_name or "unknown_tool")
