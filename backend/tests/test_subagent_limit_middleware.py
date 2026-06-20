"""Tests for the subagent concurrency-cap middleware."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.middleware.subagent_limit_middleware import (
    SlotFlowSubagentLimitMiddleware,
)


def _ctx() -> RunContext:
    return RunContext(
        thread_id="t",
        run_id="r",
        model_name="m",
        mode="ultra",
        agent_name="slotflow",
        thinking_enabled=True,
        is_plan_mode=True,
        subagent_enabled=True,
    )


def _task_call(i: int) -> dict:
    return {"name": "task_tool", "args": {"agent_name": "researcher", "task": f"t{i}"}, "id": f"task{i}", "type": "tool_call"}


def test_does_not_touch_when_within_limit() -> None:
    mw = SlotFlowSubagentLimitMiddleware(max_concurrent=3)
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2)])
    result = mw.after_model({"messages": [HumanMessage("go"), msg]}, Runtime(context=_ctx()))
    assert result is None


def test_truncates_excess_task_calls() -> None:
    mw = SlotFlowSubagentLimitMiddleware(max_concurrent=2)
    msg = AIMessage(
        content="",
        tool_calls=[_task_call(1), _task_call(2), _task_call(3), _task_call(4)],
        additional_kwargs={
            "reasoning_content": "thinking",
            "tool_calls": [{"id": f"task{i}", "function": {"name": "task_tool"}} for i in (1, 2, 3, 4)],
        },
    )
    result = mw.after_model({"messages": [HumanMessage("go"), msg]}, Runtime(context=_ctx()))

    trimmed = result["messages"][0]
    assert [tc["id"] for tc in trimmed.tool_calls] == ["task1", "task2"]
    # raw OpenAI tool_calls trimmed in sync; reasoning_content preserved for DeepSeek history
    assert [tc["id"] for tc in trimmed.additional_kwargs["tool_calls"]] == ["task1", "task2"]
    assert trimmed.additional_kwargs["reasoning_content"] == "thinking"


def test_keeps_non_task_tool_calls() -> None:
    mw = SlotFlowSubagentLimitMiddleware(max_concurrent=1)
    other = {"name": "web_search", "args": {"query": "x"}, "id": "w1", "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2), other])
    result = mw.after_model({"messages": [HumanMessage("go"), msg]}, Runtime(context=_ctx()))

    ids = [tc["id"] for tc in result["messages"][0].tool_calls]
    assert ids == ["task1", "w1"]  # only one task kept, web_search untouched


def test_ignores_non_ai_or_toolless_messages() -> None:
    mw = SlotFlowSubagentLimitMiddleware(max_concurrent=1)
    assert mw.after_model({"messages": [HumanMessage("hi")]}, Runtime(context=_ctx())) is None
    assert mw.after_model({"messages": [AIMessage(content="done")]}, Runtime(context=_ctx())) is None
