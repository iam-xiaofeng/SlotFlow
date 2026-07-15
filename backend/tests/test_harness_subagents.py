"""Module 21 tests: SlotFlow subagent task tools."""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.subagents import (
    SlotFlowSubagentConfig,
    SlotFlowSubagentProfile,
    build_subagent_tools,
    default_role_catalog,
)
from app.harness.tools.registry import build_harness_tools


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """Test fake model that supports the tool-binding boundary."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _bundle(mode: str = "ultra"):
    request = ChatStreamRequest(message="委派子任务", mode=mode)
    return build_run_config(
        thread_id="thread_subagent",
        run_id="run_subagent",
        request=request,
    )


def test_subagent_default_recursion_limit_allows_multi_tool_loops() -> None:
    assert SlotFlowSubagentConfig().recursion_limit == 100


def test_subagent_tools_are_registered_only_when_feature_is_enabled() -> None:
    flash_bundle = _bundle(mode="flash")
    ultra_bundle = _bundle(mode="ultra")
    model = ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")])

    assert build_subagent_tools(
        features=features_from_run_context(flash_bundle.context),
        model=model,
        run_context=flash_bundle.context,
    ) == []
    assert build_subagent_tools(
        features=features_from_run_context(ultra_bundle.context),
    ) == []
    assert [
        tool.name
        for tool in build_subagent_tools(
            features=features_from_run_context(ultra_bundle.context),
            model=model,
            run_context=ultra_bundle.context,
        )
    ] == ["subagent_list", "subagent_role_search", "task_tool"]


@pytest.mark.asyncio
async def test_task_tool_runs_real_subagent_and_returns_result() -> None:
    bundle = _bundle(mode="ultra")
    model = ToolAwareFakeMessagesListChatModel(
        responses=[AIMessage(content="真实子 agent 结果")]
    )
    tools = build_subagent_tools(
        features=features_from_run_context(bundle.context),
        model=model,
        run_context=bundle.context,
    )
    tool = next(item for item in tools if item.name == "task_tool")

    raw = await tool.ainvoke(
        {
            "agent_name": "researcher",
            "task": "整理 MCP provider 的官方用法",
            "context": "模块 20 后续验证",
            "expected_output": "列出官方 API 和风险",
            "priority": "high",
        }
    )
    result = json.loads(raw)

    assert result["status"] == "completed"
    assert result["agent_name"] == "researcher"
    assert result["task"] == "整理 MCP provider 的官方用法"
    assert result["context"] == "模块 20 后续验证"
    assert result["expected_output"] == "列出官方 API 和风险"
    assert result["priority"] == "high"
    assert result["result"] == "真实子 agent 结果"
    assert result["source"] == "slotflow_subagent_task_tool"


@pytest.mark.asyncio
async def test_subagent_list_returns_profile_capabilities() -> None:
    bundle = _bundle(mode="ultra")
    tool = next(
        item
        for item in build_subagent_tools(
            features=features_from_run_context(bundle.context),
            model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
            run_context=bundle.context,
        )
        if item.name == "subagent_list"
    )

    result = json.loads(await tool.ainvoke({}))

    assert result["source"] == "slotflow_subagent_list"
    assert [item["name"] for item in result["subagents"]] == [
        "researcher",
        "analyst",
        "planner",
        "coder",
        "reviewer",
        "writer",
    ]
    assert result["subagents"][0]["capabilities"]
    assert result["subagents"][0]["output_contract"]
    assert [item["slug"] for item in result["role_domains"]] == [
        "engineering",
        "design",
        "finance",
        "market",
        "sales",
        "product",
        "research",
        "specialized",
    ]
    assert result["role_domains"][0]["role_count"] > 0
    for domain in result["role_domains"]:
        assert "prompt" not in domain
        for role in domain["sample_roles"]:
            assert "prompt" not in role


@pytest.mark.asyncio
async def test_subagent_role_search_returns_short_metadata_only() -> None:
    bundle = _bundle(mode="ultra")
    tool = next(
        item
        for item in build_subagent_tools(
            features=features_from_run_context(bundle.context),
            model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
            run_context=bundle.context,
        )
        if item.name == "subagent_role_search"
    )

    result = json.loads(
        await tool.ainvoke(
            {
                "query": "frontend react component",
                "domain": "engineering",
                "max_results": 3,
            }
        )
    )

    assert result["source"] == "slotflow_subagent_role_search"
    assert result["roles"]
    assert len(result["roles"]) <= 3
    assert any(role["id"] == "engineering-frontend-developer" for role in result["roles"])
    for role in result["roles"]:
        assert role["domain"] == "engineering"
        assert "prompt" not in role


def test_default_role_catalog_resolves_one_concrete_agency_role() -> None:
    catalog = default_role_catalog()

    role = catalog.resolve(
        domain="engineering",
        role_name="Frontend Developer",
        task="Build a React component",
    )

    assert role is not None
    assert role.id == "engineering-frontend-developer"
    assert role.domain == "engineering"
    assert role.path == "engineering/engineering-frontend-developer.md"
    assert "Frontend Developer Agent Personality" in role.prompt


def test_role_search_falls_back_to_domain_shortlist_for_non_matching_query() -> None:
    result = default_role_catalog().search(
        query="做一个地图交互界面",
        domain="engineering",
        max_results=4,
    )

    assert result
    assert len(result) <= 4
    assert all(role["domain"] == "engineering" for role in result)
    assert all("prompt" not in role for role in result)


@pytest.mark.asyncio
async def test_task_tool_returns_structured_error_for_unknown_agent() -> None:
    bundle = _bundle(mode="ultra")
    tool = next(
        item
        for item in build_subagent_tools(
        features=features_from_run_context(bundle.context),
        model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        run_context=bundle.context,
        )
        if item.name == "task_tool"
    )

    result = json.loads(
        await tool.ainvoke(
            {
                "agent_name": "missing",
                "task": "do something",
            }
        )
    )

    assert result["status"] == "error"
    assert result["agent_name"] == "missing"
    assert result["result"] == "unknown subagent: missing"


@pytest.mark.asyncio
async def test_task_tool_injects_selected_agency_role(monkeypatch) -> None:
    bundle = _bundle(mode="ultra")
    captured: dict[str, object] = {}

    class _Graph:
        async def ainvoke(self, payload, config=None):
            captured["payload"] = payload
            captured["invoke_config"] = config
            return {"messages": [AIMessage(content="角色化子任务结果")]}

    def fake_build_slotflow_graph(**kwargs):
        captured.update(kwargs)
        return _Graph()

    import app.harness.graph as graph_module

    monkeypatch.setattr(graph_module, "build_slotflow_graph", fake_build_slotflow_graph)
    tool = next(
        item
        for item in build_subagent_tools(
            features=features_from_run_context(bundle.context),
            model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
            run_context=bundle.context,
            config=SlotFlowSubagentConfig(recursion_limit=73),
        )
        if item.name == "task_tool"
    )

    result = json.loads(
        await tool.ainvoke(
            {
                "agent_name": "coder",
                "task": "Build a React component",
                "context": "SlotFlow frontend",
                "expected_output": "Implementation notes",
                "domain": "engineering",
                "role_name": "Frontend Developer",
            }
        )
    )

    system_prompt = str(captured["system_prompt"])
    payload = captured["payload"]
    assert result["status"] == "completed"
    assert result["agent_name"] == "coder"
    assert result["role_id"] == "engineering-frontend-developer"
    assert result["role_path"] == "engineering/engineering-frontend-developer.md"
    assert result["role_name"] == "Frontend Developer"
    assert result["result"] == "角色化子任务结果"
    assert "<slotflow-agency-role>" in system_prompt
    assert "name=Frontend Developer" in system_prompt
    assert "Frontend Developer Agent Personality" in system_prompt
    assert "Financial Analyst Agent" not in system_prompt
    assert isinstance(payload, dict)
    assert captured["invoke_config"] == {"recursion_limit": 73}
    assert "Selected role: Frontend Developer" in payload["messages"][0]["content"]


def test_build_harness_tools_adds_task_tool_between_workspace_and_mcp_boundary() -> None:
    bundle = _bundle(mode="ultra")
    tools = build_harness_tools(
        features=features_from_run_context(bundle.context),
        model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        run_context=bundle.context,
    )

    names = [tool.name for tool in tools]
    # Subagent tools sit after the workspace/customization tools (the task-tool boundary).
    assert {"subagent_list", "subagent_role_search", "task_tool"} <= set(names)
    assert names.index("subagent_role_search") < names.index("task_tool")
    assert names.index("task_tool") > names.index("artifact_write")
    assert names.index("task_tool") > names.index("skill_match")
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"


def test_disabled_subagent_profiles_do_not_register_task_tool() -> None:
    config = SlotFlowSubagentConfig(
        profiles=(
            SlotFlowSubagentProfile(
                name="disabled",
                description="Disabled test profile",
                system_prompt="Do not use.",
                enabled=False,
            ),
        )
    )

    assert build_subagent_tools(
        features=features_from_run_context(_bundle(mode="ultra").context),
        model=ToolAwareFakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        run_context=_bundle(mode="ultra").context,
        config=config,
    ) == []


@pytest.mark.asyncio
async def test_harness_graph_can_execute_subagent_task_tool() -> None:
    bundle = _bundle(mode="ultra")
    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task_tool",
                        "args": {
                            "agent_name": "coder",
                            "task": "检查模块 21 的工具注册顺序",
                            "context": "SlotFlow harness tests",
                            "expected_output": "返回工具顺序和风险",
                        },
                        "id": "call_task_tool",
                    }
                ],
            ),
            AIMessage(content="真实 coder 子任务结果。"),
            AIMessage(content="子任务结果已经收到。"),
        ]
    )
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试 subagent 的助手。",
            middleware_config=SlotFlowMiddlewareConfig(clarify_gate_enabled=False),
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "委派一个子任务"}]},
        config=bundle.config,
        context=bundle.context,
    )
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 1
    assert tool_messages[0].name == "task_tool"
    tool_result = json.loads(str(tool_messages[0].content))
    assert tool_result["agent_name"] == "coder"
    assert tool_result["status"] == "completed"
    assert tool_result["expected_output"] == "返回工具顺序和风险"
    assert tool_result["result"] == "真实 coder 子任务结果。"
    assert result["messages"][-1].content == "子任务结果已经收到。"
