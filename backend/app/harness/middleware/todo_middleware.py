"""Todo-list middleware for SlotFlow harness runs.

The ``write_todos`` tool + system prompt stay owned by the ``TodoListMiddleware`` base (it
provides the tool). The reminder logic delegates to ``app.harness.steps.todo``.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import TodoListMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.state import SlotFlowAgentState
from app.harness.steps.todo import SLOTFLOW_TODO_SYSTEM_PROMPT, todo_reminder_update


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
        _ = runtime
        return todo_reminder_update(state=state)

    @override
    async def abefore_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)
