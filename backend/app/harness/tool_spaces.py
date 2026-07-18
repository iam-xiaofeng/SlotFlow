"""Hierarchical, additive tool-space disclosure for model-facing schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

TOOL_SPACE_ORDER = ("workspace", "sandbox", "browser", "network", "documents", "extensions", "memory")

# 默认只对这两个"重/稀有"空间做渐进式披露(loader + 未激活失败关闭):browser 是 MCP 浏览器
# 自动化、extensions 是 skills + 所有 MCP 工具,二者的工具 schema 最臃肿、日常使用频率最低,gate
# 它们能保住主要 token 收益;而 workspace/network/sandbox/documents/memory 这些日常工具默认激活、
# turn-1 即可直接调用,避免"读写文件/联网/生成 artifact 全部 tool_not_activated"的死路。
DEFAULT_GATED_SPACES = frozenset({"browser", "extensions"})


def gated_tool_spaces_from_env() -> frozenset[str]:
    """Read the set of tool spaces that stay behind a loader (progressive disclosure)."""

    import os

    raw = os.environ.get("SLOTFLOW_TOOL_SPACES_GATED")
    if raw is None:
        return DEFAULT_GATED_SPACES
    names = {piece.strip().lower() for piece in raw.split(",") if piece.strip()}
    return frozenset(name for name in names if name in TOOL_SPACE_ORDER)



@dataclass(frozen=True)
class ToolSpaceSetup:
    tools: tuple[BaseTool, ...]
    initial_names: frozenset[str]
    spaces: dict[str, tuple[str, ...]]


def tool_space_for_tool(tool: BaseTool) -> str | None:
    name = tool.name
    if name.startswith(("workspace_", "artifact_")):
        return "workspace"
    if name.startswith(("sandbox_", "docker_")):
        return "sandbox"
    if name.startswith("browser_"):
        return "browser"
    if name.startswith(("web_", "agent_reach_")):
        return "network"
    if name.startswith(("convert_", "markitdown_", "view_image")):
        return "documents"
    if name.startswith(("skill_", "find_skills", "search_skill", "mcp_")):
        return "extensions"
    if name.startswith("memory_"):
        return "memory"
    metadata = tool.metadata if isinstance(tool.metadata, dict) else {}
    if any(key in metadata for key in ("server_name", "mcp_server", "deerflow_mcp")):
        return "extensions"
    return None


def assemble_tool_spaces(
    candidate_tools: list[BaseTool],
    *,
    gated_spaces: frozenset[str] | None = None,
) -> ToolSpaceSetup:
    gated = DEFAULT_GATED_SPACES if gated_spaces is None else gated_spaces
    grouped: dict[str, list[BaseTool]] = {name: [] for name in TOOL_SPACE_ORDER}
    core: list[BaseTool] = []
    for candidate in candidate_tools:
        space = tool_space_for_tool(candidate)
        if space is None or candidate.name.startswith("context_archive_"):
            core.append(candidate)
        else:
            grouped[space].append(candidate)

    # 只对被 gate 的空间建 loader 并做渐进式披露;其余空间的工具直接进入 initial(默认激活)。
    active_space_tools: list[BaseTool] = []
    loaders: list[BaseTool] = []
    gated_catalog: dict[str, tuple[str, ...]] = {}
    for space in TOOL_SPACE_ORDER:
        space_tools = grouped[space]
        if not space_tools:
            continue
        if space in gated:
            loaders.append(_build_space_loader(space, space_tools))
            gated_catalog[space] = tuple(tool.name for tool in space_tools)
        else:
            active_space_tools.extend(space_tools)

    all_tools = [*core, *active_space_tools, *loaders, *candidate_tools]
    unique: dict[str, BaseTool] = {}
    for candidate in all_tools:
        unique.setdefault(candidate.name, candidate)
    initial = frozenset(
        [
            *(tool.name for tool in core),
            *(tool.name for tool in active_space_tools),
            *(tool.name for tool in loaders),
        ]
    )
    return ToolSpaceSetup(
        tools=tuple(unique.values()),
        initial_names=initial,
        spaces=gated_catalog,
    )



def _build_space_loader(space: str, tools: list[BaseTool]) -> BaseTool:
    catalog = {candidate.name: candidate for candidate in tools}
    descriptions = "; ".join(
        f"{candidate.name}: {(candidate.description or '').splitlines()[0][:120]}"
        for candidate in tools
    )

    @tool(
        f"{space}_tools",
        description=(
            f"List or activate exact tools in the {space} tool space. "
            f"Available tools: {descriptions}. Pass exact names; use an empty list to list the catalog."
        ),
    )
    def load_space_tools(
        names: list[str],
        state: Annotated[dict[str, Any], InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        requested = list(dict.fromkeys(name.strip() for name in names if name.strip()))
        unknown = [name for name in requested if name not in catalog]
        current = list(state.get("promoted_tool_names") or [])
        added = [name for name in requested if name in catalog and name not in current]
        promoted = [*current, *added]
        content = {
            "space": space,
            "available": [
                {"name": candidate.name, "description": (candidate.description or "")[:240]}
                for candidate in tools
            ],
            "added": added,
            "already_active": [name for name in requested if name in current],
            "unknown": unknown,
            "usage": "Activated tools become callable on the next model request; activation is additive within this context epoch.",
        }
        return Command(update={
            "promoted_tool_names": promoted,
            "messages": [ToolMessage(content=json.dumps(content, ensure_ascii=False), tool_call_id=tool_call_id, name=f"{space}_tools")],
        })

    return load_space_tools
