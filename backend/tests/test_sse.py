"""AgentEvent 到业务 SSE 帧的转换测试。

这些测试证明 agent 事件可以稳定变成浏览器能消费的 SSE：

AgentEvent -> BusinessSseEvent -> SSE 文本帧
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.chat.agent_adapter import AgentEvent, LangGraphEventAgentAdapter
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.sse import (
    BusinessSseEvent,
    agent_event_to_sse_event,
    encode_sse_event,
    iter_business_events,
    iter_sse_frames,
    make_error_event,
)


def _data_from_frame(frame: str) -> dict:
    """从 SSE 文本帧里取出 data JSON，方便测试断言。"""

    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


async def _events(*events: AgentEvent) -> AsyncIterator[AgentEvent]:
    """把几条 AgentEvent 包成异步流。"""

    for event in events:
        yield event


async def _broken_events() -> AsyncIterator[AgentEvent]:
    """模拟 agent adapter 在流式过程中抛异常。"""

    yield AgentEvent(
        event="message.delta",
        data={
            "message_id": "msg_1",
            "role": "assistant",
            "delta": "出错前的片段",
            "index": 0,
        },
    )
    raise RuntimeError("agent adapter crashed")


def test_agent_event_to_sse_event_keeps_event_name_and_data() -> None:
    """AgentEvent 进入 SSE 层后，事件名和业务数据不能丢。"""

    event = agent_event_to_sse_event(
        AgentEvent(
            event="message.delta",
            data={"message_id": "msg_1", "delta": "你好"},
        )
    )

    assert event == BusinessSseEvent(
        event="message.delta",
        data={"message_id": "msg_1", "delta": "你好"},
    )


def test_agent_event_to_sse_event_keeps_tool_status() -> None:
    event = agent_event_to_sse_event(
        AgentEvent(
            event="tool.status",
            data={
                "tool_name": "sandbox_exec",
                "phase": "running",
                "message": "正在初始化 Docker 沙箱并执行代码",
            },
        )
    )

    assert event == BusinessSseEvent(
        event="tool.status",
        data={
            "tool_name": "sandbox_exec",
            "phase": "running",
            "message": "正在初始化 Docker 沙箱并执行代码",
        },
    )


def test_encode_sse_event_outputs_standard_frame() -> None:
    """业务事件要编码成浏览器能识别的 SSE 文本帧。"""

    frame = encode_sse_event(
        BusinessSseEvent(
            event="message.delta",
            event_id="evt_1",
            data={"message_id": "msg_1", "delta": "你好"},
        )
    )

    assert frame.startswith("id: evt_1\n")
    assert "event: message.delta\n" in frame
    assert 'data: {"message_id":"msg_1","delta":"你好"}\n' in frame
    assert frame.endswith("\n\n")


def test_make_error_event_keeps_exception_name_and_message() -> None:
    """异常要变成 run.error，并保留异常类型和信息。"""

    event = make_error_event(ValueError("bad input"))

    assert event.event == "run.error"
    assert event.data == {
        "name": "ValueError",
        "message": "bad input",
    }


@pytest.mark.asyncio
async def test_iter_business_events_converts_stream_exception_to_run_error() -> None:
    """上游流崩掉时，最后一条业务事件应该是 run.error。"""

    events = [event async for event in iter_business_events(_broken_events())]

    assert [event.event for event in events] == ["message.delta", "run.error"]
    assert events[-1].data == {
        "name": "RuntimeError",
        "message": "agent adapter crashed",
    }


@pytest.mark.asyncio
async def test_iter_sse_frames_encodes_agent_events() -> None:
    """AgentEvent 流可以完整变成 SSE 文本帧。"""

    frames = [
        frame
        async for frame in iter_sse_frames(
            _events(
                AgentEvent(event="run.prepared", data={"run_id": "run_1"}),
                AgentEvent(event="message.delta", data={"delta": "第一段"}),
                AgentEvent(event="run.finished", data={"run_id": "run_1"}),
            )
        )
    ]

    assert [frame.splitlines()[0] for frame in frames] == [
        "event: run.prepared",
        "event: message.delta",
        "event: run.finished",
    ]
    assert _data_from_frame(frames[1]) == {"delta": "第一段"}


@pytest.mark.asyncio
async def test_completed_agent_event_stream_can_be_encoded_as_sse_frames() -> None:
    """一轮完整 AgentEvent 流可以直接接到 SSE 编码器。"""

    frames = [
        frame
        async for frame in iter_sse_frames(
            _events(
                AgentEvent(
                    event="run.prepared",
                    data={
                        "thread_id": "thread_sse",
                        "run_id": "run_sse",
                    },
                ),
                AgentEvent(
                    event="message.delta",
                    data={
                        "message_id": "run_sse:assistant",
                        "role": "assistant",
                        "delta": "解释 SSE",
                        "index": 0,
                    },
                ),
                AgentEvent(
                    event="state.snapshot",
                    data={
                        "thread_id": "thread_sse",
                        "run_id": "run_sse",
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "解释 SSE",
                            }
                        ],
                        "state": {"mode": "pro"},
                    },
                ),
                AgentEvent(
                    event="run.finished",
                    data={
                        "thread_id": "thread_sse",
                        "run_id": "run_sse",
                    },
                ),
            )
        )
    ]
    event_lines = [frame.splitlines()[0] for frame in frames]

    assert event_lines[0] == "event: run.prepared"
    assert "event: message.delta" in event_lines[1:-2]
    assert event_lines[-2] == "event: state.snapshot"
    assert event_lines[-1] == "event: run.finished"

    first_data = _data_from_frame(frames[0])
    final_data = _data_from_frame(frames[-2])

    assert first_data["thread_id"] == "thread_sse"
    assert first_data["run_id"] == "run_sse"
    assert final_data["thread_id"] == "thread_sse"
    assert final_data["run_id"] == "run_sse"
    assert final_data["state"]["mode"] == "pro"


@pytest.mark.asyncio
async def test_langgraph_adapter_stream_can_be_encoded_as_sse_frames() -> None:
    """真实 v3 adapter 的 state.snapshot 也必须能被 JSON 编码成 SSE。"""

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    request = ChatStreamRequest(message="ping", model_name="fake-list-model")
    bundle = build_run_config(
        thread_id="thread_graph_sse",
        run_id="run_graph_sse",
        request=request,
    )
    graph = create_agent(
        model=FakeListChatModel(responses=["hello from LangGraph v3"]),
        tools=[],
    )
    adapter = LangGraphEventAgentAdapter(graph)

    frames = [
        frame
        async for frame in iter_sse_frames(adapter.stream_events(request=request, bundle=bundle))
    ]
    event_lines = [frame.splitlines()[0] for frame in frames]

    assert event_lines[0] == "event: run.prepared"
    assert "event: state.snapshot" in event_lines
    assert event_lines[-1] == "event: run.finished"
