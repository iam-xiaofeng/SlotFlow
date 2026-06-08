"""LangGraph v3 event streaming 的业务适配层。

模块四开始接近真实 agent，但它仍然不碰 FastAPI，也不直接产出 SSE。

这一层只做一件事：把 LangGraph / LangChain agent 的 v3 typed projections
整理成 SlotFlow 自己的 `AgentEvent`。

为什么要多这一层？

真实 agent 的输出会随着工具、模型、LangGraph 版本变得很丰富。前端不应该直接认识
`GraphRunStream.messages`、`GraphRunStream.values` 这些内部投影对象，否则每次底层
变化都会传导到浏览器。SlotFlow 先把底层事件翻译成少量业务事件：

- `run.prepared`：后端已经创建 run，准备调用 agent；
- `message.delta`：assistant 正在流出文本片段；
- `tool.delta`：工具调用相关片段；
- `state.snapshot`：本次 run 的状态快照；
- `run.finished`：agent 已经完成。

模块五会继续把这些 `AgentEvent` 编码成 SSE 文本帧。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.chat.models import ChatStreamRequest, RunConfigBundle


AgentEventName = Literal[
    "run.prepared",
    "message.delta",
    "tool.delta",
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
    这样测试可以换成稳定的本地 adapter，真实运行再换成 LangGraph adapter。
    """

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        """根据一次聊天请求流式产出业务事件。"""


class StaticProjectionAgentAdapter:
    """测试和学习用的 v3 投影模拟器。

    它不调用模型，也不访问网络。它模拟的是“v3 投影之后的形状”，不是旧模块四里的
    `("messages", chunk)` stream_mode 元组。这样模块五和模块六从一开始就沿着真实
    LangGraph v3 event streaming 的方向学习。
    """

    def __init__(self, *, answer_prefix: str = "SlotFlow 收到") -> None:
        self._answer_prefix = answer_prefix

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        """产出一组稳定、可测试的业务事件。

        输出顺序模拟真实聊天链路：

        1. `run.prepared` 说明 run 配置已经整理好；
        2. 多条 `message.delta` 模拟 assistant 文本逐段流出；
        3. `state.snapshot` 给前端一份最终状态；
        4. `run.finished` 表示这次 agent 执行结束。
        """

        yield make_prepared_event(bundle=bundle)

        answer = self._build_answer(request=request, bundle=bundle)
        message_id = assistant_message_id(bundle)

        for index, delta in enumerate(split_text(answer)):
            yield AgentEvent(
                event="message.delta",
                data={
                    "message_id": message_id,
                    "role": "assistant",
                    "delta": delta,
                    "index": index,
                },
            )

        yield AgentEvent(
            event="state.snapshot",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
                "messages": [
                    {
                        "id": message_id,
                        "role": "assistant",
                        "content": answer,
                    }
                ],
                # 这里是教学模拟，不是 LangGraph 真实 values 输出。结构故意保持成
                # “graph state 快照”的样子，避免让它看起来像 RunContext 本身。
                "state": {
                    "messages": [
                        {
                            "id": message_id,
                            "role": "assistant",
                            "content": answer,
                        }
                    ],
                    "model_name": bundle.context.model_name,
                    "mode": bundle.context.mode,
                    "thinking_enabled": bundle.context.thinking_enabled,
                    "is_plan_mode": bundle.context.is_plan_mode,
                    "subagent_enabled": bundle.context.subagent_enabled,
                    "files": list(bundle.context.files),
                    "uploaded_files": [
                        uploaded_file.model_dump(mode="json")
                        for uploaded_file in bundle.context.uploaded_files
                    ],
                },
            },
        )
        yield make_finished_event(bundle=bundle)

    def _build_answer(self, *, request: ChatStreamRequest, bundle: RunConfigBundle) -> str:
        """拼出稳定回答，方便测试看清 request/context 是否真的进入 agent。"""

        file_note = f"，并收到 {len(bundle.context.files)} 个文件" if bundle.context.files else ""
        return (
            f"{self._answer_prefix}：{request.message}{file_note}。"
            f" model={bundle.context.model_name}, mode={bundle.context.mode}, "
            f"thread={bundle.context.thread_id}, run={bundle.context.run_id}。"
        )


class LangGraphEventAgentAdapter:
    """真实 LangGraph / LangChain agent 的 v3 event streaming 适配器。

    这个类接收一个已经构造好的 agent graph。它要求 graph 支持
    `astream_events(..., version="v3")`，并优先通过 v3 的 typed projections 取数据。

    SlotFlow 目前只消费 `messages`、`values`、`tool_calls` 三类事件。`output` 属于
    终态方法，不适合一边流一边读；等事件流结束后再通过 `run.finished` 表达完成。
    """

    def __init__(self, graph: Any, *, prefer_projection_stream: bool = True) -> None:
        self._graph = graph
        self._prefer_projection_stream = prefer_projection_stream

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        """调用真实 graph，并把 v3 投影逐条转换成 `AgentEvent`。"""

        yield make_prepared_event(bundle=bundle)

        stream_input = build_agent_input(request)

        if self._prefer_projection_stream:
            projection_stream = await self._graph.astream_events(
                stream_input,
                config=bundle.config,
                version="v3",
                context=bundle.context,
            )
            yielded_projection_event = False
            async for event in iter_projection_agent_events(projection_stream, bundle=bundle):
                yielded_projection_event = True
                yield event

            if yielded_projection_event:
                yield make_finished_event(bundle=bundle)
                return

        run_stream = await self._graph.astream_events(
            stream_input,
            config=bundle.config,
            version="v3",
            context=bundle.context,
        )

        # 当前异步 v3 run stream 暴露了 `stream.messages` / `stream.values` /
        # `stream.tool_calls` 这些 typed projections，但没有同步 lane 的
        # `interleave()` / 异步版 `ainterleave()`。如果分别消费多个 projection，
        # 就无法稳定恢复 LangGraph 原始事件顺序，所以这里保留 raw protocol log
        # fallback 作为顺序保真路径。
        async for raw_event in run_stream:
            event = protocol_event_to_agent_event(raw_event, bundle=bundle)
            if event is not None:
                yield event

        yield make_finished_event(bundle=bundle)

@dataclass(slots=True)
class ProjectionEnvelope:
    """记录一条 projection item 来自哪个 v3 projection。"""

    projection: str
    item: Any


async def iter_projection_agent_events(
    run_stream: Any,
    *,
    bundle: RunConfigBundle,
) -> AsyncIterator[AgentEvent]:
    """优先消费官方 v3 projections，并映射成 SlotFlow AgentEvent。

    当前异步 v3 API 没有官方 `ainterleave()`，所以这里只能并发拉取 projection
    channel，再按“谁先产出谁先发”的近似顺序输出。若三个 channel 都没有产出任何可映射
    事件，调用方会退回 raw protocol fallback。
    """

    channels = projection_channels(run_stream)
    if not channels:
        return

    queue: asyncio.Queue[ProjectionEnvelope | BaseException | object] = asyncio.Queue()
    done_sentinel = object()
    latest_snapshot: AgentEvent | None = None

    async def pump_projection(projection: str, channel: Any) -> None:
        nonlocal latest_snapshot
        try:
            async for item in channel:
                if projection == "values":
                    latest_snapshot = projection_item_to_agent_event(
                        projection=projection,
                        item=item,
                        bundle=bundle,
                    )
                    continue
                if projection == "messages":
                    async for message_item in flatten_message_projection_items(item):
                        await queue.put(ProjectionEnvelope(projection=projection, item=message_item))
                else:
                    await queue.put(ProjectionEnvelope(projection=projection, item=item))
        except Exception as exc:  # pragma: no cover - surfaced to caller
            await queue.put(exc)
        finally:
            await queue.put(done_sentinel)

    tasks = [
        asyncio.create_task(pump_projection(projection, channel))
        for projection, channel in channels
    ]

    remaining = len(tasks)
    try:
        while remaining:
            item = await queue.get()
            if item is done_sentinel:
                remaining -= 1
                continue
            if isinstance(item, BaseException):
                raise item
            if not isinstance(item, ProjectionEnvelope):
                continue

            event = projection_item_to_agent_event(
                projection=item.projection,
                item=item.item,
                bundle=bundle,
            )
            if event is not None:
                yield event
        if latest_snapshot is not None:
            yield latest_snapshot
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def projection_channels(run_stream: Any) -> list[tuple[str, Any]]:
    """收集当前 async run stream 上可用的核心 v3 projection channel。"""

    channels: list[tuple[str, Any]] = []
    for name in ("messages", "values", "tool_calls"):
        channel = getattr(run_stream, name, None)
        if channel is not None and hasattr(channel, "__aiter__"):
            channels.append((name, channel))
    return channels


async def flatten_message_projection_items(item: Any) -> AsyncIterator[Any]:
    """把 messages projection 展开成可直接映射的 item。

    `AsyncGraphRunStream.messages` 会先给出按节点分组的消息子流；子流内部才是真正的
    message-start / content-block-delta / ... 事件。这里把它压平成线性 item，保持
    AgentEvent 适配层只面对“message item -> message.delta”的职责。
    """

    if hasattr(item, "__aiter__"):
        async for nested in item:
            yield nested
        return

    if isinstance(item, tuple) and item and hasattr(item[0], "__aiter__"):
        async for nested in item[0]:
            yield nested
        return

    if isinstance(item, list):
        for nested in item:
            yield nested
        return

    yield item


def build_agent_input(request: ChatStreamRequest) -> dict[str, Any]:
    """把 SlotFlow 请求体整理成 LangChain agent 输入。

    LangChain agent 的标准输入是 `{"messages": [...]}`。这里先只放一条 user
    message。后续模块六会从仓库里读取 thread 历史，再把多轮 messages 拼进去。
    """

    return {
        "messages": [
            {
                "role": "user",
                "content": request.message,
            }
        ]
    }


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


def split_text(text: str) -> list[str]:
    """把文本切成适合测试观察的短片段。

    真模型按 token 流出，边界不稳定；学习测试更需要稳定可读，所以这里按中文标点
    和英文句号切分。
    """

    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in "，。.!?":
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def projection_item_to_agent_event(
    *,
    projection: str,
    item: Any,
    bundle: RunConfigBundle,
) -> AgentEvent | None:
    """把一条 v3 projection item 转成 SlotFlow 业务事件。

    真实 LangGraph 投影的 item 可能是 dict，也可能是消息对象。这里用一组小 helper
    提取必要字段，而不是在主流程里写一大堆类型判断。
    """

    if projection == "messages":
        delta = extract_message_delta(item)
        if not delta:
            return None
        return AgentEvent(
            event="message.delta",
            data={
                "message_id": assistant_message_id(bundle),
                "role": "assistant",
                "delta": delta,
                "index": None,
            },
        )

    if projection == "values":
        return AgentEvent(
            event="state.snapshot",
            data=normalize_values_snapshot(item=item, bundle=bundle),
        )

    if projection == "tool_calls":
        return AgentEvent(
            event="tool.delta",
            data=normalize_mapping(item),
        )

    return None


def protocol_event_to_agent_event(event: Any, *, bundle: RunConfigBundle) -> AgentEvent | None:
    """把 v3 protocol event 转成 SlotFlow 业务事件。

    `astream_events(..., version="v3")` 的异步返回值可以直接迭代主事件日志。每条日志
    大致长这样：

    ```py
    {
        "method": "messages",
        "params": {
            "data": ...
        }
    }
    ```

    我们只取 `method` 和 `params.data`，再复用 projection 映射函数。
    """

    if not isinstance(event, dict):
        return None

    method = event.get("method")
    params = event.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return None

    return projection_item_to_agent_event(
        projection=method,
        item=params.get("data"),
        bundle=bundle,
    )


def extract_message_delta(item: Any) -> str:
    """从 message projection item 中提取文本增量。

    LangGraph v3 的 message item 在不同模型和 transformer 下可能略有差异：

    - 有些直接给消息对象，文本在 `.content`；
    - 有些给 dict，文本在 `content` 或 `delta`；
    - 有些把消息放在 `(message, metadata)` 这样的 tuple 里。

    这里只抽取文本，不尝试理解更多元数据。复杂字段后面可以单独加测试扩展。
    """

    if isinstance(item, tuple) and item:
        return extract_message_delta(item[0])

    if isinstance(item, dict):
        event_type = item.get("event")
        if event_type == "content-block-delta":
            delta = item.get("delta")
            if isinstance(delta, dict):
                text = delta.get("text")
                if isinstance(text, str):
                    return text
        if event_type == "content-block-start":
            content = item.get("content")
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str) and text:
                    return text
        for key in ("delta", "content", "text"):
            value = item.get(key)
            if isinstance(value, str):
                return value
        message = item.get("message")
        if message is not None:
            return extract_message_delta(message)
        return ""

    content = getattr(item, "content", None)
    if isinstance(content, str):
        return content

    return ""


def normalize_values_snapshot(*, item: Any, bundle: RunConfigBundle) -> dict[str, Any]:
    """把 values projection 整理成前端可读的状态快照。"""

    data = normalize_mapping(item)
    messages = data.get("messages", [])
    normalized_messages = normalize_messages(messages)
    data["messages"] = normalized_messages
    return {
        "thread_id": bundle.context.thread_id,
        "run_id": bundle.context.run_id,
        "messages": normalized_messages,
        "state": data,
    }


def normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """把 LangChain message 对象列表转成普通 dict 列表。"""

    if not isinstance(messages, Iterable) or isinstance(messages, (str, bytes, dict)):
        return []

    return [normalize_message(message) for message in messages]


def normalize_message(message: Any) -> dict[str, Any]:
    """把单条 message 对象压成稳定的 `role/content` 形状。"""

    if isinstance(message, dict):
        role = message.get("role") or message.get("type") or "message"
        content = normalize_message_content(message.get("content", ""))
        normalized = {
            "role": role,
            "content": content,
        }
        if isinstance(message.get("id"), str):
            normalized["id"] = message["id"]
        if isinstance(message.get("name"), str):
            normalized["name"] = message["name"]
        return normalized

    role = getattr(message, "type", None) or getattr(message, "role", None) or "message"
    content = normalize_message_content(getattr(message, "content", ""))
    normalized = {
        "role": role,
        "content": content,
    }
    message_id = getattr(message, "id", None)
    if isinstance(message_id, str):
        normalized["id"] = message_id
    name = getattr(message, "name", None)
    if isinstance(name, str):
        normalized["name"] = name
    return normalized


def normalize_message_content(content: Any) -> str:
    """把消息内容压成前端和 SSE 都容易消费的纯文本。"""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        if nested is not None:
            return normalize_message_content(nested)
        return repr(to_jsonable(content))

    if isinstance(content, list):
        parts = [extract_text_block_text(item) for item in content]
        text = "".join(part for part in parts if part)
        if text:
            return text
        return repr(to_jsonable(content))

    return repr(to_jsonable(content))


def extract_text_block_text(item: Any) -> str:
    """从 LangChain content block 里尽量抽出纯文本。"""

    if isinstance(item, str):
        return item

    if isinstance(item, dict):
        text = item.get("text")
        if isinstance(text, str):
            return text
        delta = item.get("delta")
        if delta is not None:
            return extract_text_block_text(delta)
        nested = item.get("content")
        if nested is not None:
            return extract_text_block_text(nested)

    return ""


def normalize_mapping(item: Any) -> dict[str, Any]:
    """尽量把未知 item 转成普通 dict，方便 JSON 编码和测试断言。"""

    if isinstance(item, dict):
        return {
            str(key): to_jsonable(value)
            for key, value in item.items()
        }

    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        try:
            return to_jsonable(model_dump(mode="json"))
        except TypeError:
            return to_jsonable(model_dump())

    if hasattr(item, "__dict__"):
        return to_jsonable(item.__dict__)

    return {"value": repr(item)}


def to_jsonable(value: Any) -> Any:
    """把 LangChain / Pydantic 对象递归转成 JSON 能编码的普通数据。

    SSE 最终要走 `json.dumps`。真实 LangGraph state 里常见 `HumanMessage`、`AIMessage`
    这类对象，不能直接塞进 `state.snapshot`。这里把它们压成普通 dict，前端就不用
    认识 Python 对象。
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): to_jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return to_jsonable(model_dump(mode="json"))
        except TypeError:
            return to_jsonable(model_dump())

    content = getattr(value, "content", None)
    if isinstance(content, str):
        role = getattr(value, "type", None) or getattr(value, "role", None) or "message"
        return {
            "role": role,
            "content": content,
        }

    if hasattr(value, "__dict__"):
        return to_jsonable(value.__dict__)

    return repr(value)


async def collect_agent_events(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    """测试辅助函数：把异步事件流收集成列表。"""

    return [event async for event in stream]
