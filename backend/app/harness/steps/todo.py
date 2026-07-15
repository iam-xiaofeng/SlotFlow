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
from app.harness.utils import message_content_text

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


def todo_enforcement_update(
    *,
    state: SlotFlowAgentState,
    plan_enabled: bool,
) -> dict[str, Any] | None:
    """Ask the model to create/update todos from the post-model graph node when needed.

    This is deliberately dynamic graph behavior, not a static system-prompt rule: the node
    inspects the just-produced AI message and active ``todos`` state after every model call.

    The control instruction is returned on the ``todo_enforcement`` state channel (not as a
    message object in any channel): ``pre_model`` folds ``pending`` into the retry step's
    system prompt, and ``attempted`` guards against re-injecting after the model ignored one
    request. Nothing user-facing or durable is written to the conversation history.
    """

    messages = list(state.get("messages") or [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last_ai is None or messages[-1] is not last_ai:
        return None
    if _has_write_todos_call([last_ai]):
        # The model is writing todos right now: re-arm the guard so a later
        # incomplete-todo state may enforce again.
        return _reset_enforcement_update(state)
    if last_ai.tool_calls:
        return None
    if _enforcement_already_attempted(state):
        return None

    todos = list(state.get("todos") or [])
    latest_user_request = _latest_user_text(messages)
    if not todos:
        if not _should_create_initial_todos(latest_user_request, plan_enabled=plan_enabled):
            return None
        pending = (
            "<slotflow-todo-enforcer>\n"
            "This request needs visible progress tracking. Call `write_todos` now with "
            "3-7 concrete user-visible steps and mark the first active step "
            "`in_progress`. Do not answer in prose before creating the todo list.\n"
            "</slotflow-todo-enforcer>"
        )
        return {"todo_enforcement": {"pending": pending, "attempted": True}}

    if _all_todos_completed(todos):
        return None
    pending = (
        "<slotflow-todo-enforcer>\n"
        "The active todo list is not complete. Before giving a final answer, call "
        "`write_todos` with the current statuses. Mark completed work as completed and "
        "keep exactly one current item `in_progress` when work remains.\n\n"
        f"{_format_todos(todos)}\n"
        "</slotflow-todo-enforcer>"
    )
    return {"todo_enforcement": {"pending": pending, "attempted": True}}


def consume_todo_enforcement(state: SlotFlowAgentState) -> tuple[str | None, dict[str, Any]]:
    """Read a pending enforcement instruction and return the state clear-update.

    Returns ``(pending_text, state_update)``. ``pre_model`` folds ``pending_text``
    into THIS step's system prompt (a plain str channel — never a streamable
    message object) and merges ``state_update`` to clear ``pending`` while keeping
    the ``attempted`` guard, so a single ignored enforcement never loops.
    """

    enforcement = state.get("todo_enforcement")
    if not isinstance(enforcement, dict):
        return None, {}
    pending = enforcement.get("pending")
    if not isinstance(pending, str) or not pending:
        return None, {}
    cleared = {**enforcement, "pending": None}
    return pending, {"todo_enforcement": cleared}


def route_after_model_has_enforcement(state: SlotFlowAgentState) -> bool:
    """Return true when post_model queued a todo enforcement retry for pre_model."""

    enforcement = state.get("todo_enforcement")
    return (
        isinstance(enforcement, dict)
        and isinstance(enforcement.get("pending"), str)
        and bool(enforcement.get("pending"))
    )


def _enforcement_already_attempted(state: SlotFlowAgentState) -> bool:
    """One ignored enforcement blocks further attempts until write_todos re-arms it.

    ``attempted`` is set when an enforcement instruction is queued and reset by
    ``todo_enforcement_update`` the moment the model actually calls ``write_todos``
    (see ``_reset_enforcement_update``). Pure flag semantics — no history scanning.
    """

    enforcement = state.get("todo_enforcement")
    return isinstance(enforcement, dict) and bool(enforcement.get("attempted"))


def _reset_enforcement_update(state: SlotFlowAgentState) -> dict[str, Any] | None:
    enforcement = state.get("todo_enforcement")
    if isinstance(enforcement, dict) and (
        enforcement.get("attempted") or enforcement.get("pending")
    ):
        return {"todo_enforcement": {"pending": None, "attempted": False}}
    return None


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
            return _strip_slotflow_injected_context(message_content_text(message.content))
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
