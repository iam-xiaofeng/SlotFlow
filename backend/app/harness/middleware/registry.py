"""SlotFlow harness middleware registry."""

from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.memory import SlotFlowMemoryStore
from app.harness.middleware.artifact_discovery_middleware import (
    SlotFlowArtifactDiscoveryMiddleware,
)
from app.harness.middleware.builtins import SlotFlowRuntimeSummaryMiddleware
from app.harness.middleware.clarify_gate_middleware import (
    SlotFlowClarifyGateMiddleware,
)
from app.harness.middleware.config import SlotFlowMiddlewareConfig
from app.harness.middleware.dangling_tool_call_middleware import (
    SlotFlowDanglingToolCallMiddleware,
)
from app.harness.middleware.long_term_memory import SlotFlowLongTermMemoryMiddleware
from app.harness.middleware.skills_preflight_middleware import (
    SlotFlowSkillsPreflightMiddleware,
)
from app.harness.middleware.subagent_limit_middleware import (
    SlotFlowSubagentLimitMiddleware,
)
from app.harness.middleware.summarization_middleware import (
    SlotFlowSummarizationMiddleware,
)
from app.harness.middleware.todo_middleware import SlotFlowTodoMiddleware
from app.harness.middleware.tool_safety import SlotFlowToolSafetyMiddleware
from app.harness.middleware.uploads_middleware import SlotFlowUploadsMiddleware
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.utils import dedupe_by_name


SlotFlowAgentMiddleware = AgentMiddleware[SlotFlowAgentState, RunContext]


def build_harness_middleware(
    *,
    features: SlotFlowHarnessFeatures,
    model: str | BaseChatModel,
    run_context: RunContext | None = None,
    config: SlotFlowMiddlewareConfig | None = None,
    memory_store: SlotFlowMemoryStore | None = None,
    sandbox_config: SlotFlowSandboxConfig | None = None,
    skills_root: Path | None = None,
    skills_config_store: object | None = None,
    extra_middleware: list[SlotFlowAgentMiddleware] | None = None,
    tools_enabled: bool = True,
) -> list[SlotFlowAgentMiddleware]:
    """Assemble middleware for the current graph."""

    resolved = config or SlotFlowMiddlewareConfig()
    middleware: list[SlotFlowAgentMiddleware] = list(extra_middleware or [])

    if resolved.dangling_tool_call_enabled:
        middleware.append(SlotFlowDanglingToolCallMiddleware())

    if resolved.tool_safety_enabled:
        middleware.append(SlotFlowToolSafetyMiddleware())

    if resolved.summarization_enabled:
        middleware.append(
            SlotFlowSummarizationMiddleware(
                model=model,
                trigger_tokens=resolved.summarization_trigger_tokens,
                keep_messages=resolved.summarization_keep_messages,
                trim_tokens_to_summarize=resolved.summarization_trim_tokens,
            )
        )

    if resolved.long_term_memory_enabled and memory_store is not None:
        middleware.append(
            SlotFlowLongTermMemoryMiddleware(
                memory_store=memory_store,
                run_context=run_context,
                tools_enabled=tools_enabled,
                model=model,
                proactive_extraction_enabled=resolved.proactive_memory_extraction_enabled,
            )
        )

    if resolved.skills_preflight_enabled:
        middleware.append(
            SlotFlowSkillsPreflightMiddleware(
                sandbox_config=sandbox_config,
                skills_root=skills_root,
                skills_config_store=skills_config_store,
            )
        )

    if resolved.uploads_enabled:
        middleware.append(SlotFlowUploadsMiddleware(sandbox_config=sandbox_config))

    # Clarify-gate forces clarification (pro+ultra) on the first model step of a fresh user
    # turn. It pauses the graph with LangGraph native interrupt(); the answer resumes the run.
    # The voluntary ``ask_clarification`` tool uses the same interrupt() mechanism, so no
    # separate clarification middleware is needed to surface either path.
    if (
        tools_enabled
        and resolved.clarify_gate_enabled
        and run_context is not None
        and run_context.mode in ("pro", "ultra")
    ):
        middleware.append(SlotFlowClarifyGateMiddleware(model=model))

    if tools_enabled and resolved.todo_enabled and features.plan_enabled:
        middleware.append(SlotFlowTodoMiddleware())

    if tools_enabled and resolved.subagent_limit_enabled and features.subagent_enabled:
        middleware.append(
            SlotFlowSubagentLimitMiddleware(
                max_concurrent=resolved.subagent_max_concurrent,
            )
        )

    if resolved.artifact_discovery_enabled:
        middleware.append(
            SlotFlowArtifactDiscoveryMiddleware(sandbox_config=sandbox_config)
        )

    if resolved.runtime_summary_enabled:
        middleware.append(SlotFlowRuntimeSummaryMiddleware(features=features))

    return dedupe_by_name(middleware)
