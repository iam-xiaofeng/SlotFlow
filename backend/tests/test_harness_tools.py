"""模块 11 测试：SlotFlow harness 安全内置工具。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools import slotflow_context_tool
from app.harness.tools.registry import build_harness_tools
from app.harness.tools.workspace import build_workspace_tools


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """测试用 fake model：允许 LangChain agent 绑定工具。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _bundle():
    request = ChatStreamRequest(message="调用工具", mode="pro")
    return build_run_config(
        thread_id="thread_tool",
        run_id="run_tool",
        request=request,
    )


def test_slotflow_context_tool_is_read_only_and_json_shaped() -> None:
    """第一批内置工具只返回上下文摘要，不碰文件、网络或 sandbox。"""

    raw = slotflow_context_tool.invoke(
        {
            "thread_id": "thread_tool",
            "run_id": "run_tool",
            "mode": "pro",
        }
    )

    assert json.loads(raw) == {
        "thread_id": "thread_tool",
        "run_id": "run_tool",
        "mode": "pro",
        "source": "slotflow_context_tool",
    }


def test_build_harness_tools_adds_safe_builtin_and_dedupes_by_name() -> None:
    """registry 是后续 builtin/workspace/MCP/subagent 工具的统一入口。"""

    @tool("slotflow_context")
    def replacement_context_tool() -> str:
        """Replacement tool used to prove first-name wins dedupe."""

        return "replacement"

    tools = build_harness_tools(
        features=features_from_run_context(_bundle().context),
        extra_tools=[replacement_context_tool],
    )

    assert [tool.name for tool in tools] == [
        "slotflow_context",
        "workspace_list",
        "workspace_read",
    ]
    assert tools[0] is replacement_context_tool


def test_workspace_tools_list_and_read_text_files(tmp_path: Path) -> None:
    """文件工具只能通过 SlotFlowWorkspace 访问 workspace root 内部内容。"""

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("hello", encoding="utf-8")
    tools = {
        item.name: item
        for item in build_workspace_tools(SlotFlowSandboxConfig(workspace_root=root))
    }

    assert list(tools) == ["workspace_list", "workspace_read"]
    listing = json.loads(tools["workspace_list"].invoke({"path": "."}))
    read_result = json.loads(tools["workspace_read"].invoke({"path": "docs/a.txt"}))

    assert listing == {
        "path": ".",
        "entries": [{"path": "docs", "kind": "directory", "size_bytes": None}],
        "source": "slotflow_workspace",
    }
    assert read_result == {
        "path": "docs/a.txt",
        "content": "hello",
        "size_bytes": 5,
        "source": "slotflow_workspace",
    }


def test_workspace_write_tool_is_only_registered_when_enabled(tmp_path: Path) -> None:
    """workspace_write 必须由 sandbox config 显式开启。"""

    read_only_tools = build_workspace_tools(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "readonly")
    )
    assert [item.name for item in read_only_tools] == ["workspace_list", "workspace_read"]

    writable_root = tmp_path / "writable"
    writable_tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(
                workspace_root=writable_root,
                writes_enabled=True,
            )
        )
    }

    raw = writable_tools["workspace_write"].invoke(
        {"path": "notes/a.txt", "content": "hello"}
    )

    assert list(writable_tools) == ["workspace_list", "workspace_read", "workspace_write"]
    assert json.loads(raw) == {
        "path": "notes/a.txt",
        "bytes_written": 5,
        "source": "slotflow_workspace",
    }
    assert (writable_root / "notes" / "a.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_harness_graph_can_execute_builtin_tool_call() -> None:
    """真实 LangGraph graph 能执行 harness 绑定的内置工具。"""

    bundle = _bundle()
    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "slotflow_context",
                        "args": {
                            "thread_id": bundle.context.thread_id,
                            "run_id": bundle.context.run_id,
                            "mode": bundle.context.mode,
                        },
                        "id": "call_slotflow_context",
                    }
                ],
            ),
            AIMessage(content="工具结果已经收到。"),
        ]
    )
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(system_prompt="你是测试 harness 的助手。"),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取当前 SlotFlow run context"}]},
        config=bundle.config,
        context=bundle.context,
    )

    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 1
    assert tool_messages[0].name == "slotflow_context"
    assert json.loads(str(tool_messages[0].content))["run_id"] == bundle.context.run_id
    assert result["messages"][-1].content == "工具结果已经收到。"
