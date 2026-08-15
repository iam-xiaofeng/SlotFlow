"""SlotFlow workspace file tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.sandbox.layout import (
    ARTIFACTS_DIR_NAME,
    thread_artifacts_dir,
    thread_dir_name,
)
from app.harness.sandbox.readers import plain_text_excerpt

# 单次读取的字符上限，与 `skill_read` 的 `MAX_SKILL_READ_CHARS` 同一套语义。
#
# 2026-08-14 补：这条上限以前根本不存在。`workspace_read` 恰恰是系统提示点名让模型用来读
# 上传文件的工具，而工具结果卸载（`tool_output_offload`）又把它列在跳过名单里（把工作区文件
# 卸载成工作区文件是循环，跳过本身是对的）——于是它成了整个工具集里唯一没有任何上限的读口。
# 真机后果：一个 446KB 的 index.html 被整段内联成 373K 字符的 ToolMessage（≈166k token），
# 之后模型每次都返回空响应，且空响应进了 checkpoint，整个对话被永久毒化。见 HARNESS_NOTES §63。
MAX_WORKSPACE_READ_CHARS = 24_000


def _with_capped_content(
    result: dict[str, Any], *, offset: int, path: str
) -> dict[str, Any]:
    """按字符上限截断读取结果，并告诉模型怎么续读。

    只截断字符串正文。图片这类 `content` 是 base64 的结果一旦截断就彻底损坏，
    但它们走的是 `_assert_readable_file` 的字节上限，不会到这里；这里仍按"非 str 不碰"处理。
    """

    content = result.get("content")
    if not isinstance(content, str):
        return result

    total = len(content)
    start = max(0, offset)
    if start >= total:
        result["content"] = ""
        result["read"] = {
            "truncated": False,
            "total_chars": total,
            "offset": start,
            "note": "offset is past the end of this file",
        }
        return result

    chunk = content[start : start + MAX_WORKSPACE_READ_CHARS]
    end = start + len(chunk)
    result["content"] = chunk
    if end >= total:
        result["read"] = {"truncated": False, "total_chars": total, "offset": start}
        return result

    result["read"] = {
        "truncated": True,
        "total_chars": total,
        "offset": start,
        "next_offset": end,
        "note": (
            f"{total - end} more characters remain. Continue with "
            f"workspace_read('{path}', offset={end}), or use "
            f"workspace_grep('<keyword>', '{path}') to jump to the relevant part."
        ),
    }
    return result


def resolve_thread_scoped_path(path: str, *, root: Path, thread_id: str | None) -> str:
    """把模型给的工作区路径**优先按本对话目录**解释。

    2026-08-15 真机 bug:写和读两侧对同一个字符串的理解不一致。
    `artifact_write("cybervault/index.html")` 会规范化成 `<thread>/artifacts/cybervault/index.html`
    (见 `normalize_artifact_path`),而 `workspace_read("artifacts/cybervault/index.html")`
    直接按 **workspace root** 解析——落到了旧布局遗留的顶层 `artifacts/` 上,报
    "workspace path is not a file"。父代理把 `artifacts/xxx` 写进子代理任务描述时必踩:
    那一轮三个子代理里有两个连报三次读不到,其中一个绕了 30 分钟才以空响应失败。

    模型这么写并不算错——沙箱容器的 cwd 就是 `<thread>/`,`ls` 看到的正是 `artifacts/`、
    `work/`、`uploads/`。所以这里补上这层换算,而不是要求模型记住宿主侧的完整前缀。

    顺序是"先本对话、再原样":反过来的话,顶层遗留的同名旧文件会盖掉本轮刚写的产物。
    两个都不存在时返回本对话候选,好让报错信息指向真正该看的目录。
    """

    base = thread_dir_name(thread_id)
    cleaned = (path or ".").strip().lstrip("/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:].lstrip("/")
    if cleaned in ("", "."):
        # 不带参数的 `workspace_tree()` 以前列的是整个 workspace root,
        # 也就是**别的对话的目录**;有 thread 时应当只看自己这一份。
        return base if (root / base).is_dir() else "."
    if cleaned == base or cleaned.startswith(f"{base}/"):
        return cleaned
    candidate = f"{base}/{cleaned}"
    if (root / candidate).exists():
        return candidate
    if (root / cleaned).exists():
        return cleaned  # 旧布局的顶层 artifacts/ 与 .uploads/ 原件仍照旧可读
    return candidate


def build_workspace_tools(
    config: SlotFlowSandboxConfig | None = None,
    *,
    thread_id: str | None = None,
) -> list[BaseTool]:
    """Build file tools bound to the current SlotFlow workspace config.

    `thread_id` namespaces generated artifacts under `<thread_id>/artifacts/`, so each
    conversation gets one folder holding everything the user can open (uploads staged
    by the run plus files the agent generates). 读侧工具也按它换算路径,详见
    `resolve_thread_scoped_path`。
    """

    workspace = build_slotflow_workspace(config)
    artifact_root = artifact_dir_for_thread(thread_id)

    def scoped(path: str) -> str:
        return resolve_thread_scoped_path(path, root=workspace.root, thread_id=thread_id)

    def workspace_list(path: str = ".") -> str:
        """List immediate files and directories under a SlotFlow workspace path."""

        target = scoped(path)
        entries = workspace.list_entries(target)
        return json.dumps(
            {
                "path": target,
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

    def workspace_read(path: str, offset: int = 0) -> str:
        """Read a text, markdown, docx, PDF, or image file from the workspace.

        Long files are truncated; use `offset` to continue reading, or
        `workspace_grep` to jump straight to the part you need.
        """

        target = scoped(path)
        result = workspace.read_file(target).model_dump()
        return json.dumps(
            _with_capped_content(result, offset=offset, path=target),
            ensure_ascii=False,
        )

    def workspace_tree(path: str = ".", max_depth: int = 3, max_entries: int = 120) -> str:
        """List workspace files recursively with depth and result limits."""

        target = scoped(path)
        entries = list_workspace_tree(
            workspace=workspace,
            path=target,
            max_depth=max_depth,
            max_entries=max_entries,
        )
        return json.dumps(
            {
                "path": target,
                "entries": entries,
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    def workspace_search(query: str, path: str = ".", max_results: int = 20) -> str:
        """Search readable workspace files for a literal text query."""

        target = scoped(path)
        matches = search_workspace_text(
            workspace=workspace,
            query=query,
            path=target,
            max_results=max_results,
        )
        return json.dumps(
            {
                "query": query,
                "path": target,
                "matches": matches,
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    def workspace_grep(pattern: str, path: str = ".", max_results: int = 20) -> str:
        """Grep readable SlotFlow workspace files for a literal pattern without Docker."""

        target = scoped(path)
        matches = search_workspace_text(
            workspace=workspace,
            query=pattern,
            path=target,
            max_results=max_results,
        )
        return json.dumps(
            {
                "pattern": pattern,
                "path": target,
                "matches": matches,
                "source": "slotflow_workspace",
            },
            ensure_ascii=False,
        )

    def artifact_list() -> str:
        """List this conversation's artifacts (uploads + generated files)."""

        try:
            entries = workspace.list_entries(artifact_root)
        except Exception:
            entries = []
        return json.dumps(
            {
                "path": artifact_root,
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
        threaded_structured_tool(workspace_list, name="workspace_list"),
        threaded_structured_tool(workspace_read, name="workspace_read"),
        threaded_structured_tool(workspace_tree, name="workspace_tree"),
        threaded_structured_tool(workspace_search, name="workspace_search"),
        threaded_structured_tool(workspace_grep, name="workspace_grep"),
        threaded_structured_tool(artifact_list, name="artifact_list"),
    ]

    if workspace.config.writes_enabled:

        def artifact_write(path: str, content: str) -> str:
            """Write a user-visible file into this conversation's artifact folder.

            This is the ONLY way to produce files the user can see and open in the
            artifact panel. Use it for every user-facing deliverable: reports, charts,
            HTML/Markdown pages, visualizations, comparison tables, interactive demos,
            and code previews. `path` is just a name like "report.md" or
            "charts/sales.html" — it is automatically placed under this conversation's
            artifact folder, alongside the user's uploaded files.
            """

            artifact_path = normalize_artifact_path(path, thread_id=thread_id)
            target = workspace.write_text(artifact_path, content)
            return json.dumps(
                {
                    "path": target.relative_to(workspace.root).as_posix(),
                    "bytes_written": len(content.encode("utf-8")),
                    "source": "slotflow_workspace",
                },
                ensure_ascii=False,
            )

        tools.append(threaded_structured_tool(artifact_write, name="artifact_write"))

    return tools


def threaded_structured_tool(func: Callable[..., str], *, name: str) -> StructuredTool:
    """Build a tool whose async path runs blocking local file work in a worker thread."""

    async def coroutine(*args: Any, **kwargs: Any) -> str:
        return await asyncio.to_thread(func, *args, **kwargs)

    coroutine.__name__ = f"a{func.__name__}"
    return StructuredTool.from_function(func=func, coroutine=coroutine, name=name)


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
    max_candidates = 1000
    if root.is_file():
        candidates = [root]
    else:
        candidates = sorted(root.rglob("*"))[:max_candidates]
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


def artifact_dir_for_thread(thread_id: str | None) -> str:
    """Return the artifact folder for a conversation (per-thread when known)."""

    return thread_artifacts_dir(thread_id)


def normalize_artifact_path(path: str, thread_id: str | None = None) -> str:
    """Keep generated artifacts under this conversation's artifact directory.

    模型给的 `path` 可能是裸名("report.md"),也可能带上它在沙箱里看到的目录前缀
    ("artifacts/report.md"、"<thread>/artifacts/report.md")。宽容地把前缀剥掉,
    最终一律落在 "<thread>/artifacts/<name>"。
    """

    base = artifact_dir_for_thread(thread_id)
    stripped = path.strip().lstrip("/").strip()
    for _ in range(2):
        for prefix in (f"{thread_dir_name(thread_id)}/", f"{ARTIFACTS_DIR_NAME}/"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].lstrip("/")
    if not stripped or stripped in (ARTIFACTS_DIR_NAME, thread_dir_name(thread_id)):
        return f"{base}/artifact.md"
    return f"{base}/{stripped}"
