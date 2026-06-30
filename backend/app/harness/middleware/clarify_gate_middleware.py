"""Deterministic clarify gate for pro/ultra runs.

Thin delegate to ``app.harness.steps.clarify_gate``. The triage + interrupt + answer-injection
logic lives in the step module so a graph ``triage_gate`` node can reuse it.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState
from app.harness.steps.clarify_gate import (
    TriageFn,
    already_clarified,
    clarify_mode_enabled,
    clarify_via_interrupt,
    is_fresh_user_turn,
    run_triage,
)

TriageFn  # re-exported for tests


class SlotFlowClarifyGateMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Force clarification (pro+ultra) on the first model step of a fresh user turn."""

    name = "SlotFlowClarifyGateMiddleware"

    def __init__(
        self,
        *,
        model: Any = None,
        triage: TriageFn | None = None,
    ) -> None:
        self._model = model
        self._triage_fn = triage

    def before_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return None

    @override
    async def abefore_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        try:
            return await self._gate(state, runtime)
        except GraphBubbleUp:
            raise
        except Exception:  # noqa: BLE001
            return None

    async def _gate(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        ctx = getattr(runtime, "context", None)
        if not clarify_mode_enabled(getattr(ctx, "mode", None)):
            return None
        messages = list(state.get("messages") or [])
        if not is_fresh_user_turn(messages):
            return None
        if already_clarified(messages):
            return None
        triage = await run_triage(
            messages=messages,
            model=self._model,
            triage_fn=self._triage_fn,
        )
        if triage is None:
            return None
        if not triage.get("actionable", True):
            return clarify_via_interrupt(triage, ctx)
        return None
