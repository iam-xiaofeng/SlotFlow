"""Deterministic cap on parallel sub-agent delegation.

The operating-procedure prompt encourages delegating INDEPENDENT parts to ``task_tool`` in
parallel, but nothing stops the model from firing a dozen at once and exhausting the
sub-agent runner. Mirroring DeerFlow's ``SubagentLimitMiddleware``, this trims the number of
concurrent ``task_tool`` calls on a single model step down to ``max_concurrent`` — a graph-level
guard that does not rely on the model behaving. Non-``task_tool`` calls are left untouched.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState

_TASK_TOOL = "task_tool"


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
        return self._cap(state)

    @override
    async def aafter_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return self._cap(state)

    def _cap(self, state: SlotFlowAgentState) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        message = messages[-1]
        if not isinstance(message, AIMessage):
            return None
        tool_calls = list(message.tool_calls or [])
        if not tool_calls:
            return None

        task_calls = [tc for tc in tool_calls if tc.get("name") == _TASK_TOOL]
        if len(task_calls) <= self._max_concurrent:
            return None

        kept_task_ids = {tc.get("id") for tc in task_calls[: self._max_concurrent]}
        new_tool_calls = [
            tc
            for tc in tool_calls
            if tc.get("name") != _TASK_TOOL or tc.get("id") in kept_task_ids
        ]
        kept_ids = {tc.get("id") for tc in new_tool_calls}

        # Keep the OpenAI-format raw tool_calls (additional_kwargs) in sync so the trimmed
        # set is what actually round-trips to the provider; preserve everything else
        # (notably reasoning_content, required for DeepSeek thinking-mode history).
        additional_kwargs = dict(message.additional_kwargs or {})
        raw_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_calls, list):
            additional_kwargs["tool_calls"] = [
                raw for raw in raw_calls if raw.get("id") in kept_ids
            ]

        trimmed = message.model_copy(
            update={"tool_calls": new_tool_calls, "additional_kwargs": additional_kwargs}
        )
        return {"messages": [trimmed]}
