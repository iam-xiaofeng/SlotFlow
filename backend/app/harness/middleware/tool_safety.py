"""Tool-call safety middleware for SlotFlow harness."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState


SLOTFLOW_TOOL_ERROR_SOURCE = "slotflow_tool_safety"


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


def build_error_tool_message(
    tool_call: Any,
    *,
    error_type: str,
    message: str,
    exception_type: str | None = None,
    source: str = SLOTFLOW_TOOL_ERROR_SOURCE,
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
            "source": source,
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
        function = tool_call.get("function")
        if raw_name is None and isinstance(function, dict):
            raw_name = function.get("name")
    else:
        raw_name = getattr(tool_call, "name", None)
    return str(raw_name or "unknown_tool")
