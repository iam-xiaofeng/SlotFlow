"""Tests for the clarify-gate middleware (pro/ultra first-step clarify enforcement)."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.middleware.clarify_gate_middleware import SlotFlowClarifyGateMiddleware


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


def _gate(triage_value) -> SlotFlowClarifyGateMiddleware:
    return SlotFlowClarifyGateMiddleware(triage=lambda _text: triage_value)


# --- clarify gate (before_model) --------------------------------------------------------


@pytest.mark.asyncio
async def test_flash_mode_never_gates() -> None:
    gate = _gate({"actionable": False, "question": "CSV 还是 Excel?"})
    result = await gate.abefore_model({"messages": [HumanMessage("做个表格")]}, Runtime(context=_ctx("flash")))
    assert result is None


@pytest.mark.asyncio
async def test_pro_underspecified_request_pauses_with_clarification_interrupt() -> None:
    """The gate pauses the real graph via interrupt(), carrying the clarification payload; the
    user's answer resumes the run and is injected so the model proceeds (no jump_to=end, no
    synthesized tool message that could re-pop)."""

    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    gate = _gate(
        {
            "actionable": False,
            "clarification_type": "ambiguous_requirement",
            "question": "导出成什么格式？",
            "options": ["CSV", "Excel", "Markdown"],
        }
    )
    model = _ToolAwareFake(responses=[AIMessage(content="好的，导出为 CSV。")])
    graph = create_agent(model=model, tools=[], middleware=[gate], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "tg"}}
    # RunContext drives the gate's mode check; create_agent forwards context via the runtime.
    ctx = _ctx("pro")

    await graph.ainvoke({"messages": [{"role": "user", "content": "做个表格"}]}, config=config, context=ctx)
    state = await graph.aget_state(config)
    assert state.interrupts, "underspecified pro request should pause for clarification"
    payload = state.interrupts[0].value
    assert payload["type"] == "clarification"
    assert payload["source"] == "slotflow_clarification"  # projection requires this exact source
    assert payload["question"] == "导出成什么格式？"
    labels = [opt["label"] for opt in payload["options"]]
    assert labels[:3] == ["CSV", "Excel", "Markdown"]
    assert "其他" in labels[-1]  # free-text escape always appended last

    # Answer resumes the run; the answer is injected verbatim as the user's message (no
    # meta-frame, so the model won't echo a wrapper) and the model produces its final reply.
    result = await graph.ainvoke(Command(resume="CSV"), config=config, context=ctx)
    assert result["messages"][-1].content == "好的，导出为 CSV。"
    injected = [m for m in result["messages"] if isinstance(m, HumanMessage) and m.content == "CSV"]
    assert injected, "the user's answer should be injected verbatim as a HumanMessage"
    after = await graph.aget_state(config)
    assert not after.interrupts  # answered -> no pending interrupt -> cannot re-pop


@pytest.mark.asyncio
async def test_actionable_request_does_not_gate() -> None:
    gate = _gate({"actionable": True})
    result = await gate.abefore_model(
        {"messages": [HumanMessage("把'你好'翻译成英文")], "slotflow": {"keep": 1}},
        Runtime(context=_ctx("pro")),
    )
    # Actionable -> let the model run; the gate makes no state change.
    assert result is None


@pytest.mark.asyncio
async def test_does_not_re_clarify_after_a_prior_clarification() -> None:
    gate = _gate({"actionable": False, "question": "再问一次？"})
    messages = [
        HumanMessage("做个表格"),
        AIMessage(content="", tool_calls=[{"name": "ask_clarification", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="{}", name="ask_clarification", tool_call_id="c1"),
        HumanMessage("CSV"),
    ]
    result = await gate.abefore_model({"messages": messages}, Runtime(context=_ctx("pro")))
    # anti-loop: actionable=False but already clarified -> never a second clarification
    assert result is None


@pytest.mark.asyncio
async def test_not_fresh_turn_does_not_gate() -> None:
    gate = _gate({"actionable": False, "question": "?"})
    messages = [
        HumanMessage("做个表格"),
        AIMessage(content="", tool_calls=[{"name": "some_tool", "args": {}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="result", name="some_tool", tool_call_id="1"),
    ]
    result = await gate.abefore_model({"messages": messages}, Runtime(context=_ctx("pro")))
    assert result is None


@pytest.mark.asyncio
async def test_triage_failure_fails_open() -> None:
    gate = _gate(None)
    result = await gate.abefore_model({"messages": [HumanMessage("做个表格")]}, Runtime(context=_ctx("pro")))
    assert result is None
