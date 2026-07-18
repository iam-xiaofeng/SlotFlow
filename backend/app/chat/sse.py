"""业务事件到 SSE 文本帧的转换工具。

```txt
AgentEvent
-> BusinessSseEvent
-> "event: ...\ndata: ...\n\n"
```

这一层仍然不依赖 FastAPI。它只是定义“前端到底会收到哪些 SSE event 名字”和
“每一帧怎么编码”。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.chat.agent_adapter import AgentEvent


SseEventName = Literal[
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
    "run.error",
]


class BusinessSseEvent(BaseModel):
    """SlotFlow 对前端公开的 SSE 事件。

    它和 `AgentEvent` 很像，但多了 `event_id`，因为 SSE 协议允许每一帧带一个 id。
    当前不做断线续传，所以 `event_id` 可选。
    """

    event: SseEventName
    data: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None


def agent_event_to_sse_event(event: AgentEvent) -> BusinessSseEvent:
    """把 agent 事件转换成前端 SSE 事件。

    当前多数事件只是原样转发。这一层仍然有意义：以后如果前端事件名和 agent
    内部事件名不完全一致，只改这里，不改 agent adapter。
    """

    return BusinessSseEvent(event=event.event, data=dict(event.data))


def unwrap_exception_group(error: BaseException) -> BaseException:
    """Return the first informative leaf from nested task-group failures."""

    if not isinstance(error, BaseExceptionGroup):
        return error

    fallback: BaseException = error
    for nested in error.exceptions:
        leaf = unwrap_exception_group(nested)
        fallback = leaf
        if str(leaf).strip():
            return leaf
    return fallback


def make_error_event(error: BaseException) -> BusinessSseEvent:
    """把异常转换成 `run.error`。

    LangGraph/AnyIO 的并发投影会用 ``ExceptionGroup`` 包住真正异常。最外层字符串只有
    ``unhandled errors in a TaskGroup``，对用户和 run 记录都没有诊断价值，因此先递归取
    第一个带消息的叶子异常。traceback 仍不直接发给前端。
    """

    display_error = unwrap_exception_group(error)
    message = str(display_error).strip() or type(display_error).__name__
    return BusinessSseEvent(
        event="run.error",
        data={
            "name": type(display_error).__name__,
            "message": message,
        },
    )


def encode_sse_event(item: BusinessSseEvent) -> str:
    """把业务事件编码成标准 SSE 文本帧。

    一帧 SSE 长这样：

    ```txt
    event: message.delta
    data: {"delta":"你好"}

    ```

    最后的空行代表这一帧结束。`ensure_ascii=False` 是为了让中文在测试输出和浏览器
    Network 面板里保持可读。
    """

    lines: list[str] = []
    if item.event_id is not None:
        lines.append(f"id: {item.event_id}")

    lines.append(f"event: {item.event}")
    lines.append(f"data: {json.dumps(item.data, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


async def iter_business_events(
    events: AsyncIterable[AgentEvent],
) -> AsyncIterator[BusinessSseEvent]:
    """消费 agent 业务事件流，产出前端 SSE 事件。

    如果上游 adapter 抛异常，这里会补一条 `run.error`。这样 FastAPI 路由可以继续
    用统一的方式 yield SSE frame，而不是在路由里混入大量异常转换逻辑。
    """

    try:
        async for event in events:
            yield agent_event_to_sse_event(event)
    except Exception as exc:
        yield make_error_event(exc)


async def iter_sse_frames(events: AsyncIterable[AgentEvent]) -> AsyncIterator[str]:
    """把 `AgentEvent` 异步流直接转换成 SSE 文本帧。"""

    async for event in iter_business_events(events):
        yield encode_sse_event(event)
