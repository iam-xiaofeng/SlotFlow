"""Discover artifacts written during a run and expose them in SlotFlow state.

Thin delegate to ``app.harness.steps.artifact_discovery``; keeps the per-run baseline snapshot
on the instance so ``before_agent``/``after_agent`` pair up.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.steps.artifact_discovery import (
    artifact_baseline,
    artifact_finalize_update,
)


class SlotFlowArtifactDiscoveryMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Record the current and newly-created artifact files in `slotflow.artifacts`."""

    def __init__(self, sandbox_config: SlotFlowSandboxConfig | None = None) -> None:
        super().__init__()
        self._sandbox_config = sandbox_config
        self._baseline_by_run_id: dict[str, set[str]] = {}

    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        self._baseline_by_run_id[run_key(runtime)] = artifact_baseline(self._sandbox_config)
        return None

    def after_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        baseline_paths = self._baseline_by_run_id.pop(run_key(runtime), set())
        return artifact_finalize_update(
            state=state,
            baseline_paths=baseline_paths,
            sandbox_config=self._sandbox_config,
        )


def run_key(runtime: Runtime[RunContext]) -> str:
    context = runtime.context
    return context.run_id if context is not None else "default"
