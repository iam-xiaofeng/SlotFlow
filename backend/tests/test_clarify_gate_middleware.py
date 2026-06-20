"""Tests for the clarify-gate middleware (pro/ultra first-step enforcement)."""

from __future__ import annotations

import json

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.middleware.clarify_gate_middleware import SlotFlowClarifyGateMiddleware


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


def _request(messages, ctx, *, tools=None, state=None) -> ModelRequest:
    return ModelRequest(
        model=object(),
        messages=messages,
        tools=tools or [],
        state=state or {"messages": messages},
        runtime=Runtime(context=ctx),
    )


class _Handler:
    def __init__(self) -> None:
        self.calls = 0
        self.request: ModelRequest | None = None

    async def __call__(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.request = request
        return ModelResponse(result=[AIMessage(content="real answer")])


# --- clarify gate (before_model) --------------------------------------------------------


@pytest.mark.asyncio
async def test_flash_mode_never_gates() -> None:
    gate = _gate({"actionable": False, "question": "CSV 还是 Excel?"})
    result = await gate.abefore_model({"messages": [HumanMessage("做个表格")]}, Runtime(context=_ctx("flash")))
    assert result is None


@pytest.mark.asyncio
async def test_pro_underspecified_request_ends_with_clarification() -> None:
    gate = _gate(
        {
            "actionable": False,
            "clarification_type": "ambiguous_requirement",
            "question": "导出成什么格式？",
            "options": ["CSV", "Excel", "Markdown"],
        }
    )
    result = await gate.abefore_model({"messages": [HumanMessage("做个表格")]}, Runtime(context=_ctx("pro")))

    assert result["jump_to"] == "end"
    ai_message, tool_message = result["messages"]
    assert ai_message.tool_calls[0]["name"] == "ask_clarification"
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.name == "ask_clarification"
    payload = json.loads(tool_message.content)
    assert payload["type"] == "clarification"
    assert payload["source"] == "slotflow_clarification"  # projection requires this exact source
    assert payload["question"] == "导出成什么格式？"
    assert [opt["label"] for opt in payload["options"]] == ["CSV", "Excel", "Markdown"]


@pytest.mark.asyncio
async def test_pro_actionable_request_stashes_triage_and_does_not_gate() -> None:
    gate = _gate({"actionable": True, "needs_plan": False})
    result = await gate.abefore_model(
        {"messages": [HumanMessage("把'你好'翻译成英文")], "slotflow": {"keep": 1}},
        Runtime(context=_ctx("pro")),
    )
    assert "jump_to" not in result  # actionable -> stash, not end
    assert result["slotflow"]["keep"] == 1  # does not clobber existing slotflow keys
    assert result["slotflow"]["clarify_gate_triage"]["actionable"] is True


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
    # anti-loop: actionable=False but already clarified -> stash, never a second clarification
    assert "jump_to" not in (result or {})


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


# --- ultra skill-first / plan-first directive (wrap_model_call) -------------------------


@pytest.mark.asyncio
async def test_ultra_injects_skill_directive_when_installed_skill_present() -> None:
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("帮我做一份金融分析报告")],
        _ctx("ultra"),
        tools=[{"name": "skill_match"}, {"name": "write_todos"}],
        state={
            "messages": [],
            "slotflow": {
                "skills_preflight": {"installed_matches": [{"name": "finance"}]},
                "clarify_gate_triage": {"actionable": True, "needs_plan": True},
            },
        },
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert handler.request.tool_choice is None  # forcing breaks DeepSeek thinking
    assert "skill_match" in handler.request.system_message.content


@pytest.mark.asyncio
async def test_ultra_specialized_task_injects_skill_discovery_directive() -> None:
    """Specialized task pushes skill discovery even when NO Skill is installed yet."""
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("用专业方法分析这组销量数据并出图")],
        _ctx("ultra"),
        tools=[{"name": "skill_match"}, {"name": "find-skills"}, {"name": "write_todos"}],
        state={
            "messages": [],
            "slotflow": {
                "skills_preflight": {"installed_matches": []},  # nothing installed
                "clarify_gate_triage": {"actionable": True, "specialized": True, "needs_plan": False},
            },
        },
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert handler.request.tool_choice is None
    assert "skill_match" in handler.request.system_message.content


@pytest.mark.asyncio
async def test_ultra_skill_directive_fires_when_preflight_ran_even_without_triage_flag() -> None:
    """The skills preflight running (specialized terms detected) is enough to push discovery."""
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("分析这组销量数据并出趋势图")],
        _ctx("ultra"),
        tools=[{"name": "skill_match"}, {"name": "find-skills"}],
        state={
            "messages": [],
            "slotflow": {
                "skills_preflight": {"installed_matches": [], "installable_search": {}},
                "clarify_gate_triage": {"actionable": True, "specialized": False, "needs_plan": False},
            },
        },
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert "skill_match" in handler.request.system_message.content


@pytest.mark.asyncio
async def test_ultra_injects_plan_directive_for_nontrivial_task() -> None:
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("帮我搭一个多页的产品介绍网站")],
        _ctx("ultra"),
        tools=[{"name": "skill_match"}, {"name": "write_todos"}],
        state={
            "messages": [],
            "slotflow": {
                "skills_preflight": {"installed_matches": []},
                "clarify_gate_triage": {"actionable": True, "needs_plan": True, "specialized": False},
            },
        },
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert "write_todos" in handler.request.system_message.content


@pytest.mark.asyncio
async def test_ultra_parallel_task_injects_subagent_delegation_directive() -> None:
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("分别调研 A、B、C 三家公司再对比")],
        _ctx("ultra"),
        tools=[{"name": "task_tool"}, {"name": "write_todos"}],
        state={
            "messages": [],
            "slotflow": {
                "clarify_gate_triage": {"actionable": True, "needs_subagent": True, "needs_plan": True},
            },
        },
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert "task_tool" in handler.request.system_message.content


@pytest.mark.asyncio
async def test_ultra_trivial_task_injects_no_directive() -> None:
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("1+1 等于几？")],
        _ctx("ultra"),
        tools=[{"name": "write_todos"}],
        state={"messages": [], "slotflow": {"clarify_gate_triage": {"actionable": True, "needs_plan": False}}},
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1


@pytest.mark.asyncio
async def test_pro_mode_wrap_model_call_injects_no_directive() -> None:
    gate = SlotFlowClarifyGateMiddleware()
    handler = _Handler()
    request = _request(
        [HumanMessage("帮我搭一个多页的产品介绍网站")],
        _ctx("pro"),
        tools=[{"name": "write_todos"}],
        state={"messages": [], "slotflow": {"clarify_gate_triage": {"actionable": True, "needs_plan": True}}},
    )

    await gate.awrap_model_call(request, handler)

    assert handler.calls == 1
    assert handler.request.system_message is None  # ultra-only enforcement
