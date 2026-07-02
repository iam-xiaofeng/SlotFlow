"""Invariant tests for the harness tool registry.

These pin the *shape* of build_harness_tools — categories present, relative ordering,
dedupe, and the key tools each category must expose — WITHOUT hardcoding the full tool
list. Adding a new tool therefore no longer breaks unrelated assertions across the suite
(the brittleness that motivated this file).
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.features import features_from_run_context
from app.harness.tools.registry import build_harness_tools


def _features():
    bundle = build_run_config(
        thread_id="t", run_id="r", request=ChatStreamRequest(message="hi")
    )
    return features_from_run_context(bundle.context)


def _features_for_mode(mode: str):
    bundle = build_run_config(
        thread_id="t",
        run_id="r",
        request=ChatStreamRequest(message="hi", mode=mode),
    )
    return features_from_run_context(bundle.context)


def _tool_names() -> list[str]:
    return [t.name for t in build_harness_tools(features=_features())]


def test_registry_has_no_duplicate_tool_names() -> None:
    names = _tool_names()
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"


def test_registry_exposes_key_tools_per_category() -> None:
    names = set(_tool_names())
    assert {"ask_clarification"} <= names  # builtin
    assert {"write_todos"} <= names  # explicit todo requests work in every mode
    assert {"workspace_read", "artifact_write"} <= names  # workspace
    assert {"sandbox_exec"} <= names  # code execution sandbox
    assert {"web_fetch", "web_search"} <= names  # network
    assert {  # customization / skill + mcp discovery
        "skill_match",
        "find-skills",
        "skill_install",
        "search_skill_repos",
        "mcp_add_http",
    } <= names
    # The ambiguous direct write tool was removed and must never reappear.
    assert "workspace_write" not in names


def test_registry_exposes_write_todos_even_in_flash_mode() -> None:
    names = {tool.name for tool in build_harness_tools(features=_features_for_mode("flash"))}

    assert "write_todos" in names


def test_registry_orders_workspace_then_network_then_customization() -> None:
    names = _tool_names()
    assert names.index("workspace_read") < names.index("web_fetch")
    assert names.index("workspace_read") < names.index("sandbox_exec")
    assert names.index("sandbox_exec") < names.index("web_fetch")
    assert names.index("web_fetch") < names.index("skill_match")


def test_registry_dedupes_with_first_name_wins() -> None:
    @tool("ask_clarification")
    def replacement() -> str:
        """Replacement builtin to prove first-name-wins dedupe."""

        return "x"

    tools = build_harness_tools(features=_features(), extra_tools=[replacement])
    names = [t.name for t in tools]
    assert tools[0] is replacement
    assert names.count("ask_clarification") == 1
