"""Discover artifacts written during a run and expose them in SlotFlow state."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.state import SlotFlowAgentState


SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE = "slotflow_artifact_discovery"


class SlotFlowArtifactDiscoveryMiddleware(
    AgentMiddleware[SlotFlowAgentState, RunContext]
):
    """Record the current and newly-created artifact files in `slotflow.artifacts`."""

    def __init__(self, sandbox_config: SlotFlowSandboxConfig | None = None) -> None:
        super().__init__()
        self._sandbox_config = sandbox_config

    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        _ = runtime
        slotflow = dict(state.get("slotflow") or {})
        artifacts = list_artifact_entries(self._sandbox_config)
        slotflow["artifact_discovery"] = {
            "baseline_paths": [entry["path"] for entry in artifacts if entry["kind"] == "file"],
            "source": SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE,
        }
        return {"slotflow": slotflow}

    def after_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        _ = runtime
        slotflow = dict(state.get("slotflow") or {})
        discovery = slotflow.get("artifact_discovery")
        baseline_paths = set()
        if isinstance(discovery, dict) and isinstance(discovery.get("baseline_paths"), list):
            baseline_paths = {str(path) for path in discovery["baseline_paths"]}

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

    entries: list[dict[str, Any]] = []
    for child in sorted(artifact_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = child.relative_to(workspace.root).as_posix()
        if child.is_dir():
            entries.append({"path": relative, "kind": "directory", "size_bytes": None})
        elif child.is_file():
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size_bytes": child.stat().st_size,
                }
            )
    return entries
