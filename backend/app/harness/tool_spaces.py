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


def assemble_tool_spaces(candidate_tools: list[BaseTool]) -> ToolSpaceSetup:
    grouped: dict[str, list[BaseTool]] = {name: [] for name in TOOL_SPACE_ORDER}
    core: list[BaseTool] = []
    for candidate in candidate_tools:
        space = tool_space_for_tool(candidate)
        if space is None or candidate.name.startswith("context_archive_"):
            core.append(candidate)
        else:
            grouped[space].append(candidate)

    loaders = [
        _build_space_loader(space, tools)
        for space, tools in grouped.items()
        if tools
    ]
    all_tools = [*core, *loaders, *candidate_tools]
    unique: dict[str, BaseTool] = {}
    for candidate in all_tools:
        unique.setdefault(candidate.name, candidate)
    initial = frozenset([*(tool.name for tool in core), *(tool.name for tool in loaders)])
    return ToolSpaceSetup(
        tools=tuple(unique.values()),
        initial_names=initial,
        spaces={space: tuple(tool.name for tool in tools) for space, tools in grouped.items() if tools},
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
