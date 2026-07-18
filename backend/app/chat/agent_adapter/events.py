"""SlotFlow agent 业务事件定义与构造。

这里只定义 agent 边界对外流出的业务事件形状（`AgentEvent`）、适配器协议
（`AgentAdapter`），以及构造 run 生命周期事件 / 组装 agent 输入的纯函数。不涉及
LangGraph 投影解析（见 projections.py）和异步流式消费（见 streaming.py）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.chat.models import ChatStreamRequest, RunConfigBundle


AgentEventName = Literal[
    "run.prepared",
    "run.usage",
    "context.compressing",
    "message.delta",
    "tool.delta",
    "tool.status",
    "clarification.requested",
    "todo.updated",
    "state.snapshot",
    "run.finished",
]


class AgentEvent(BaseModel):
    """SlotFlow 自己理解的 agent 流式事件。

    `event` 是业务事件名；`data` 是这个事件携带的数据。

    注意：这里没有 HTTP、SSE、FastAPI 的概念。它只是 agent 边界流出的业务对象。
    """

    event: AgentEventName
    data: dict[str, Any] = Field(default_factory=dict)


class AgentAdapter(Protocol):
    """所有 agent 适配器都要实现的最小接口。

    路由层后面只依赖这个协议，而不直接依赖 LangChain、LangGraph 或 LLM。
    测试可以在测试文件里定义自己的轻量 fake；生产代码只保留真实运行需要的 adapter。
    """

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        """根据一次聊天请求流式产出业务事件。"""


def build_agent_input(
    request: ChatStreamRequest,
    *,
    bundle: RunConfigBundle | None = None,
) -> dict[str, Any]:
    """把 SlotFlow 请求体整理成 LangChain agent 输入。

    LangChain agent 的标准输入是 `{"messages": [...]}`。这里先只放一条 user
    message。后续可以从仓库里读取 thread 历史，再把多轮 messages 拼进去。
    """

    return {
        "messages": [
            {
                "role": "user",
                "content": build_user_message_content(request=request, bundle=bundle),
            }
        ]
    }


def build_user_message_content(
    *,
    request: ChatStreamRequest,
    bundle: RunConfigBundle | None,
) -> str:
    """Make current attachments unambiguous inside the actual user message."""

    if bundle is None or not bundle.context.uploaded_files:
        return request.message

    lines = [
        request.message,
        "",
        "<slotflow-current-uploaded-files>",
        "The following files are attached to the current user message.",
        "If the user says this file/这个文件, it refers only to these current files.",
        "For file-content questions, call workspace_read(path) on the current file path before answering.",
        "Do not answer from previous uploaded files unless the user explicitly asks about history.",
    ]
    for uploaded_file in bundle.context.uploaded_files:
        display_name = uploaded_file.original_filename or uploaded_file.filename
        lines.append(
            "- "
            f"path={uploaded_file.workspace_path}; "
            f"filename={display_name}; "
            f"stored_filename={uploaded_file.filename}; "
            f"content_type={uploaded_file.content_type or 'unknown'}; "
            f"size_bytes={uploaded_file.size_bytes}"
        )
    lines.append("</slotflow-current-uploaded-files>")
    return "\n".join(lines)


def make_prepared_event(*, bundle: RunConfigBundle) -> AgentEvent:
    """创建 `run.prepared` 事件，让上层能看到本次 run 的核心配置。"""

    return AgentEvent(
        event="run.prepared",
        data={
            "thread_id": bundle.context.thread_id,
            "run_id": bundle.context.run_id,
            "model_name": bundle.context.model_name,
            "mode": bundle.context.mode,
            "agent_name": bundle.context.agent_name,
        },
    )


def make_context_compressing_event(*, bundle: RunConfigBundle) -> AgentEvent:
    """Notify the UI that LangChain is compressing history, without exposing text."""

    return AgentEvent(
        event="context.compressing",
        data={
            "thread_id": bundle.context.thread_id,
            "run_id": bundle.context.run_id,
        },
    )


def make_finished_event(*, bundle: RunConfigBundle) -> AgentEvent:
    """创建 `run.finished` 事件，表示 agent 流已经正常结束。"""

    return AgentEvent(
        event="run.finished",
        data={
            "thread_id": bundle.context.thread_id,
            "run_id": bundle.context.run_id,
        },
    )


def assistant_message_id(bundle: RunConfigBundle) -> str:
    """给一次 run 的 assistant 流式消息生成稳定 ID。"""

    return f"{bundle.context.run_id}:assistant"


async def collect_agent_events(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    """测试辅助函数：把异步事件流收集成列表。"""

    return [event async for event in stream]
