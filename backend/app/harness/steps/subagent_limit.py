"""Step: deterministic cap on parallel sub-agent delegation.

Extracted from ``SlotFlowSubagentLimitMiddleware.after_model``. Stateless; trims the number of
concurrent ``task_tool`` calls on a single model step down to ``max_concurrent``, preserving
non-``task_tool`` calls and the message's ``reasoning_content``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from app.harness.state import SlotFlowAgentState

_TASK_TOOL = "task_tool"


def cap_subagent_calls(
    *,
    state: SlotFlowAgentState,
    max_concurrent: int,
) -> dict[str, Any] | None:
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
    if len(task_calls) <= max_concurrent:
        return None

    kept_task_ids = {tc.get("id") for tc in task_calls[:max_concurrent]}
    new_tool_calls = [
        tc
        for tc in tool_calls
        if tc.get("name") != _TASK_TOOL or tc.get("id") in kept_task_ids
    ]
    kept_ids = {tc.get("id") for tc in new_tool_calls}

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
