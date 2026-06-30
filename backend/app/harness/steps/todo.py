"""Step: todo-list tool, system prompt, reminder, and parallel-call guard.

重构后 SlotFlow 不再用官方 ``TodoListMiddleware``（它依赖 ``AgentMiddleware`` 的
``wrap_model_call``/``after_model`` hook）。这里直接复用官方 ``write_todos`` 工具（返回
``Command(update={"todos":..., "messages":[ToolMessage]})``，ToolNode 原生支持，state 已有
``todos`` 字段），并把官方中间件的两段逻辑——system prompt 注入与「禁止并行 write_todos」
守卫——抽成纯函数，由 graph 节点调用。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import BaseModel

from app.harness.state import SlotFlowAgentState

SLOTFLOW_TODO_SYSTEM_PROMPT = """## `write_todos`

You have access to the `write_todos` tool to help you manage and plan complex objectives.
Use this tool for complex objectives to ensure that you are tracking each necessary step.
This tool is very helpful for planning complex objectives, and for breaking down these larger complex objectives into smaller steps.

It is critical that you mark todos as completed as soon as you are done with a step. Do not batch up multiple steps before marking them as completed.
For simple objectives that only require a few steps, it is better to just complete the objective directly and NOT use this tool.
Writing todos takes time and tokens, use it when it is helpful for managing complex many-step problems! But not for simple few-step requests.

## Important To-Do List Usage Notes to Remember

- The `write_todos` tool should never be called multiple times in parallel.
- Don't be afraid to revise the To-Do list as you go. New information may reveal new tasks that need to be done, or old tasks that are irrelevant.

## Finishing a task

When you finish all work, write your final answer in the message AFTER your last `write_todos` call — not in the same turn as that call. Start the final message with the substantive content the user asked for — the data, computation, summary, or analysis. The user wants the result, not confirmation that the work is done."""


class Todo(BaseModel):
    """A single todo item with content and status."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


class WriteTodosInput(BaseModel):
    """Input schema for the `write_todos` tool."""

    todos: list[Todo]


@tool("write_todos")
def write_todos_tool(
    todos: list[dict[str, Any]], tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command[Any]:
    """Create and manage a structured task list for your current work session.

    Use it for complex multi-step objectives (3+ steps). Mark the first task
    in_progress immediately when you write the list; mark tasks completed as soon
    as they are done (do not batch). Never call it multiple times in parallel.
    The todo list tracks work; it is not the final answer.
    """

    return Command(
        update={
            "todos": todos,
            "messages": [
                ToolMessage(f"Updated todo list to {todos}", tool_call_id=tool_call_id)
            ],
        }
    )


def todo_reminder_update(
    *,
    state: SlotFlowAgentState,
) -> dict[str, Any] | None:
    """Inject a reminder when active todos have left the visible message context."""

    todos = list(state.get("todos") or [])
    if not todos:
        return None
    messages = list(state.get("messages") or [])
    if _has_write_todos_call(messages):
        return None
    if _has_named_human_message(messages, "slotflow_todo_reminder"):
        return None
    reminder = HumanMessage(
        name="slotflow_todo_reminder",
        content=(
            "<slotflow-todo-reminder>\n"
            "The active todo list is no longer visible in the recent context, "
            "but it is still part of this run state:\n\n"
            f"{_format_todos(todos)}\n\n"
            "Continue using `write_todos` whenever item status changes.\n"
            "</slotflow-todo-reminder>"
        ),
    )
    return {"messages": [reminder]}


def todo_parallel_call_guard(
    *,
    state: SlotFlowAgentState,
) -> dict[str, Any] | None:
    """Reject multiple parallel ``write_todos`` calls (official middleware parity)."""

    messages = list(state.get("messages") or [])
    if not messages:
        return None
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if not last_ai or not last_ai.tool_calls:
        return None
    write_calls = [tc for tc in last_ai.tool_calls if tc.get("name") == "write_todos"]
    if len(write_calls) <= 1:
        return None
    return {
        "messages": [
            ToolMessage(
                content=(
                    "Error: The `write_todos` tool should never be called multiple times "
                    "in parallel. Please call it only once per model invocation to update "
                    "the todo list."
                ),
                tool_call_id=tc.get("id", ""),
                status="error",
            )
            for tc in write_calls
        ]
    }


def _has_write_todos_call(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls or []:
            if tool_call.get("name") == "write_todos":
                return True
    return False


def _has_named_human_message(messages: list[Any], name: str) -> bool:
    return any(
        isinstance(message, HumanMessage) and getattr(message, "name", None) == name
        for message in messages
    )


def _format_todos(todos: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- [{todo.get('status', 'pending')}] {todo.get('content', '')}"
        for todo in todos
    )
