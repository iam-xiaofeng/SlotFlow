"""Tool-call safety middleware for SlotFlow harness runs.

Thin delegate to ``app.harness.steps.tool_safety``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState
from app.harness.steps.tool_safety import build_error_tool_message, tool_call_id, tool_call_name

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
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            return build_error_tool_message(
                request.tool_call,
                error_type="tool_execution_error",
                message=str(exc),
                exception_type=exc.__class__.__name__,
            )


__all__ = [
    "build_error_tool_message",
    "tool_call_id",
    "tool_call_name",
    "SLOTFLOW_TOOL_ERROR_SOURCE",
    "SlotFlowToolSafetyMiddleware",
]
