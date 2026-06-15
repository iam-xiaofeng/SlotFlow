"""模块四测试：LangGraph v3 event streaming 适配层。

这组测试不启动 FastAPI，也不调用真实 DeepSeek API。它只验证一个边界：

LangGraph v3 typed projections
-> SlotFlow AgentEvent

这样做的好处是，真实模型和网络先不参与，事件形状先被我们固定下来。
"""

from __future__ import annotations

import json

import pytest

from app.chat.agent_adapter import (
    AgentEvent,
    LangGraphEventAgentAdapter,
    build_agent_input,
    clarification_event_from_snapshot,
    collect_agent_events,
    extract_message_delta,
    normalize_values_snapshot,
    projection_item_to_agent_event,
)
from app.chat.models import ChatStreamRequest, UploadedFileContext
from app.chat.run_config import build_run_config


def _bundle(
    request: ChatStreamRequest | None = None,
):
    """构建一份稳定 run bundle，减少测试重复样板。"""

    return build_run_config(
        thread_id="thread_test",
        run_id="run_test",
        request=request or ChatStreamRequest(message="解释 v3 投影"),
    )


def test_build_agent_input_uses_langchain_messages_shape() -> None:
    """LangChain agent 的输入应该是 `{"messages": [...]}`。"""

    payload = build_agent_input(ChatStreamRequest(message="你好"))

    assert payload == {
        "messages": [
            {
                "role": "user",
                "content": "你好",
            }
        ]
    }


def test_build_agent_input_makes_current_uploads_unambiguous() -> None:
    request = ChatStreamRequest(message="完整输出这个文件内容", files=["file_pdf"])
    bundle = build_run_config(
        thread_id="thread_test",
        run_id="run_test",
        request=request,
        uploaded_files=[
            UploadedFileContext(
                id="file_pdf",
                filename="upload.pdf",
                original_filename="交通视频分析系统.pdf",
                content_type="application/pdf",
                size_bytes=8787636,
                workspace_path="uploads/run_test/upload.pdf",
            )
        ],
    )

    payload = build_agent_input(request, bundle=bundle)
    content = payload["messages"][0]["content"]

    assert "完整输出这个文件内容" in content
    assert "<slotflow-current-uploaded-files>" in content
    assert "path=uploads/run_test/upload.pdf" in content
    assert "filename=交通视频分析系统.pdf" in content
    assert "Do not answer from previous uploaded files" in content


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"delta": "第一段"}, "第一段"),
        ({"content": "完整内容"}, "完整内容"),
        ({"event": "content-block-delta", "delta": {"type": "text-delta", "text": "逐字流出"}}, "逐字流出"),
        (({"delta": "tuple 里的消息"}, {"node": "model"}), "tuple 里的消息"),
    ],
)
def test_extract_message_delta_accepts_common_projection_shapes(item, expected) -> None:
    """message projection 可能是 dict，也可能是 tuple；我们只抽取文本增量。"""

    assert extract_message_delta(item) == expected


def test_projection_message_item_becomes_message_delta_event() -> None:
    """messages 投影应该变成前端逐字显示用的 message.delta。"""

    event = projection_item_to_agent_event(
        projection="messages",
        item={"delta": "你好"},
        bundle=_bundle(),
    )

    assert event == AgentEvent(
        event="message.delta",
        data={
            "message_id": "run_test:assistant",
            "role": "assistant",
            "delta": "你好",
            "index": None,
        },
    )


def test_projection_values_item_becomes_state_snapshot_event() -> None:
    """values 投影应该变成 state.snapshot，而不是让前端直接读 LangGraph 状态。"""

    event = projection_item_to_agent_event(
        projection="values",
        item={
            "messages": [{"role": "assistant", "content": "完成"}],
            "next": None,
        },
        bundle=_bundle(),
    )

    assert event is not None
    assert event.event == "state.snapshot"
    assert event.data == {
        "thread_id": "thread_test",
        "run_id": "run_test",
        "messages": [{"role": "assistant", "content": "完成"}],
        "state": {
            "messages": [{"role": "assistant", "content": "完成"}],
            "next": None,
        },
    }


def test_projection_tool_call_item_becomes_tool_delta_event() -> None:
    """工具调用投影先统一映射成 tool.delta，后面 UI 再决定怎么展示。"""

    event = projection_item_to_agent_event(
        projection="tool_calls",
        item={"name": "search", "args": {"query": "SlotFlow"}},
        bundle=_bundle(),
    )

    assert event == AgentEvent(
        event="tool.delta",
        data={"name": "search", "args": {"query": "SlotFlow"}},
    )


def test_normalize_values_snapshot_keeps_thread_and_run_identity() -> None:
    """状态快照必须带 thread_id/run_id，前端才能把它放回正确会话。"""

    snapshot = normalize_values_snapshot(
        item={"messages": [{"role": "assistant", "content": "完成"}]},
        bundle=_bundle(),
    )

    assert snapshot["thread_id"] == "thread_test"
    assert snapshot["run_id"] == "run_test"
    assert snapshot["messages"] == [{"role": "assistant", "content": "完成"}]


def test_clarification_tool_message_becomes_requested_event() -> None:
    payload = {
        "type": "clarification",
        "id": "clarification:call_1",
        "question": "你想分析哪个币种？",
        "clarification_type": "ambiguous_requirement",
        "context": "昨天的记忆里有 BTC 和 ETH。",
        "options": [{"id": "A", "label": "BTC"}],
        "source": "slotflow_clarification",
    }
    snapshot = normalize_values_snapshot(
        item={
            "messages": [
                {
                    "role": "tool",
                    "name": "ask_clarification",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            ]
        },
        bundle=_bundle(),
    )

    event = clarification_event_from_snapshot(snapshot)

    assert event == AgentEvent(
        event="clarification.requested",
        data={
            **payload,
            "thread_id": "thread_test",
            "run_id": "run_test",
        },
    )


def test_run_bundle_keeps_business_context_out_of_configurable() -> None:
    """业务 context 走 `context=` 通道，不回流到 configurable。"""

    bundle = _bundle(
        ChatStreamRequest(
            message="解释 context",
            model_name="deepseek-v4-flash",
            mode="ultra",
            files=["upload_1"],
        )
    )

    assert bundle.context.thread_id == "thread_test"
    assert bundle.context.run_id == "run_test"
    assert bundle.context.files == ["upload_1"]
    assert bundle.config == {
        "configurable": {
            "thread_id": "thread_test",
        },
    }


@pytest.mark.asyncio
async def test_langgraph_event_adapter_consumes_v3_projection_stream() -> None:
    """用 LangChain fake model 跑真实 v3 stream，证明 adapter 不只是静态模拟。

    这里重点验证主路径消费官方 typed projections。
    """

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    request = ChatStreamRequest(message="ping", model_name="fake-list-model")
    bundle = _bundle(request)
    graph = create_agent(
        model=FakeListChatModel(responses=["hello from LangGraph v3"]),
        tools=[],
    )
    adapter = LangGraphEventAgentAdapter(graph)

    events = await collect_agent_events(adapter.stream_events(request=request, bundle=bundle))
    event_names = [event.event for event in events]

    assert event_names[0] == "run.prepared"
    assert "message.delta" in event_names
    assert "state.snapshot" in event_names
    assert event_names[-1] == "run.finished"
    snapshot = next(event.data for event in events if event.event == "state.snapshot")
    assert snapshot["messages"][-1]["role"] == "ai"
    assert snapshot["messages"][-1]["content"] == "hello from LangGraph v3"


@pytest.mark.asyncio
async def test_langgraph_event_adapter_consumes_projection_stream_without_raw_iteration() -> None:
    """adapter 只消费 typed projections，不再保留 raw event 兼容分支。"""

    class ProjectionChannel:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            async def iterator():
                for item in self._items:
                    yield item

            return iterator()

    class ProjectionOnlyStream:
        def __init__(self) -> None:
            self.messages = ProjectionChannel(
                [[
                    {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "A"}},
                    {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "B"}},
                ]]
            )
            self.values = ProjectionChannel(
                [{"messages": [{"role": "assistant", "content": "AB"}]}]
            )
            self.tool_calls = ProjectionChannel([])

        def __aiter__(self):
            raise AssertionError("adapter should not iterate raw event stream")

    class StubGraph:
        async def astream_events(self, *_args, **_kwargs):
            return ProjectionOnlyStream()

    adapter = LangGraphEventAgentAdapter(StubGraph())
    events = await collect_agent_events(
        adapter.stream_events(
            request=ChatStreamRequest(message="ping"),
            bundle=_bundle(),
        )
    )

    assert [event.event for event in events] == [
        "run.prepared",
        "message.delta",
        "message.delta",
        "state.snapshot",
        "run.finished",
    ]
    assert "".join(event.data["delta"] for event in events if event.event == "message.delta") == "AB"
