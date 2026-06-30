"""Tests for the subagent concurrency-cap step (formerly middleware).

重构后逻辑在 app/harness/steps/subagent_limit.py::cap_subagent_calls。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.harness.steps.subagent_limit import cap_subagent_calls


def _task_call(i: int) -> dict:
    return {"name": "task_tool", "args": {"agent_name": "researcher", "task": f"t{i}"}, "id": f"task{i}", "type": "tool_call"}


def _state(msg):
    return {"messages": [HumanMessage("go"), msg]}


def test_does_not_touch_when_within_limit() -> None:
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2)])
    assert cap_subagent_calls(state=_state(msg), max_concurrent=3) is None


def test_truncates_excess_task_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[_task_call(1), _task_call(2), _task_call(3), _task_call(4)],
        additional_kwargs={
            "reasoning_content": "thinking",
            "tool_calls": [{"id": f"task{i}", "function": {"name": "task_tool"}} for i in (1, 2, 3, 4)],
        },
    )
    result = cap_subagent_calls(state=_state(msg), max_concurrent=2)
    trimmed = result["messages"][0]
    assert [tc["id"] for tc in trimmed.tool_calls] == ["task1", "task2"]
    assert [tc["id"] for tc in trimmed.additional_kwargs["tool_calls"]] == ["task1", "task2"]
    assert trimmed.additional_kwargs["reasoning_content"] == "thinking"


def test_keeps_non_task_tool_calls() -> None:
    other = {"name": "web_search", "args": {"query": "x"}, "id": "w1", "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2), other])
    result = cap_subagent_calls(state=_state(msg), max_concurrent=1)
    ids = [tc["id"] for tc in result["messages"][0].tool_calls]
    assert ids == ["task1", "w1"]


def test_ignores_non_ai_or_toolless_messages() -> None:
    assert cap_subagent_calls(state={"messages": [HumanMessage("hi")]}, max_concurrent=1) is None
    assert cap_subagent_calls(state={"messages": [AIMessage(content="done")]}, max_concurrent=1) is None
