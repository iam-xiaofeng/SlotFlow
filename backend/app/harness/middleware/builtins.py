"""First SlotFlow-owned LangChain agent middleware."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.state import SlotFlowAgentState


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

        existing = dict(state.get("slotflow") or {})
        existing["runtime"] = {
            "thread_id": context.thread_id,
            "run_id": context.run_id,
            "model_name": context.model_name,
            "mode": context.mode,
            "agent_name": context.agent_name,
            "thinking_enabled": self._features.thinking_enabled,
            "plan_enabled": self._features.plan_enabled,
            "subagent_enabled": self._features.subagent_enabled,
            "files_count": len(context.files),
        }
        return {"slotflow": existing}
