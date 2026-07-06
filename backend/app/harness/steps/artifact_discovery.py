"""Step: discover artifacts written during a run and expose them in state.

Extracted from ``SlotFlowArtifactDiscoveryMiddleware`` (before_agent baseline + after_agent
new-entries). The baseline snapshot is kept per-run on the returned object so the graph
``prepare``/``finalize`` nodes can pair them; the thin middleware keeps the same instance
state for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.state import SlotFlowAgentState
from app.harness.tools.workspace import list_workspace_tree

SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE = "slotflow_artifact_discovery"


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


def artifact_baseline(sandbox_config: SlotFlowSandboxConfig | None = None) -> set[str]:
    """Paths of existing artifact files before the run starts."""

    return {
        entry["path"]
        for entry in list_artifact_entries(sandbox_config)
        if entry["kind"] == "file"
    }


def artifact_finalize_update(
    *,
    state: SlotFlowAgentState,
    baseline_paths: set[str],
    sandbox_config: SlotFlowSandboxConfig | None = None,
) -> dict[str, Any]:
    """Record current and newly-created artifacts into ``state.slotflow.artifacts``."""

    slotflow = dict(state.get("slotflow") or {})
    entries = list_artifact_entries(sandbox_config)
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
