"""SlotFlow workspace file tools."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.sandbox.readers import plain_text_excerpt


def build_workspace_tools(
    config: SlotFlowSandboxConfig | None = None,
) -> list[BaseTool]:
    """Build file tools bound to the current SlotFlow workspace config."""

    workspace = build_slotflow_workspace(config)

    @tool("workspace_list")
    def workspace_list(path: str = ".") -> str:
        """List immediate files and directories under a SlotFlow workspace path."""

        entries = workspace.list_entries(path)
        return json.dumps(
            {
                "path": path,
                "entries": [
                    {
                        "path": entry.path,
                        "kind": entry.kind,
                        "size_bytes": entry.size_bytes,
                    }
                    for entry in entries
                ],
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    @tool("workspace_read")
    def workspace_read(path: str) -> str:
        """Read a text, markdown, docx, PDF, or image file from the workspace."""

        return json.dumps(workspace.read_file(path).model_dump(), ensure_ascii=False)

    @tool("workspace_tree")
    def workspace_tree(path: str = ".", max_depth: int = 3, max_entries: int = 120) -> str:
        """List workspace files recursively with depth and result limits."""

        entries = list_workspace_tree(
            workspace=workspace,
            path=path,
            max_depth=max_depth,
            max_entries=max_entries,
        )
        return json.dumps(
            {
                "path": path,
                "entries": entries,
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    @tool("workspace_search")
    def workspace_search(query: str, path: str = ".", max_results: int = 20) -> str:
        """Search readable workspace files for a literal text query."""

        matches = search_workspace_text(
            workspace=workspace,
            query=query,
            path=path,
            max_results=max_results,
        )
        return json.dumps(
            {
                "query": query,
                "path": path,
                "matches": matches,
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    @tool("artifact_list")
    def artifact_list() -> str:
        """List generated artifacts under workspace/artifacts."""

        try:
            entries = workspace.list_entries("artifacts")
        except Exception:
            entries = []
        return json.dumps(
            {
                "path": "artifacts",
                "entries": [
                    {
                        "path": entry.path,
                        "kind": entry.kind,
                        "size_bytes": entry.size_bytes,
                    }
                    for entry in entries
                ],
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    tools: list[BaseTool] = [
        workspace_list,
        workspace_read,
        workspace_tree,
        workspace_search,
        artifact_list,
    ]

    if workspace.config.writes_enabled:

        @tool("workspace_write")
        def workspace_write(path: str, content: str) -> str:
            """Write UTF-8 draft or intermediate content into the SlotFlow workspace.

            Do not use this for user-visible deliverables. Use artifact_write for
            generated reports, charts, visualizations, demos, previews, or files the
            user should see in the artifact panel.
            """

            target = workspace.write_text(path, content)
            return json.dumps(
                {
                    "path": target.relative_to(workspace.root).as_posix(),
                    "bytes_written": len(content.encode("utf-8")),
                    "source": "slotflow_workspace",
                },
                ensure_ascii=False,
            )

        tools.append(workspace_write)

        @tool("artifact_write")
        def artifact_write(path: str, content: str) -> str:
            """Write a user-visible UTF-8 artifact under workspace/artifacts."""

            artifact_path = normalize_artifact_path(path)
            target = workspace.write_text(artifact_path, content)
            return json.dumps(
                {
                    "path": target.relative_to(workspace.root).as_posix(),
                    "bytes_written": len(content.encode("utf-8")),
                    "source": "slotflow_workspace",
                },
                ensure_ascii=False,
            )

        tools.append(artifact_write)

    return tools


def list_workspace_tree(
    *,
    workspace,
    path: str,
    max_depth: int,
    max_entries: int,
) -> list[dict[str, object]]:
    """Return a bounded recursive workspace listing."""

    root = workspace.resolve_path(path)
    if not root.exists():
        return []
    if root.is_file():
        relative = root.relative_to(workspace.root).as_posix()
        return [{"path": relative, "kind": "file", "size_bytes": root.stat().st_size}]

    safe_max_depth = max(0, min(max_depth, 8))
    safe_max_entries = max(1, min(max_entries, 500))
    entries: list[dict[str, object]] = []

    def visit(directory: Path, depth: int) -> None:
        if len(entries) >= safe_max_entries or depth > safe_max_depth:
            return
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if len(entries) >= safe_max_entries:
                return
            resolved_child = workspace.resolve_path(child.relative_to(workspace.root).as_posix())
            relative = resolved_child.relative_to(workspace.root).as_posix()
            if resolved_child.is_dir():
                entries.append({"path": relative, "kind": "directory", "size_bytes": None})
                visit(resolved_child, depth + 1)
            elif resolved_child.is_file():
                entries.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "size_bytes": resolved_child.stat().st_size,
                    }
                )

    visit(root, 1)
    return entries


def search_workspace_text(
    *,
    workspace,
    query: str,
    path: str,
    max_results: int,
) -> list[dict[str, object]]:
    """Search files that `workspace_read` can expose as text content."""

    stripped_query = query.strip()
    if not stripped_query:
        return []

    root = workspace.resolve_path(path)
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    needle = stripped_query.lower()
    safe_max_results = max(1, min(max_results, 100))
    matches: list[dict[str, object]] = []

    for candidate in candidates:
        if len(matches) >= safe_max_results:
            break
        if not candidate.is_file():
            continue

        relative_path = candidate.relative_to(workspace.root).as_posix()
        try:
            result = workspace.read_file(relative_path)
        except Exception:
            continue
        if result.content is None:
            continue

        content_lower = result.content.lower()
        index = content_lower.find(needle)
        if index < 0:
            continue

        start = max(0, index - 120)
        end = min(len(result.content), index + len(stripped_query) + 120)
        matches.append(
            {
                "path": relative_path,
                "kind": result.kind,
                "media_type": result.media_type,
                "excerpt": plain_text_excerpt(result.content[start:end]),
            }
        )

    return matches


def normalize_artifact_path(path: str) -> str:
    """Keep generated artifacts under the artifacts directory."""

    stripped = path.strip().lstrip("/")
    if not stripped:
        stripped = "artifact.md"
    if stripped == "artifacts" or stripped.startswith("artifacts/"):
        return stripped
    return f"artifacts/{stripped}"
