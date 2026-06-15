"""Todo-list middleware for SlotFlow harness runs."""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState


SLOTFLOW_TODO_SYSTEM_PROMPT = """## SlotFlow todo list

You have access to `write_todos` for visible task tracking.

Use it for non-trivial multi-step work, especially when the user asks for several
changes, debugging plus verification, research followed by implementation, or any
task whose plan can change after reading files or tool results.

Operational rules:
- Create or revise the todo list before starting substantial multi-step work.
- Keep at least one item `in_progress` while work remains.
- Update the list immediately when a step starts, completes, becomes irrelevant,
  or a newly discovered step is needed.
- Do not batch status updates until the end.
- Do not use todos for simple one-step questions where tracking adds no value.

The todo list is not the final answer. After all work is complete, provide the
requested result in normal assistant text."""


def _has_write_todos_call(messages: list[Any]) -> bool:
    """Return whether visible context still contains a write_todos call."""

    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls or []:
            if tool_call.get("name") == "write_todos":
                return True
    return False


def _has_named_human_message(messages: list[Any], name: str) -> bool:
    """Return whether a reminder with the given name is already visible."""

    return any(
        isinstance(message, HumanMessage) and getattr(message, "name", None) == name
        for message in messages
    )


def _format_todos(todos: list[dict[str, Any]]) -> str:
    """Format todos for a compact model reminder."""

    return "\n".join(
        f"- [{todo.get('status', 'pending')}] {todo.get('content', '')}"
        for todo in todos
    )


class SlotFlowTodoMiddleware(TodoListMiddleware):
    """Expose write_todos and keep active todo state visible to the model."""

    def __init__(self) -> None:
        super().__init__(system_prompt=SLOTFLOW_TODO_SYSTEM_PROMPT)

    @override
    def before_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        """Remind the model about todos if the original tool call left context."""

        _ = runtime
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

    @override
    async def abefore_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)
