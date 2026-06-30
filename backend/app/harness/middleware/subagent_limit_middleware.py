"""Deterministic cap on parallel sub-agent delegation.

Thin delegate to ``app.harness.steps.subagent_limit``.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState
from app.harness.steps.subagent_limit import cap_subagent_calls


class SlotFlowSubagentLimitMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Truncate excess parallel ``task_tool`` calls on a single model step."""

    name = "SlotFlowSubagentLimitMiddleware"

    def __init__(self, *, max_concurrent: int = 3) -> None:
        self._max_concurrent = max(1, max_concurrent)

    @override
    def after_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return cap_subagent_calls(state=state, max_concurrent=self._max_concurrent)

    @override
    async def aafter_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return cap_subagent_calls(state=state, max_concurrent=self._max_concurrent)
