"""LangGraph v3 event streaming 适配层测试。

这组测试不启动 FastAPI，也不调用真实 DeepSeek API。它只验证一个边界：

LangGraph v3 typed projections
-> SlotFlow AgentEvent

这样做的好处是，真实模型和网络不参与，事件形状可以稳定验证。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from app.chat.agent_adapter import (
    AgentEvent,
    LangGraphEventAgentAdapter,
    build_agent_input,
    clarification_event_from_interrupt,
    collect_agent_events,
    extract_message_delta,
    extract_message_delta_parts,
    flatten_message_projection_items,
    iter_projection_agent_events,
    normalize_message_content,
    normalize_values_snapshot,
    strip_slotflow_context_blocks,
    projection_item_to_agent_event,
    todo_event_from_snapshot,
    tool_status_event_from_tool_call,
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


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"type": "reasoning", "reasoning": "deepseek/openai 风格"}, "deepseek/openai 风格"),
        ({"type": "thinking", "thinking": "anthropic 扩展思考"}, "anthropic 扩展思考"),
        ({"additional_kwargs": {"reasoning_content": "kw 风格"}}, "kw 风格"),
    ],
)
def test_extract_message_delta_parts_reads_litellm_reasoning(item, expected) -> None:
    """The projection consumes LangChain blocks and LiteLLM reasoning metadata."""

    assert extract_message_delta_parts(item) == {"reasoning_content": expected}


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
            "channel": "content",
            "index": None,
        },
    )


def test_deepseek_reasoning_content_becomes_reasoning_delta_event() -> None:
    chunk = SimpleNamespace(
        additional_kwargs={"reasoning_content": "先分析问题"},
        content="",
    )

    event = projection_item_to_agent_event(
        projection="messages",
        item=chunk,
        bundle=_bundle(),
    )

    assert event == AgentEvent(
        event="message.delta",
        data={
            "message_id": "run_test:assistant",
            "role": "assistant",
            "delta": "先分析问题",
            "channel": "reasoning",
            "index": None,
        },
    )


def test_standard_reasoning_content_block_becomes_reasoning_delta_event() -> None:
    chunk = AIMessageChunk(content=[{"type": "reasoning", "reasoning": "逐步分析"}])

    event = projection_item_to_agent_event(
        projection="messages",
        item=chunk,
        bundle=_bundle(),
    )

    assert event == AgentEvent(
        event="message.delta",
        data={
            "message_id": "run_test:assistant",
            "role": "assistant",
            "delta": "逐步分析",
            "channel": "reasoning",
            "index": None,
        },
    )


def test_extract_message_delta_parts_keeps_content_and_reasoning_separate() -> None:
    assert extract_message_delta_parts(
        {"content_blocks": [{"type": "reasoning", "reasoning": "推理"}]},
    ) == {
        "reasoning_content": "推理",
    }
    assert extract_message_delta_parts({"content": "正文"}) == {"content": "正文"}


def test_extract_message_delta_parts_reads_standard_reasoning_delta() -> None:
    assert extract_message_delta_parts(
        {
            "event": "content-block-delta",
            "delta": {"type": "reasoning", "reasoning": "先理解需求"},
        },
    ) == {"reasoning_content": "先理解需求"}


def test_extract_message_delta_parts_reads_litellm_additional_kwargs() -> None:
    chunk = SimpleNamespace(
        additional_kwargs={"reasoning_content": "拆解任务"},
        content="",
    )

    assert extract_message_delta_parts((chunk, {"langgraph_node": "model"})) == {
        "reasoning_content": "拆解任务",
    }


def test_extract_message_delta_parts_reads_reasoning_content_block() -> None:
    assert extract_message_delta_parts(
        {
            "content": [
                {"type": "reasoning", "reasoning": "先判断用户意图"},
                {"type": "text", "text": "正文"},
            ],
        },
    ) == {"reasoning_content": "先判断用户意图"}


def test_summarization_projection_delta_is_not_public_message_delta() -> None:
    event = projection_item_to_agent_event(
        projection="messages",
        item=(
            {"delta": "Here is a concise summary of the conversation."},
            {"metadata": {"lc_source": "summarization"}},
        ),
        bundle=_bundle(),
    )

    assert event is None


def test_summarization_projection_delta_is_filtered_by_langgraph_node() -> None:
    event = projection_item_to_agent_event(
        projection="messages",
        item=SimpleNamespace(
            _node="SlotFlowSummarizationMiddleware",
            content="Summary for next model call",
        ),
        bundle=_bundle(),
    )

    assert event is None


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


def test_sandbox_tool_call_becomes_tool_status_event() -> None:
    """sandbox_exec 开始执行时要给前端一个可见状态，避免 Docker 启动期像卡死。"""

    event = tool_status_event_from_tool_call(
        {
            "name": "sandbox_exec",
            "args": {"command": "python -m pip install pandas && python script.py"},
        },
        bundle=_bundle(),
    )

    assert event == AgentEvent(
        event="tool.status",
        data={
            "thread_id": "thread_test",
            "run_id": "run_test",
            "tool_name": "sandbox_exec",
            "phase": "running",
            "message": "正在初始化 Docker 沙箱并执行代码",
            "command": "python -m pip install pandas && python script.py",
            "source": "slotflow_tool_call_projection",
        },
    )


def test_generic_tool_call_becomes_tool_status_event() -> None:
    """所有普通工具执行都要对前端可见,否则用户只看到冻结的思考态。"""

    event = tool_status_event_from_tool_call(
        {"name": "artifact_write", "args": {"path": "hello.html"}},
        bundle=_bundle(),
    )

    assert event is not None
    assert event.event == "tool.status"
    assert event.data["tool_name"] == "artifact_write"
    assert event.data["phase"] == "running"
    assert event.data["message"] == "正在写入产物文件"
    assert event.data["command"] is None

    unknown = tool_status_event_from_tool_call(
        {"name": "some_mcp_tool", "args": {}},
        bundle=_bundle(),
    )
    assert unknown is not None
    assert unknown.data["message"] == "正在调用工具"


def test_sandbox_artifact_copy_tool_call_becomes_tool_status_event() -> None:
    event = tool_status_event_from_tool_call(
        {"name": "sandbox_artifact_copy", "args": {"source_path": "chart.png"}},
        bundle=_bundle(),
    )

    assert event is not None
    assert event.data["tool_name"] == "sandbox_artifact_copy"
    assert event.data["message"] == "正在发布 Docker 文件到产物"
    assert event.data["command"] is None


def test_own_ui_tool_calls_do_not_become_tool_status_event() -> None:
    """澄清与 todo 工具有专属 UI,不再叠加状态芯片。"""

    for tool_name in ("ask_clarification", "write_todos"):
        event = tool_status_event_from_tool_call(
            {"name": tool_name, "args": {}},
            bundle=_bundle(),
        )
        assert event is None, tool_name


def test_normalize_values_snapshot_keeps_thread_and_run_identity() -> None:
    """状态快照必须带 thread_id/run_id，前端才能把它放回正确会话。"""

    snapshot = normalize_values_snapshot(
        item={"messages": [{"role": "assistant", "content": "完成"}]},
        bundle=_bundle(),
    )

    assert snapshot["thread_id"] == "thread_test"
    assert snapshot["run_id"] == "run_test"
    assert snapshot["messages"] == [{"role": "assistant", "content": "完成"}]


def test_clarification_interrupt_becomes_requested_event() -> None:
    payload = {
        "type": "clarification",
        "id": "clarification:call_1",
        "question": "你想分析哪个币种？",
        "clarification_type": "ambiguous_requirement",
        "context": "昨天的记忆里有 BTC 和 ETH。",
        "options": [{"id": "A", "label": "BTC"}],
        "source": "slotflow_clarification",
    }

    event = clarification_event_from_interrupt(payload, bundle=_bundle())

    assert event == AgentEvent(
        event="clarification.requested",
        data={
            **payload,
            "thread_id": "thread_test",
            "run_id": "run_test",
        },
    )


def test_clarification_interrupt_ignores_non_clarification_value() -> None:
    # An interrupt value that is not a clarification payload yields no event (defensive).
    assert clarification_event_from_interrupt({"type": "other"}, bundle=_bundle()) is None
    assert clarification_event_from_interrupt(None, bundle=_bundle()) is None


@pytest.mark.asyncio
async def test_adapter_surfaces_clarification_then_resume_does_not_re_pop() -> None:
    """End-to-end through the real adapter: turn 1 asks (interrupt) → turn 2 (the answer)
    resumes the SAME thread, produces the final answer, and emits NO clarification.requested.

    This is the regression guard for the reported bug — an answered clarification must never
    re-pop on the next turn. The adapter resumes from the pending interrupt and surfaces a
    clarification ONLY when one is freshly pending, never re-derived from history.
    """

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from app.harness.tools.builtins import ask_clarification_tool

    class _ToolAwareFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

    model = _ToolAwareFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {"question": "你想分析哪个币种？", "clarification_type": "ambiguous_requirement", "options": ["BTC", "ETH"]},
                        "id": "call_clarify",
                    }
                ],
            ),
            AIMessage(content="好的，我来分析 BTC 的近期走势。"),
        ]
    )
    graph = create_agent(model=model, tools=[ask_clarification_tool], checkpointer=InMemorySaver())
    adapter = LangGraphEventAgentAdapter(graph)

    ask_bundle = build_run_config(
        thread_id="thread_clar", run_id="run_ask", request=ChatStreamRequest(message="分析一下")
    )
    ans_bundle = build_run_config(
        thread_id="thread_clar", run_id="run_ans", request=ChatStreamRequest(message="BTC")
    )

    turn1 = await collect_agent_events(
        adapter.stream_events(request=ChatStreamRequest(message="分析一下"), bundle=ask_bundle)
    )
    assert "clarification.requested" in [e.event for e in turn1]
    clar = next(e for e in turn1 if e.event == "clarification.requested")
    assert clar.data["question"] == "你想分析哪个币种？"

    turn2 = await collect_agent_events(
        adapter.stream_events(request=ChatStreamRequest(message="BTC"), bundle=ans_bundle)
    )
    # The answer turn must NOT re-pop the clarification, and must produce the real answer.
    assert "clarification.requested" not in [e.event for e in turn2]
    answer = "".join(
        e.data.get("delta", "") for e in turn2 if e.event == "message.delta" and e.data.get("channel") == "content"
    )
    snapshot = next((e for e in turn2 if e.event == "state.snapshot"), None)
    final = snapshot.data["messages"][-1]["content"] if snapshot else ""
    assert "BTC" in (answer + final)


def test_todos_in_values_snapshot_become_todo_updated_event() -> None:
    snapshot = normalize_values_snapshot(
        item={
            "messages": [],
            "todos": [
                {"text": "读取代码", "status": "completed"},
                {"content": "补前端展示", "status": "in_progress"},
                {"content": "跑测试", "status": "later"},
            ],
        },
        bundle=_bundle(),
    )

    event = todo_event_from_snapshot(snapshot)

    assert event == AgentEvent(
        event="todo.updated",
        data={
            "thread_id": "thread_test",
            "run_id": "run_test",
            "todos": [
                {"content": "读取代码", "status": "completed"},
                {"content": "补前端展示", "status": "in_progress"},
                {"content": "跑测试", "status": "pending"},
            ],
        },
    )


@pytest.mark.asyncio
async def test_identical_todo_snapshots_emit_each_todo_updated_event() -> None:
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
            snapshot = {
                "messages": [],
                "todos": [{"content": "保持当前状态", "status": "in_progress"}],
            }
            self.values = ProjectionChannel([snapshot, snapshot])

    events = [
        event
        async for event in iter_projection_agent_events(
            ProjectionOnlyStream(),
            bundle=_bundle(),
        )
    ]

    todo_events = [event for event in events if event.event == "todo.updated"]
    assert len(todo_events) == 2
    assert todo_events[0].data["todos"] == [
        {"content": "保持当前状态", "status": "in_progress"}
    ]


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
    assert all(
        event.data["channel"] == "content"
        for event in events
        if event.event == "message.delta"
    )


@pytest.mark.asyncio
async def test_langgraph_event_adapter_emits_sandbox_tool_status_before_tool_delta() -> None:
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
            self.messages = ProjectionChannel([])
            self.values = ProjectionChannel([])
            self.tool_calls = ProjectionChannel(
                [
                    {
                        "name": "sandbox_exec",
                        "args": {"command": "python -V"},
                    }
                ]
            )

    events = [
        event
        async for event in iter_projection_agent_events(
            ProjectionOnlyStream(),
            bundle=_bundle(),
        )
    ]

    assert [event.event for event in events] == ["tool.status", "tool.delta"]
    assert events[0].data["message"] == "正在初始化 Docker 沙箱并执行代码"
    assert events[0].data["command"] == "python -V"


@pytest.mark.asyncio
async def test_langgraph_event_adapter_uses_typed_message_reasoning_projection() -> None:
    class ProjectionChannel:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            async def iterator():
                for item in self._items:
                    yield item

            return iterator()

    class TypedMessageStream:
        _node = "model"

        def __init__(self) -> None:
            self.reasoning = ProjectionChannel(["先", "想"])
            self.text = ProjectionChannel(["回答"])

    class ProjectionOnlyStream:
        def __init__(self) -> None:
            self.messages = ProjectionChannel([TypedMessageStream()])
            self.values = ProjectionChannel([])
            self.tool_calls = ProjectionChannel([])

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
    message_events = [event for event in events if event.event == "message.delta"]

    assert "".join(
        event.data["delta"]
        for event in message_events
        if event.data["channel"] == "reasoning"
    ) == "先想"
    assert "".join(
        event.data["delta"]
        for event in message_events
        if event.data["channel"] == "content"
    ) == "回答"


@pytest.mark.asyncio
async def test_langgraph_event_adapter_drains_summarization_substream_without_leaking_text() -> None:
    class ProjectionChannel:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            async def iterator():
                for item in self._items:
                    yield item

            return iterator()

    class SummarizationSubstream:
        _node = "SlotFlowSummarizationMiddleware"

        def __aiter__(self):
            async def iterator():
                yield {"delta": "Summary for next model call"}

            return iterator()

    class ProjectionOnlyStream:
        def __init__(self) -> None:
            self.messages = ProjectionChannel(
                [
                    SummarizationSubstream(),
                    [{"event": "content-block-delta", "delta": {"type": "text-delta", "text": "最终答复"}}],
                ]
            )
            self.values = ProjectionChannel([])
            self.tool_calls = ProjectionChannel([])

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
        "context.compressing",
        "message.delta",
        "run.finished",
    ]
    assert "Summary" not in "".join(
        str(event.data.get("delta", ""))
        for event in events
        if event.event == "message.delta"
    )
    assert events[2].data["delta"] == "最终答复"


@pytest.mark.asyncio
async def test_langgraph_event_adapter_filters_real_summarization_middleware_stream() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from app.harness.builder import build_slotflow_harness_graph
    from app.harness.config import SlotFlowHarnessConfig
    from app.harness.middleware import SlotFlowMiddlewareConfig
    from app.chat.run_config import build_run_config

    class _ToolAware(FakeMessagesListChatModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

    # The summarization node reuses the chat model for its internal summary call (advancing
    # the cursor once), so we provide: turn-1 answer, turn-2 summary text (filtered by node
    # name), turn-2 final answer.
    chat_model = _ToolAware(
        responses=[
            AIMessage(content="旧回答"),
            AIMessage(content="Summary for next model call"),
            AIMessage(content="最终答复"),
        ]
    )
    request = ChatStreamRequest(message="旧上下文", mode="flash")
    bundle = build_run_config(thread_id="tsum", run_id="r1", request=request)
    graph = build_slotflow_harness_graph(
        model=chat_model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试助手。",
            middleware_config=SlotFlowMiddlewareConfig(
                clarify_gate_enabled=False,
                summarization_enabled=True,
                summarization_trigger_tokens=1,
                summarization_keep_messages=1,
                summarization_trim_tokens=100,
            ),
        ),
        checkpointer=InMemorySaver(),
    )
    adapter = LangGraphEventAgentAdapter(graph)

    await collect_agent_events(
        adapter.stream_events(
            request=ChatStreamRequest(message="旧上下文"),
            bundle=bundle,
        )
    )
    # Resume on the same thread so turn-2 sees turn-1 history and triggers summarization.
    events = await collect_agent_events(
        adapter.stream_events(
            request=ChatStreamRequest(message="继续"),
            bundle=build_run_config(thread_id="tsum", run_id="r2", request=ChatStreamRequest(message="继续")),
        )
    )

    assert "context.compressing" in [event.event for event in events]
    # The summary node's internal model stream is filtered by node name; only the agent
    # node's final answer should surface as message.delta.
    deltas = "".join(
        str(event.data.get("delta", ""))
        for event in events
        if event.event == "message.delta"
    )
    assert "Summary for next model call" not in deltas
    assert deltas == "最终答复"


@pytest.mark.asyncio
async def test_message_projection_flattening_preserves_parent_metadata() -> None:
    class ProjectionChannel:
        def __aiter__(self):
            async def iterator():
                yield {"delta": "internal summary"}

            return iterator()

    items = [
        item
        async for item in flatten_message_projection_items(
            (ProjectionChannel(), {"metadata": {"lc_source": "summarization"}})
        )
    ]

    assert items == [
        (
            {"delta": "internal summary"},
            {"metadata": {"lc_source": "summarization"}},
        )
    ]


def test_normalize_message_content_reasoning_only_returns_empty() -> None:
    """纯 reasoning 块消息的正文必须是空串——曾被 repr 成
    \"[{type: reasoning, ...}]\" 直接当回复展示(2026-07-04 真机踩坑)。"""

    content = [{"type": "reasoning", "reasoning": "内部思考过程", "index": 0}]

    assert normalize_message_content(content) == ""


def test_normalize_message_content_unknown_payload_never_reprs() -> None:
    assert normalize_message_content({"weird": {"nested": 1}}) == ""
    assert normalize_message_content(12345) == ""


def test_normalize_message_content_strips_slotflow_context_blocks() -> None:
    """模型复读的 <slotflow-*> 内部标签块不得进入用户可见正文。"""

    text = (
        "答案开头<slotflow-todo-reminder>\n内部提醒内容\n</slotflow-todo-reminder>答案结尾"
    )

    assert normalize_message_content(text) == "答案开头答案结尾"
    assert strip_slotflow_context_blocks("<slotflow-runtime>x</slotflow-runtime>好") == "好"
    assert strip_slotflow_context_blocks("无标签正文不受影响") == "无标签正文不受影响"
    assert strip_slotflow_context_blocks("孤立标签<slotflow-todo-enforcer>也剥") == "孤立标签也剥"
