"""SlotFlow harness middleware registry."""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.memory import SlotFlowMemoryStore
from app.harness.middleware.builtins import SlotFlowRuntimeSummaryMiddleware
from app.harness.middleware.clarification_middleware import (
    SlotFlowClarificationMiddleware,
)
from app.harness.middleware.config import SlotFlowMiddlewareConfig
from app.harness.middleware.long_term_memory import SlotFlowLongTermMemoryMiddleware
from app.harness.middleware.skills_preflight_middleware import (
    SlotFlowSkillsPreflightMiddleware,
)
from app.harness.middleware.todo_middleware import SlotFlowTodoMiddleware
from app.harness.middleware.tool_safety import SlotFlowToolSafetyMiddleware
from app.harness.middleware.uploads_middleware import SlotFlowUploadsMiddleware
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState


SlotFlowAgentMiddleware = AgentMiddleware[SlotFlowAgentState, RunContext]


def build_harness_middleware(
    *,
    features: SlotFlowHarnessFeatures,
    run_context: RunContext | None = None,
    config: SlotFlowMiddlewareConfig | None = None,
    memory_store: SlotFlowMemoryStore | None = None,
    sandbox_config: SlotFlowSandboxConfig | None = None,
    extra_middleware: list[SlotFlowAgentMiddleware] | None = None,
    tools_enabled: bool = True,
) -> list[SlotFlowAgentMiddleware]:
    """Assemble middleware for the current graph."""

    resolved = config or SlotFlowMiddlewareConfig()
    middleware: list[SlotFlowAgentMiddleware] = list(extra_middleware or [])

    if resolved.tool_safety_enabled:
        middleware.append(SlotFlowToolSafetyMiddleware())

    if resolved.long_term_memory_enabled and memory_store is not None:
        middleware.append(
            SlotFlowLongTermMemoryMiddleware(
                memory_store=memory_store,
                run_context=run_context,
                tools_enabled=tools_enabled,
            )
        )

    if resolved.skills_preflight_enabled:
        middleware.append(
            SlotFlowSkillsPreflightMiddleware(sandbox_config=sandbox_config)
        )

    if resolved.uploads_enabled:
        middleware.append(SlotFlowUploadsMiddleware())

    if tools_enabled and resolved.clarification_enabled:
        middleware.append(SlotFlowClarificationMiddleware())

    if tools_enabled and resolved.todo_enabled and features.plan_enabled:
        middleware.append(SlotFlowTodoMiddleware())

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
