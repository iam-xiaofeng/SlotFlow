"""Module 14 tests: SlotFlow harness middleware registry."""

from __future__ import annotations

import json

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.runtime import Runtime

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.middleware import (
    SlotFlowMiddlewareConfig,
    SlotFlowRuntimeSummaryMiddleware,
    SlotFlowToolSafetyMiddleware,
    build_harness_middleware,
)
from app.harness.middleware.tool_safety import repair_dangling_tool_calls
from app.harness.sandbox import SlotFlowSandboxConfig


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """测试用 fake model：允许 LangChain agent 绑定工具。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _bundle():
    request = ChatStreamRequest(
        message="解释 middleware",
        mode="ultra",
        files=["upload_a"],
    )
    return build_run_config(
        thread_id="thread_middleware",
        run_id="run_middleware",
        request=request,
    )


def test_runtime_summary_middleware_writes_compact_context_snapshot() -> None:
    bundle = _bundle()
    features = features_from_run_context(bundle.context)
    middleware = SlotFlowRuntimeSummaryMiddleware(features=features)

    update = middleware.before_agent(
        {"messages": [], "slotflow": {"existing": "kept"}},
        Runtime(context=bundle.context),
    )

    assert update == {
        "slotflow": {
            "existing": "kept",
            "runtime": {
                "thread_id": "thread_middleware",
                "run_id": "run_middleware",
                "model_name": "deepseek-v4-flash",
                "mode": "ultra",
                "agent_name": "default",
                "thinking_enabled": True,
                "plan_enabled": True,
                "subagent_enabled": True,
                "files_count": 1,
                "uploaded_files": [],
            },
        }
    }


def test_build_harness_middleware_adds_runtime_summary_by_default() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
    )

    assert [item.name for item in middleware] == [
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]


def test_build_harness_middleware_can_disable_runtime_summary() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        config=SlotFlowMiddlewareConfig(runtime_summary_enabled=False),
    )

    assert [item.name for item in middleware] == ["SlotFlowToolSafetyMiddleware"]


def test_build_harness_middleware_can_disable_tool_safety() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        config=SlotFlowMiddlewareConfig(tool_safety_enabled=False),
    )

    assert [item.name for item in middleware] == ["SlotFlowRuntimeSummaryMiddleware"]


def test_build_harness_middleware_dedupes_by_name() -> None:
    features = features_from_run_context(_bundle().context)
    replacement = SlotFlowRuntimeSummaryMiddleware(features=features)

    middleware = build_harness_middleware(
        features=features,
        extra_middleware=[replacement],
    )

    assert [item.name for item in middleware] == [
        "SlotFlowRuntimeSummaryMiddleware",
        "SlotFlowToolSafetyMiddleware",
    ]
    assert middleware[0] is replacement


def test_tool_safety_middleware_converts_tool_exception_to_error_message() -> None:
    @tool("boom")
    def boom_tool() -> str:
        """Tool placeholder used to test middleware exception handling."""

        return "unused"

    middleware = SlotFlowToolSafetyMiddleware()
    request = ToolCallRequest(
        tool_call={"name": "boom", "args": {}, "id": "call_boom"},
        tool=boom_tool,
        state={},
        runtime=None,
    )

    def handler(_: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("boom failed")

    message = middleware.wrap_tool_call(request, handler)
    payload = json.loads(str(message.content))

    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.name == "boom"
    assert message.tool_call_id == "call_boom"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["exception_type"] == "RuntimeError"
    assert payload["error"]["source"] == "slotflow_tool_safety"


def test_tool_safety_middleware_handles_unknown_tool_without_calling_handler() -> None:
    middleware = SlotFlowToolSafetyMiddleware()
    calls: list[str] = []
    request = ToolCallRequest(
        tool_call={"name": "missing_tool", "args": {}, "id": "call_missing"},
        tool=None,
        state={},
        runtime=None,
    )

    def handler(_: ToolCallRequest) -> ToolMessage:
        calls.append("called")
        return ToolMessage(content="unused", tool_call_id="call_missing")

    message = middleware.wrap_tool_call(request, handler)
    payload = json.loads(str(message.content))

    assert calls == []
    assert message.status == "error"
    assert message.name == "missing_tool"
    assert payload["error"]["type"] == "unknown_tool"


def test_repair_dangling_tool_calls_inserts_error_before_next_model_message() -> None:
    repaired = repair_dangling_tool_calls(
        [
            HumanMessage(content="first"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {"path": "missing.txt"},
                        "id": "call_read",
                    }
                ],
            ),
            HumanMessage(content="next"),
        ]
    )

    assert len(repaired) == 4
    assert isinstance(repaired[2], ToolMessage)
    assert repaired[2].status == "error"
    assert repaired[2].tool_call_id == "call_read"
    assert json.loads(str(repaired[2].content))["error"]["type"] == "dangling_tool_call"


@pytest.mark.asyncio
async def test_harness_graph_runs_runtime_summary_middleware() -> None:
    bundle = _bundle()
    graph = build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["middleware ok"]),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(system_prompt="你是测试 middleware 的助手。"),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取 runtime 摘要"}]},
        config=bundle.config,
        context=bundle.context,
    )

    assert result["slotflow"]["runtime"]["run_id"] == bundle.context.run_id
    assert result["slotflow"]["runtime"]["subagent_enabled"] is True


@pytest.mark.asyncio
async def test_harness_graph_turns_tool_exception_into_error_tool_message(tmp_path) -> None:
    bundle = _bundle()
    graph = build_slotflow_harness_graph(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "workspace_read",
                            "args": {"path": "../outside.txt"},
                            "id": "call_bad_path",
                        }
                    ],
                ),
                AIMessage(content="工具错误已收到。"),
            ]
        ),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试 tool safety 的助手。",
            sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace"),
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取非法路径"}]},
        config=bundle.config,
        context=bundle.context,
    )
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]
    payload = json.loads(str(tool_messages[0].content))

    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert tool_messages[0].name == "workspace_read"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["source"] == "slotflow_tool_safety"
    assert result["messages"][-1].content == "工具错误已收到。"
