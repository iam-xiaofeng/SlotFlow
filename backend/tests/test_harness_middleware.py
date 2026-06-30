"""Tests for the SlotFlow harness graph integration (node+edge version).

重构后中间件单测已迁移到 tests/test_harness_steps.py；本文件只保留两个 graph 级
集成测试（runtime summary 进 state、tool 异常 → error ToolMessage），覆盖 build_slotflow_harness_graph
组装的真实图行为。
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.middleware import SlotFlowMiddlewareConfig
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
            middleware_config=SlotFlowMiddlewareConfig(clarify_gate_enabled=False),
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
