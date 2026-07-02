"""Step: todo-list tool, reminder, post-model enforcement, and parallel-call guard.

重构后 SlotFlow 不再用官方 ``TodoListMiddleware``（它依赖 ``AgentMiddleware`` 的
``wrap_model_call``/``after_model`` hook）。这里直接复用官方 ``write_todos`` 工具（返回
``Command(update={"todos":..., "messages":[ToolMessage]})``，ToolNode 原生支持，state 已有
``todos`` 字段），并把「活跃 todo 遗忘提醒」「缺失/过期 todo 的后置节点约束」和
「禁止并行 write_todos」抽成纯函数，由 graph 节点调用。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.harness.state import SlotFlowAgentState

TODO_ENFORCER_MESSAGE_NAME = "slotflow_todo_enforcer"
TODO_REMINDER_MESSAGE_NAME = "slotflow_todo_reminder"

_TODO_EXPLICIT_MARKERS = (
    "todo",
    "to-do",
    "待办",
    "任务列表",
    "write_todos",
    "write todos",
)
_TODO_COMPLEX_TASK_MARKERS = (
    "实现",
    "修复",
    "优化",
    "重构",
    "检查",
    "排查",
    "测试",
    "验证",
    "新增",
    "添加",
    "设计",
    "分析",
    "调研",
    "对比",
    "整理",
    "报告",
    "计划",
    "方案",
    "整个",
    "全部",
    "前端",
    "后端",
    "链路",
    "issue",
    "commit",
    "implement",
    "fix",
    "debug",
    "refactor",
    "test",
    "verify",
    "analyze",
    "research",
    "compare",
    "plan",
    "report",
)


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
) -> dict[str, Any] | None:
    """Inject a reminder when active todos have left the visible message context."""

    todos = list(state.get("todos") or [])
    if not todos:
        return None
    messages = list(state.get("messages") or [])
    if _has_write_todos_call(messages):
        return None
    if _has_named_human_message(messages, TODO_REMINDER_MESSAGE_NAME):
        return None
    reminder = HumanMessage(
        name=TODO_REMINDER_MESSAGE_NAME,
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


def todo_enforcement_update(
    *,
    state: SlotFlowAgentState,
    plan_enabled: bool,
) -> dict[str, Any] | None:
    """Ask the model to create/update todos from the post-model graph node when needed.

    This is deliberately dynamic graph behavior, not a static system-prompt rule: the node
    inspects the just-produced AI message and active ``todos`` state after every model call.
    """

    messages = list(state.get("messages") or [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last_ai is None or messages[-1] is not last_ai:
        return None
    if _has_write_todos_call([last_ai]):
        return None
    if last_ai.tool_calls:
        return None
    if _has_named_human_message_since_last_write_todos(messages, TODO_ENFORCER_MESSAGE_NAME):
        return None

    todos = list(state.get("todos") or [])
    latest_user_request = _latest_user_text(messages)
    if not todos:
        if not _should_create_initial_todos(latest_user_request, plan_enabled=plan_enabled):
            return None
        message = HumanMessage(
            name=TODO_ENFORCER_MESSAGE_NAME,
            content=(
                "<slotflow-todo-enforcer>\n"
                "This request needs visible progress tracking. Call `write_todos` now with "
                "3-7 concrete user-visible steps and mark the first active step "
                "`in_progress`. Do not answer in prose before creating the todo list.\n"
                "</slotflow-todo-enforcer>"
            ),
        )
        return {"messages": [message]}

    if _all_todos_completed(todos):
        return None
    message = HumanMessage(
        name=TODO_ENFORCER_MESSAGE_NAME,
        content=(
            "<slotflow-todo-enforcer>\n"
            "The active todo list is not complete. Before giving a final answer, call "
            "`write_todos` with the current statuses. Mark completed work as completed and "
            "keep exactly one current item `in_progress` when work remains.\n\n"
            f"{_format_todos(todos)}\n"
            "</slotflow-todo-enforcer>"
        ),
    )
    return {"messages": [message]}


def latest_message_is_todo_enforcer(state: SlotFlowAgentState) -> bool:
    """Return true when post_model just appended the todo enforcement control message."""

    messages = list(state.get("messages") or [])
    if not messages:
        return False
    message = messages[-1]
    return (
        isinstance(message, HumanMessage)
        and getattr(message, "name", None) == TODO_ENFORCER_MESSAGE_NAME
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


def _has_named_human_message(messages: list[Any], name: str) -> bool:
    return any(
        isinstance(message, HumanMessage) and getattr(message, "name", None) == name
        for message in messages
    )


def _has_named_human_message_since_last_write_todos(messages: list[Any], name: str) -> bool:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls or []:
                if tool_call.get("name") == "write_todos":
                    return False
        if isinstance(message, HumanMessage) and getattr(message, "name", None) == name:
            return True
    return False


def _looks_like_todo_worthy_request(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return False
    if len(normalized) >= 80:
        return True
    return any(marker in normalized for marker in _TODO_COMPLEX_TASK_MARKERS)


def _should_create_initial_todos(text: str, *, plan_enabled: bool) -> bool:
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in _TODO_EXPLICIT_MARKERS):
        return True
    return plan_enabled and _looks_like_todo_worthy_request(normalized)


def _all_todos_completed(todos: list[dict[str, Any]]) -> bool:
    return bool(todos) and all(todo.get("status") == "completed" for todo in todos)


def _latest_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and getattr(message, "name", None) is None:
            content = getattr(message, "content", "")
            text = content if isinstance(content, str) else str(content)
            return _strip_slotflow_injected_context(text)
    return ""


def _strip_slotflow_injected_context(text: str) -> str:
    """Return the user's original request after SlotFlow-injected XML context blocks."""

    remaining = text.strip()
    while remaining.startswith("<slotflow-"):
        close_start = remaining.find("</slotflow-")
        if close_start == -1:
            break
        close_end = remaining.find(">", close_start)
        if close_end == -1:
            break
        remaining = remaining[close_end + 1 :].lstrip()
    return remaining


def _format_todos(todos: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- [{todo.get('status', 'pending')}] {todo.get('content', '')}"
        for todo in todos
    )
