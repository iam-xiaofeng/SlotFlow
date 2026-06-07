"""SlotFlow harness middleware registry."""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.middleware.builtins import SlotFlowRuntimeSummaryMiddleware
from app.harness.middleware.config import SlotFlowMiddlewareConfig
from app.harness.middleware.tool_safety import SlotFlowToolSafetyMiddleware
from app.harness.state import SlotFlowAgentState


SlotFlowAgentMiddleware = AgentMiddleware[SlotFlowAgentState, RunContext]


def build_harness_middleware(
    *,
    features: SlotFlowHarnessFeatures,
    config: SlotFlowMiddlewareConfig | None = None,
    extra_middleware: list[SlotFlowAgentMiddleware] | None = None,
) -> list[SlotFlowAgentMiddleware]:
    """Assemble middleware for the current graph."""

    resolved = config or SlotFlowMiddlewareConfig()
    middleware: list[SlotFlowAgentMiddleware] = list(extra_middleware or [])

    if resolved.tool_safety_enabled:
        middleware.append(SlotFlowToolSafetyMiddleware())

    if resolved.runtime_summary_enabled:
        middleware.append(SlotFlowRuntimeSummaryMiddleware(features=features))

    return dedupe_middleware_by_name(middleware)


def dedupe_middleware_by_name(
    middleware: list[SlotFlowAgentMiddleware],
) -> list[SlotFlowAgentMiddleware]:
    """Deduplicate by middleware.name, preserving the first instance."""

    seen_names: set[str] = set()
    unique: list[SlotFlowAgentMiddleware] = []
    for item in middleware:
        if item.name in seen_names:
            continue
        unique.append(item)
        seen_names.add(item.name)
    return unique
