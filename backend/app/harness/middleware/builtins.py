"""First SlotFlow-owned LangChain agent middleware (now a thin delegate to steps)."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.state import SlotFlowAgentState
from app.harness.steps.runtime_summary import runtime_summary_update


class SlotFlowRuntimeSummaryMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Write a compact, read-only SlotFlow runtime summary into graph state."""

    def __init__(self, *, features: SlotFlowHarnessFeatures) -> None:
        self._features = features

    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None:
            return None
        return runtime_summary_update(state=state, context=context, features=self._features)
