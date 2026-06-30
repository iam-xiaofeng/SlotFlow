"""Tests for the clarify-gate step (pro/ultra first-step clarify enforcement).

重构后澄清门逻辑在 app/harness/steps/clarify_gate.py；这里覆盖 triage/interrupt/防循环
语义，并通过 build_slotflow_harness_graph 做端到端 interrupt+resume 验证。
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.chat.models import RunContext
from app.chat.run_config import build_run_config
from app.chat.models import ChatStreamRequest
from app.harness.config import SlotFlowHarnessConfig
from app.harness.builder import build_slotflow_harness_graph
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.steps.clarify_gate import (
    already_clarified,
    clarify_mode_enabled,
    is_fresh_user_turn,
    parse_triage,
    run_triage,
)


class _ToolAwareFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _ctx(mode: str) -> RunContext:
    return RunContext(
        thread_id="t",
        run_id="r",
        model_name="m",
        mode=mode,
        agent_name="slotflow",
        thinking_enabled=True,
        is_plan_mode=mode in ("pro", "ultra"),
        subagent_enabled=mode == "ultra",
    )


def test_clarify_mode_enabled_only_pro_ultra() -> None:
    assert clarify_mode_enabled("pro") is True
    assert clarify_mode_enabled("ultra") is True
    assert clarify_mode_enabled("flash") is False


def test_is_fresh_user_turn_detects_latest_human() -> None:
    assert is_fresh_user_turn([HumanMessage("hi")]) is True
    assert is_fresh_user_turn([AIMessage(content="x")]) is False


def test_already_clarified_after_ask_clarification_tool() -> None:
    messages = [
        HumanMessage("做个表格"),
        AIMessage(content="", tool_calls=[{"name": "ask_clarification", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="{}", name="ask_clarification", tool_call_id="c1"),
        HumanMessage("CSV"),
    ]
    assert already_clarified(messages) is True


def test_parse_triage_reads_json() -> None:
    assert parse_triage('{"actionable": false, "question": "q?"}') == {"actionable": False, "question": "q?"}
    assert parse_triage("nope") is None


@pytest.mark.asyncio
async def test_run_triage_uses_injected_triage_fn() -> None:
    async def not_needed():
        pass

    triage = await run_triage(
        messages=[HumanMessage("做个表格")],
        triage_fn=lambda text: {"actionable": False, "question": "格式?"},
    )
    assert triage == {"actionable": False, "question": "格式?"}


@pytest.mark.asyncio
async def test_run_triage_fails_open_on_none() -> None:
    triage = await run_triage(messages=[HumanMessage("x")], triage_fn=lambda text: None)
    assert triage is None


@pytest.mark.asyncio
async def test_pro_underspecified_request_pauses_with_clarification_interrupt() -> None:
    """The node graph pauses via interrupt(); resume injects the answer verbatim (no re-pop)."""

    # Force the triage to be non-actionable via monkeypatching run_triage in graph module.
    import app.harness.graph as G

    original = G.run_triage

    async def fake_triage(*, messages, model=None, triage_fn=None):
        return {
            "actionable": False,
            "clarification_type": "ambiguous_requirement",
            "question": "导出成什么格式？",
            "options": ["CSV", "Excel", "Markdown"],
        }

    G.run_triage = fake_triage
    try:
        model = _ToolAwareFake(responses=[AIMessage(content="好的，导出为 CSV。")])
        request = ChatStreamRequest(message="做个表格", mode="pro")
        bundle = build_run_config(thread_id="tg", run_id="r1", request=request)
        graph = build_slotflow_harness_graph(
            model=model,
            run_context=bundle.context,
            harness_config=SlotFlowHarnessConfig(
                system_prompt="你是测试助手。",
                middleware_config=SlotFlowMiddlewareConfig(),
            ),
            checkpointer=InMemorySaver(),
        )
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "做个表格"}]},
            config=bundle.config,
            context=bundle.context,
        )
        state = await graph.aget_state(bundle.config)
        assert state.interrupts, "underspecified pro request should pause for clarification"
        payload = state.interrupts[0].value
        assert payload["type"] == "clarification"
        assert payload["source"] == "slotflow_clarification"
        assert payload["question"] == "导出成什么格式？"
        labels = [opt["label"] for opt in payload["options"]]
        assert labels[:3] == ["CSV", "Excel", "Markdown"]
        assert "其他" in labels[-1]

        result = await graph.ainvoke(Command(resume="CSV"), config=bundle.config, context=bundle.context)
        assert result["messages"][-1].content == "好的，导出为 CSV。"
        injected = [m for m in result["messages"] if isinstance(m, HumanMessage) and m.content == "CSV"]
        assert injected, "answer should be injected verbatim as a HumanMessage"
        after = await graph.aget_state(bundle.config)
        assert not after.interrupts  # answered -> no pending interrupt -> cannot re-pop
    finally:
        G.run_triage = original
