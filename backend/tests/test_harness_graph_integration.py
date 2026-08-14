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
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

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
                            "name": "workspace_tools",
                            "args": {"names": ["workspace_read"]},
                            "id": "call_load_workspace",
                        }
                    ],
                ),
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
            middleware_config=SlotFlowMiddlewareConfig(),
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
    error_message = next(message for message in tool_messages if message.name == "workspace_read")
    payload = json.loads(str(error_message.content))

    assert len(tool_messages) == 2
    assert error_message.status == "error"
    assert error_message.name == "workspace_read"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["source"] == "slotflow_tool_safety"
    assert result["messages"][-1].content == "工具错误已收到。"


# ---------------------------------------------------------------------------
# 摘要链路回归：RemoveMessage 哨兵不得泄漏给模型；llm_input_messages 不得过期
# ---------------------------------------------------------------------------

_LONG_TURN = "这是一段刻意写得很长的历史消息，用来把近似的令牌计数推过摘要触发阈值。" * 6


class _RecordingFakeChatModel(FakeListChatModel):
    """FakeListChatModel + 记录每次喂给模型的完整消息列表（摘要与 agent 调用都会记录）。"""

    received: list = []

    def _call(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._call(messages, stop=stop, run_manager=run_manager, **kwargs)


def _summarization_bundle(thread_id: str, run_id: str):
    request = ChatStreamRequest(message="继续", mode="ultra")
    return build_run_config(thread_id=thread_id, run_id=run_id, request=request)


def _summarization_harness_config() -> SlotFlowHarnessConfig:
    return SlotFlowHarnessConfig(
        system_prompt="你是测试摘要链路的助手。",
        middleware_config=SlotFlowMiddlewareConfig(
            summarization_trigger_tokens=80,
            summarization_keep_messages=2,
            summarization_trim_tokens=4000,
        ),
    )


def _seed_history() -> list:
    history: list = []
    for index in range(4):
        history.append({"role": "user", "content": f"历史问题{index}：{_LONG_TURN}"})
        history.append({"role": "assistant", "content": f"历史回答{index}：{_LONG_TURN}"})
    return history


@pytest.mark.asyncio
async def test_summarization_never_leaks_remove_message_sentinel_to_model() -> None:
    """摘要触发后，模型输入里绝不允许出现 RemoveMessage 哨兵。

    官方 SummarizationMiddleware 返回的列表以 RemoveMessage(REMOVE_ALL_MESSAGES) 开头，
    那是给 messages 通道 add_messages reducer 的协议；llm_input_messages 是普通通道、
    由 agent 原样喂给模型 —— OpenAI 兼容序列化（含 DeepSeek）遇到哨兵会 TypeError。
    """

    bundle = _summarization_bundle("thread_summ_sentinel", "run_summ_sentinel")
    model = _RecordingFakeChatModel(responses=["摘要：早前对话已压缩", "收到", "再收到"])
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=_summarization_harness_config(),
    )

    result = await graph.ainvoke(
        {"messages": [*_seed_history(), {"role": "user", "content": "现在回答我的新问题"}]},
        config=bundle.config,
        context=bundle.context,
    )

    epoch_messages = (result.get("context_epoch") or {}).get("messages") or []
    assert any("早前对话已压缩" in str(m.content) for m in epoch_messages), (
        "摘要应当进入 model-facing epoch，否则本测试没有覆盖到目标路径"
    )
    assert any("历史问题0" in str(m.content) for m in result["messages"]), (
        "canonical history must remain lossless for context_archive access"
    )
    assert model.received, "fake model 应该至少被调用一次"
    for batch in model.received:
        assert not any(isinstance(m, RemoveMessage) for m in batch)


@pytest.mark.asyncio
async def test_llm_input_messages_projection_recomputed_after_summarization_turn() -> None:
    """摘要触发过的线程，下一轮的新用户消息必须仍能被模型看见。

    llm_input_messages 是普通 last-write 通道且被 checkpoint 持久化；若只在个别节点
    偶发写入、从不重算，一次摘要之后 agent 将永远读到旧快照，新消息全部隐身。
    pre_model 现在每步从 messages 重算投影，本测试钉住这个约定。
    """

    checkpointer = InMemorySaver()
    model = _RecordingFakeChatModel(
        responses=["摘要：早前对话已压缩", "第一轮回答", "第二轮回答", "备用回答"]
    )
    first = _summarization_bundle("thread_summ_stale", "run_summ_turn1")
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=first.context,
        harness_config=_summarization_harness_config(),
        checkpointer=checkpointer,
    )
    await graph.ainvoke(
        {"messages": [*_seed_history(), {"role": "user", "content": "第一轮：先做个总结"}]},
        config=first.config,
        context=first.context,
    )

    second = _summarization_bundle("thread_summ_stale", "run_summ_turn2")
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "第二轮：这句必须能被模型看见"}]},
        config=second.config,
        context=second.context,
    )

    agent_batches = [b for b in model.received if b and isinstance(b[0], SystemMessage)]
    assert agent_batches, "应存在 agent 节点的模型调用（其输入以 SystemMessage 开头）"
    last_batch_text = "\n".join(str(m.content) for m in agent_batches[-1])
    assert "第二轮：这句必须能被模型看见" in last_batch_text


# ---------------------------------------------------------------------------
# 缓存稳定契约：召回的长期记忆离开 system 前缀，改走尾部 SystemMessage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalled_memory_rides_trailing_suffix_not_system_prefix(tmp_path) -> None:
    """长期记忆不得进入 system 前缀（会随 query 每轮改动、打穿 tools→system→messages
    前缀缓存），必须作为最后一条 SystemMessage 追加在所有会话消息之后（append-only）。"""

    from app.harness.memory import SlotFlowMemoryStore

    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    request = ChatStreamRequest(message="占位", mode="pro")
    bundle = build_run_config(
        thread_id="thread_mem_suffix", run_id="run_mem_suffix", request=request
    )
    store.add_memory(thread_id=bundle.context.thread_id, content="用户最喜欢的颜色是蓝色")

    model = _RecordingFakeChatModel(responses=["蓝色。", "备用", "备用2"])
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试缓存契约的助手。",
            memory_store=store,
            middleware_config=SlotFlowMiddlewareConfig(
                    proactive_memory_extraction_enabled=False,
            ),
        ),
    )
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "我最喜欢的颜色是什么？"}]},
        config=bundle.config,
        context=bundle.context,
    )

    agent_batches = [b for b in model.received if b and isinstance(b[0], SystemMessage)]
    assert agent_batches, "应存在 agent 节点的模型调用"
    batch = agent_batches[-1]
    marker = "<slotflow-long-term-memory>"
    # 前缀（第一条 system）保持稳定：不含记忆段。
    assert marker not in str(batch[0].content)
    # 记忆作为最后一条 user 角色的 <system-reminder> 追加在尾部，且带上具体记忆内容。
    assert isinstance(batch[-1], HumanMessage)
    assert "<system-reminder>" in str(batch[-1].content)
    assert marker in str(batch[-1].content)
    assert "蓝色" in str(batch[-1].content)


@pytest.mark.asyncio
async def test_summarization_carries_a_skills_ledger_into_the_compacted_view() -> None:
    """压缩会把 skill_read 的正文整段折叠掉,台账必须留在压缩视图里。

    否则模型压缩之后只知道"聊过很多",不知道自己已经在按某个 Skill 的流程做事——
    这正是一次运行半途悄悄放弃 Skill 的方式。台账是确定性追加的,不依赖摘要模型听话。
    """

    bundle = _summarization_bundle("thread_summ_ledger", "run_summ_ledger")
    model = _RecordingFakeChatModel(responses=["摘要：早前对话已压缩", "收到", "再收到"])
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=_summarization_harness_config(),
    )

    result = await graph.ainvoke(
        {
            "messages": [*_seed_history(), {"role": "user", "content": "现在回答我的新问题"}],
            "used_skills": ["pdf-report", "charting"],
        },
        config=bundle.config,
        context=bundle.context,
    )

    epoch_text = "\n".join(
        str(message.content) for message in (result.get("context_epoch") or {}).get("messages") or []
    )
    assert "<slotflow-skills-ledger>" in epoch_text
    assert "pdf-report" in epoch_text and "charting" in epoch_text
    assert "skill_read(name)" in epoch_text
    assert "context_archive_search" in epoch_text


@pytest.mark.asyncio
async def test_system_prefix_stays_byte_identical_across_turns() -> None:
    """`tools → system → messages` 前缀逐字节恒定,否则 provider 的前缀缓存每轮清零。

    易变内容(skills preflight / 召回记忆 / todo 控制)一律走尾部 <system-reminder>。
    2026-08-14 之前 preflight 拼在 system 段里,每个新用户轮都会改写前缀。
    """

    checkpointer = InMemorySaver()
    model = _RecordingFakeChatModel(responses=["第一轮回答", "第二轮回答", "备用回答"])
    config = SlotFlowHarnessConfig(
        system_prompt="你是测试前缀稳定性的助手。",
        middleware_config=SlotFlowMiddlewareConfig(),
    )

    first = _summarization_bundle("thread_prefix_stable", "run_prefix_1")
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=first.context,
        harness_config=config,
        checkpointer=checkpointer,
    )
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "请分析这批股票数据并生成可视化报告"}]},
        config=first.config,
        context=first.context,
    )

    second = _summarization_bundle("thread_prefix_stable", "run_prefix_2")
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "再帮我研究一下这份专利文献的分析方法"}]},
        config=second.config,
        context=second.context,
    )

    system_texts = {
        str(batch[0].content)
        for batch in model.received
        if batch and isinstance(batch[0], SystemMessage)
    }
    assert len(system_texts) == 1, "system 前缀在多轮之间发生了变化"
    system_text = system_texts.pop()
    # 易变内容一律不得出现在 system 段:2026-08-14 之前 skills preflight 就漏在这里,
    # 它每个新用户轮都带着用户原话重算,等于每开一个新话题就自己打掉一次前缀缓存。
    # (preflight 已整体删除;召回记忆走尾部的正面证据见
    #  test_recalled_memory_rides_trailing_suffix_not_system_prefix。)
    for volatile_marker in ("skills-preflight", "slotflow-long-term-memory>", "todo-enforcer"):
        assert volatile_marker not in system_text, volatile_marker
