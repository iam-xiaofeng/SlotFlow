"""LangGraph v3 event streaming 的业务适配层。

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
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.chat.models import ChatStreamRequest, RunConfigBundle


AgentEventName = Literal[
    "run.prepared",
    "message.delta",
    "tool.delta",
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


class LangGraphEventAgentAdapter:
    """真实 LangGraph / LangChain agent 的 v3 event streaming 适配器。

    这个类接收一个已经构造好的 agent graph。它要求 graph 支持
    `astream_events(..., version="v3")`，并优先通过 v3 的 typed projections 取数据。

    SlotFlow 目前只消费 `messages`、`values`、`tool_calls` 三类事件。`output` 属于
    终态方法，不适合一边流一边读；等事件流结束后再通过 `run.finished` 表达完成。
    """

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        """调用真实 graph，并把 v3 投影逐条转换成 `AgentEvent`。"""

        yield make_prepared_event(bundle=bundle)

        stream_input = build_agent_input(request, bundle=bundle)

        projection_stream = await self._graph.astream_events(
            stream_input,
            config=bundle.config,
            version="v3",
            context=bundle.context,
        )
        async for event in iter_projection_agent_events(projection_stream, bundle=bundle):
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
    channel，再按“谁先产出谁先发”的近似顺序输出。
    """

    channels = projection_channels(run_stream)
    if not channels:
        return

    queue: asyncio.Queue[ProjectionEnvelope | BaseException | object] = asyncio.Queue()
    done_sentinel = object()
    latest_snapshot: AgentEvent | None = None
    latest_clarification: AgentEvent | None = None
    latest_todos_signature: str | None = None

    async def pump_projection(projection: str, channel: Any) -> None:
        nonlocal latest_clarification, latest_snapshot, latest_todos_signature
        try:
            async for item in channel:
                if projection == "values":
                    latest_snapshot = projection_item_to_agent_event(
                        projection=projection,
                        item=item,
                        bundle=bundle,
                    )
                    if latest_snapshot is not None:
                        todo_event = todo_event_from_snapshot(latest_snapshot.data)
                        if todo_event is not None:
                            signature = json.dumps(
                                todo_event.data.get("todos", []),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            if signature != latest_todos_signature:
                                latest_todos_signature = signature
                                await queue.put(todo_event)
                        latest_clarification = clarification_event_from_snapshot(
                            latest_snapshot.data
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
            if isinstance(item, AgentEvent):
                yield item
                continue
            if not isinstance(item, ProjectionEnvelope):
                continue

            event = projection_item_to_agent_event(
                projection=item.projection,
                item=item.item,
                bundle=bundle,
            )
            if event is not None:
                yield event
        if latest_clarification is not None:
            yield latest_clarification
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
        metadata = item[1] if len(item) > 1 else None
        async for nested in item[0]:
            yield (nested, metadata)
        return

    if isinstance(item, list):
        for nested in item:
            yield nested
        return

    yield item


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
        message_delta = extract_message_delta_parts(item)
        delta = message_delta.get("content") or message_delta.get("reasoning_content")
        if not isinstance(delta, str) or not delta:
            return None
        return AgentEvent(
            event="message.delta",
            data={
                "message_id": assistant_message_id(bundle),
                "role": "assistant",
                "delta": delta,
                "channel": "content" if message_delta.get("content") else "reasoning",
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


def extract_message_delta(item: Any) -> str:
    """从 message projection item 中提取文本增量。

    reasoning 和正文都属于 message 增量；调用方如果需要区分通道，应使用
    `extract_message_delta_parts()`。
    """

    parts = extract_message_delta_parts(item)
    return str(parts.get("content") or parts.get("reasoning_content") or "")


def extract_message_delta_parts(item: Any) -> dict[str, str]:
    """Extract message deltas split by output channel.

    官方 LangChain content block 是 reasoning 的主入口。DeepSeek 通过
    `langchain-openai` 接入时，provider hook 会把 `delta.reasoning_content`
    放进 `AIMessageChunk.additional_kwargs`，这里只保留这个明确 fallback。
    """

    if is_summarization_item(item):
        return {}

    if isinstance(item, tuple) and item:
        return extract_message_delta_parts(item[0])

    reasoning = extract_reasoning_text(item)
    if reasoning:
        return {"reasoning_content": reasoning}

    if isinstance(item, dict):
        event_type = item.get("event")
        if event_type == "content-block-delta":
            parts = extract_content_block_delta(item.get("delta"))
            if parts:
                return parts
        if event_type == "content-block-start":
            parts = extract_content_block_delta(item.get("content"))
            if parts:
                return parts
        for key in ("delta", "content", "text"):
            value = item.get(key)
            if isinstance(value, str):
                return {"content": value}
        return {}

    content = getattr(item, "content", None)
    if isinstance(content, str):
        return {"content": content}

    return {}


def is_summarization_item(item: Any) -> bool:
    """LangChain summarization calls are internal context maintenance, not chat output."""

    if isinstance(item, tuple) and len(item) > 1:
        return has_lc_source_summarization(item[1])
    return has_lc_source_summarization(item)


def has_lc_source_summarization(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("lc_source") == "summarization":
            return True
        for key in ("metadata", "config", "additional_kwargs", "response_metadata"):
            nested = value.get(key)
            if nested is not None and has_lc_source_summarization(nested):
                return True
        return False

    for attr in ("metadata", "additional_kwargs", "response_metadata"):
        try:
            nested = getattr(value, attr, None)
        except Exception:
            continue
        if nested is not None and has_lc_source_summarization(nested):
            return True
    return False


def extract_content_block_delta(item: Any) -> dict[str, str]:
    reasoning = extract_reasoning_from_content_block(item)
    if reasoning:
        return {"reasoning_content": reasoning}

    text = extract_text_block_text(item)
    if text:
        return {"content": text}

    return {}


def extract_reasoning_text(item: Any) -> str:
    """Return reasoning from LangChain standard blocks, then DeepSeek fallback."""

    reasoning = extract_standard_reasoning_text(item)
    if reasoning:
        return reasoning
    return extract_deepseek_reasoning_content(item)


def extract_standard_reasoning_text(item: Any) -> str:
    direct = extract_reasoning_from_content_block(item)
    if direct:
        return direct

    for block in iter_content_blocks(item):
        reasoning = extract_reasoning_from_content_block(block)
        if reasoning:
            return reasoning
    return ""


def extract_reasoning_from_content_block(item: Any) -> str:
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return ""

    reasoning = item.get("reasoning")
    return reasoning if isinstance(reasoning, str) and reasoning else ""


def iter_content_blocks(item: Any) -> Iterable[Any]:
    if isinstance(item, dict):
        for key in ("content_blocks", "contentBlocks"):
            yield from list_content_blocks(item.get(key))
        yield from list_content_blocks(item.get("content"))
        return

    for attr in ("content_blocks", "contentBlocks"):
        try:
            value = getattr(item, attr, None)
        except Exception:
            continue
        yield from list_content_blocks(value)

    try:
        content = getattr(item, "content", None)
    except Exception:
        return
    yield from list_content_blocks(content)


def list_content_blocks(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        yield from value


def extract_deepseek_reasoning_content(item: Any) -> str:
    additional_kwargs = (
        item.get("additional_kwargs")
        if isinstance(item, dict)
        else getattr(item, "additional_kwargs", None)
    )
    if not isinstance(additional_kwargs, dict):
        return ""

    reasoning = additional_kwargs.get("reasoning_content")
    return reasoning if isinstance(reasoning, str) and reasoning else ""


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


def clarification_event_from_snapshot(snapshot: dict[str, Any]) -> AgentEvent | None:
    """Extract the latest structured clarification request from a values snapshot."""

    messages = snapshot.get("messages")
    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "tool" or message.get("name") != "ask_clarification":
            continue
        payload = parse_clarification_payload(message.get("content"))
        if payload is None:
            return None
        payload.setdefault("thread_id", snapshot.get("thread_id"))
        payload.setdefault("run_id", snapshot.get("run_id"))
        return AgentEvent(event="clarification.requested", data=payload)
    return None


def todo_event_from_snapshot(snapshot: dict[str, Any]) -> AgentEvent | None:
    """Extract the current todo list from a values snapshot."""

    state = snapshot.get("state")
    if not isinstance(state, dict) or "todos" not in state:
        return None

    todos = normalize_todos(state.get("todos"))
    return AgentEvent(
        event="todo.updated",
        data={
            "thread_id": snapshot.get("thread_id"),
            "run_id": snapshot.get("run_id"),
            "todos": todos,
        },
    )


def normalize_todos(value: Any) -> list[dict[str, str]]:
    """Normalize LangChain todo state to the public SlotFlow shape."""

    if not isinstance(value, list):
        return []

    todos: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        status = item.get("status")
        if not isinstance(content, str) or not content.strip():
            continue
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        todos.append({"content": content, "status": status})
    return todos


def parse_clarification_payload(content: Any) -> dict[str, Any] | None:
    """Parse the JSON ToolMessage produced by SlotFlowClarificationMiddleware."""

    if isinstance(content, dict):
        payload = content
    elif isinstance(content, str):
        try:
            loaded = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        payload = loaded
    else:
        return None

    if payload.get("type") != "clarification":
        return None
    if payload.get("source") != "slotflow_clarification":
        return None
    return to_jsonable(payload)


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
        reasoning = extract_reasoning_text(message)
        if reasoning:
            normalized["reasoning_content"] = reasoning
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
    reasoning = extract_reasoning_text(message)
    if reasoning:
        normalized["reasoning_content"] = reasoning
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
