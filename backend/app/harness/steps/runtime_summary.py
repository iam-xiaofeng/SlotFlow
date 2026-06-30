"""Step: write a compact read-only SlotFlow runtime summary into graph state.

Extracted from ``SlotFlowRuntimeSummaryMiddleware.before_agent``. Stateless so it can be
called from a graph node (``prepare``) or a thin middleware delegate.
"""

from __future__ import annotations

from typing import Any

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.state import SlotFlowAgentState


def runtime_summary_update(
    *,
    state: SlotFlowAgentState,
    context: RunContext,
    features: SlotFlowHarnessFeatures,
) -> dict[str, Any] | None:
    """Write a compact runtime summary into ``state.slotflow.runtime``."""

    if context is None:
        return None
    existing = dict(state.get("slotflow") or {})
    existing["runtime"] = {
        "thread_id": context.thread_id,
        "run_id": context.run_id,
        "model_name": context.model_name,
        "mode": context.mode,
        "agent_name": context.agent_name,
        "thinking_enabled": features.thinking_enabled,
        "plan_enabled": features.plan_enabled,
        "subagent_enabled": features.subagent_enabled,
        "files_count": len(context.files),
        "uploaded_files": [
            uploaded_file.model_dump(mode="json")
            for uploaded_file in context.uploaded_files
        ],
    }
    return {"slotflow": existing}
