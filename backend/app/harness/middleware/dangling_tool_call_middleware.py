"""Repair dangling tool calls before the next model invocation.

Thin delegate to ``app.harness.steps.dangling_tool_call``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.types import ModelResponse

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState
from app.harness.steps.dangling_tool_call import (
    repair_dangling_tool_calls,
    repair_model_request,
)


class SlotFlowDanglingToolCallMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
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


__all__ = ["repair_dangling_tool_calls", "SlotFlowDanglingToolCallMiddleware"]
