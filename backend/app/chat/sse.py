"""业务事件到 SSE 文本帧的转换工具。

模块四已经把 LangGraph v3 typed projections 翻译成了 `AgentEvent`。模块五继续往
浏览器方向走一步：

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
    "message.delta",
    "tool.delta",
    "state.snapshot",
    "run.finished",
    "run.error",
]


class BusinessSseEvent(BaseModel):
    """SlotFlow 对前端公开的 SSE 事件。

    它和 `AgentEvent` 很像，但多了 `event_id`，因为 SSE 协议允许每一帧带一个 id。
    第一阶段不做断线续传，所以 `event_id` 可选。
    """

    event: SseEventName
    data: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = None


def agent_event_to_sse_event(event: AgentEvent) -> BusinessSseEvent:
    """把模块四的 agent 事件转换成前端 SSE 事件。

    当前多数事件只是原样转发。这一层仍然有意义：以后如果前端事件名和 agent
    内部事件名不完全一致，只改这里，不改 agent adapter。
    """

    return BusinessSseEvent(event=event.event, data=dict(event.data))


def make_error_event(error: BaseException) -> BusinessSseEvent:
    """把异常转换成 `run.error`。

    这里保留异常类型和消息，但不把 traceback 直接发给前端。traceback 更适合留在
    后端日志里，前端只需要知道这次 run 失败了，以及失败的大概原因。
    """

    return BusinessSseEvent(
        event="run.error",
        data={
            "name": type(error).__name__,
            "message": str(error),
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
