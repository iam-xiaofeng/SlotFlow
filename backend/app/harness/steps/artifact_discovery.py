"""Step: discover artifacts written during a run and expose them in state.

Extracted from ``SlotFlowArtifactDiscoveryMiddleware`` (before_agent baseline + after_agent
new-entries). The baseline snapshot is kept per-run on the returned object so the graph
``prepare``/``finalize`` nodes can pair them; the thin middleware keeps the same instance
state for backward compatibility.

⚠️ 扫描范围必须限定在**当前对话**的产物目录。以前扫的是全部对话共用的 ``artifacts/``,
两个对话并发跑时,B 新写的文件会被算进 A 的 ``new_entries``(前端就会弹错产物);
顺带扫描量也从全库降到单对话。
"""

from __future__ import annotations

from typing import Any

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.sandbox.layout import LEGACY_ARTIFACTS_DIR, thread_artifacts_dir
from app.harness.state import SlotFlowAgentState
from app.harness.tools.workspace import list_workspace_tree

SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE = "slotflow_artifact_discovery"


def list_artifact_entries(
    sandbox_config: SlotFlowSandboxConfig | None = None,
    *,
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a recursive, UI-friendly artifact listing for one conversation."""

    workspace = build_slotflow_workspace(sandbox_config)
    roots = [thread_artifacts_dir(thread_id)]
    # 迁移期:同一对话在旧布局下的产物也要一起报,否则老对话继续跑会"看不到自己的文件"。
    legacy_root = f"{LEGACY_ARTIFACTS_DIR}/{thread_id}" if thread_id else LEGACY_ARTIFACTS_DIR
    roots.append(legacy_root)

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not workspace.resolve_path(root).exists():
            continue
        for entry in list_workspace_tree(
            workspace=workspace,
            path=root,
            max_depth=8,
            max_entries=500,
        ):
            path = entry["path"]
            if path in seen:
                continue
            seen.add(path)
            entries.append(entry)
    return entries


def artifact_baseline(
    sandbox_config: SlotFlowSandboxConfig | None = None,
    *,
    thread_id: str | None = None,
) -> set[str]:
    """Paths of existing artifact files before the run starts."""

    return {
        entry["path"]
        for entry in list_artifact_entries(sandbox_config, thread_id=thread_id)
        if entry["kind"] == "file"
    }


def artifact_finalize_update(
    *,
    state: SlotFlowAgentState,
    baseline_paths: set[str],
    sandbox_config: SlotFlowSandboxConfig | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Record current and newly-created artifacts into ``state.slotflow.artifacts``."""

    slotflow = dict(state.get("slotflow") or {})
    entries = list_artifact_entries(sandbox_config, thread_id=thread_id)
    new_entries = [
        entry
        for entry in entries
        if entry["kind"] == "file" and entry["path"] not in baseline_paths
    ]
    slotflow["artifacts"] = {
        "path": thread_artifacts_dir(thread_id),
        "entries": entries,
        "new_entries": new_entries,
        "source": SLOTFLOW_ARTIFACT_DISCOVERY_SOURCE,
    }
    return {"slotflow": slotflow}
