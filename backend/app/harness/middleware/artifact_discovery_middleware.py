"""Discover artifacts written during a run and expose them in SlotFlow state."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.state import SlotFlowAgentState
from app.harness.tools.workspace import list_workspace_tree


SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE = "slotflow_artifact_discovery"


class SlotFlowArtifactDiscoveryMiddleware(
    AgentMiddleware[SlotFlowAgentState, RunContext]
):
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
        artifacts = list_artifact_entries(self._sandbox_config)
        self._baseline_by_run_id[run_key(runtime)] = {
            entry["path"] for entry in artifacts if entry["kind"] == "file"
        }
        return None

    def after_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        slotflow = dict(state.get("slotflow") or {})
        baseline_paths = self._baseline_by_run_id.pop(run_key(runtime), set())
        entries = list_artifact_entries(self._sandbox_config)
        new_entries = [
            entry
            for entry in entries
            if entry["kind"] == "file" and entry["path"] not in baseline_paths
        ]
        slotflow["artifacts"] = {
            "path": "artifacts",
            "entries": entries,
            "new_entries": new_entries,
            "source": SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE,
        }
        return {"slotflow": slotflow}


def list_artifact_entries(
    sandbox_config: SlotFlowSandboxConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a recursive, UI-friendly artifact listing."""

    workspace = build_slotflow_workspace(sandbox_config)
    artifact_root = workspace.resolve_path("artifacts")
    if not artifact_root.exists():
        return []
    return list_workspace_tree(
        workspace=workspace,
        path="artifacts",
        max_depth=8,
        max_entries=500,
    )


def run_key(runtime: Runtime[RunContext]) -> str:
    """Return a stable key for the current run."""

    context = runtime.context
    return context.run_id if context is not None else "default"
