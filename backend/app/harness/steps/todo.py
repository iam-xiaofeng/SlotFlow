"""Step: todo-list tool, reminder, and parallel-call guard.

重构后 SlotFlow 不再用官方 ``TodoListMiddleware``（它依赖 ``AgentMiddleware`` 的
``wrap_model_call``/``after_model`` hook）。这里直接复用官方 ``write_todos`` 工具（返回
``Command(update={"todos":..., "messages":[ToolMessage]})``，ToolNode 原生支持，state 已有
``todos`` 字段），并把「活跃 todo 遗忘提醒」和「禁止并行 write_todos」抽成纯函数，
由 graph 节点调用。

**这里刻意没有「强制建 todo」的逻辑**（2026-08-14 删除，见 HARNESS_NOTES §63）。原来的
``todo_enforcement_update`` 在 ``post_model`` 里跑，触发条件是「本次 AI 消息没有 tool_calls」
——也就是**只在模型已经写完最终答案时才可能触发**，然后把这一轮路由回 ``pre_model`` 要求它
「先建 todo 列表再回答」。那个时刻早就过去了，它能做的只有把一个已完成的回合重新拽开：真机
上一句「这是什么」被拽了两次，同一个问题答了三遍。规划留给系统提示（主动引导）和模型自己判断，
图不再替模型决定什么时候算答完。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.harness.state import SlotFlowAgentState


class Todo(BaseModel):
    """A single todo item with content and status."""

    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(
        description="User-visible todo item text.",
        validation_alias=AliasChoices("content", "text"),
    )
    status: Literal["pending", "in_progress", "completed"]


class WriteTodosInput(BaseModel):
    """Input schema for the `write_todos` tool."""

    todos: list[Todo]


@tool("write_todos")
def write_todos_tool(
    todos: list[Todo], tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command[Any]:
    """Create and manage a structured task list for your current work session.

    Use it for complex multi-step objectives (3+ steps). Mark the first task
    in_progress immediately when you write the list; mark tasks completed as soon
    as they are done (do not batch). Never call it multiple times in parallel.
    The todo list tracks work; it is not the final answer.
    """

    normalized_todos = [
        todo.model_dump() if isinstance(todo, Todo) else Todo.model_validate(todo).model_dump()
        for todo in todos
    ]
    return Command(
        update={
            "todos": normalized_todos,
            "messages": [
                ToolMessage(
                    f"Updated todo list to {normalized_todos}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def todo_reminder_update(
    *,
    state: SlotFlowAgentState,
) -> str | None:
    """Return a reminder block when active todos have left the visible message context.

    Returns the control text only; ``pre_model`` folds it into the current step's
    system prompt. It must NOT become a message object in any state channel: the
    v3 messages projection streams every newly-created message object it sees
    (deduplicated by id, so replayed history stays silent but fresh control
    messages surface), and the ``messages`` channel is additionally persisted and
    replayed by the checkpointer (§29 boundary; 2026-07-15 live leak).
    """

    todos = list(state.get("todos") or [])
    if not todos:
        return None
    messages = list(state.get("messages") or [])
    if _has_write_todos_call(messages):
        return None
    return (
        "<slotflow-todo-reminder>\n"
        "The active todo list is no longer visible in the recent context, "
        "but it is still part of this run state:\n\n"
        f"{_format_todos(todos)}\n\n"
        "Continue using `write_todos` whenever item status changes.\n"
        "</slotflow-todo-reminder>"
    )


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


def _format_todos(todos: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- [{todo.get('status', 'pending')}] {todo.get('content', '')}"
        for todo in todos
    )
