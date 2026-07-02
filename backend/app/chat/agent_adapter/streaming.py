"""LangGraph v3 event streaming 的异步消费层。

这一层接收一个已构造好的 agent graph，调用 `astream_events(..., version="v3")`，把若干
typed projection channel（messages / values / tool_calls）并发拉取、近似按时间顺序合流，
再交给 projections.py 的纯函数映射成 `AgentEvent`。这里只负责异步编排与合流顺序，不解析
具体字段。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from app.chat.agent_adapter.events import (
    AgentEvent,
    build_agent_input,
    make_context_compressing_event,
    make_finished_event,
    make_prepared_event,
)
from app.chat.agent_adapter.projections import (
    clarification_event_from_interrupt,
    is_summarization_item,
    projection_item_to_agent_event,
    todo_event_from_snapshot,
)
from app.chat.models import ChatStreamRequest, RunConfigBundle


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

        # HITL clarification uses LangGraph native interrupt()/resume. If this thread is paused
        # on a pending interrupt (a clarification awaiting an answer), the incoming user message
        # IS that answer: resume the graph with it instead of starting a fresh turn. Otherwise
        # start a normal turn. Detection is provider-agnostic (graph-level), so the frontend can
        # keep sending the answer as an ordinary message. See HARNESS_NOTES.md.
        pending = await _pending_interrupt(self._graph, bundle.config)
        if pending is not None:
            stream_input: Any = Command(resume=request.message)
        else:
            stream_input = build_agent_input(request, bundle=bundle)

        projection_stream = await self._graph.astream_events(
            stream_input,
            config=bundle.config,
            version="v3",
            context=bundle.context,
        )
        async for event in iter_projection_agent_events(projection_stream, bundle=bundle):
            yield event

        # A clarification is surfaced ONLY when the graph is now paused on a fresh interrupt —
        # never re-derived from past messages. This is what makes an answered clarification stop
        # re-popping: a resolved clarification leaves no pending interrupt.
        clarification = await _clarification_from_pending_interrupt(self._graph, bundle)
        if clarification is not None:
            yield clarification

        yield make_finished_event(bundle=bundle)


async def _pending_interrupt(graph: Any, config: Any) -> Any | None:
    """Return the first pending Interrupt on this thread, or None.

    Guarded so test stub graphs without a checkpointer/``aget_state`` degrade to "no interrupt"
    (they never pause), keeping the non-HITL streaming path unchanged.
    """

    aget_state = getattr(graph, "aget_state", None)
    if not callable(aget_state):
        return None
    try:
        state = await aget_state(config)
    except Exception:  # pragma: no cover - missing checkpointer / fresh thread
        return None

    interrupts = list(getattr(state, "interrupts", None) or [])
    if not interrupts:
        for task in getattr(state, "tasks", None) or []:
            task_interrupts = getattr(task, "interrupts", None) or []
            if task_interrupts:
                interrupts = list(task_interrupts)
                break
    return interrupts[0] if interrupts else None


async def _clarification_from_pending_interrupt(
    graph: Any, bundle: RunConfigBundle
) -> AgentEvent | None:
    pending = await _pending_interrupt(graph, bundle.config)
    if pending is None:
        return None
    return clarification_event_from_interrupt(getattr(pending, "value", None), bundle=bundle)


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
    compression_announced = False

    async def pump_projection(projection: str, channel: Any) -> None:
        nonlocal compression_announced, latest_snapshot
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
                            await queue.put(todo_event)
                    continue
                if projection == "messages":
                    if is_summarization_item(item):
                        if not compression_announced:
                            compression_announced = True
                            await queue.put(make_context_compressing_event(bundle=bundle))
                        await drain_message_projection_item(item)
                        continue
                    async for message_item in flatten_message_projection_items(item):
                        if is_summarization_item(message_item):
                            if not compression_announced:
                                compression_announced = True
                                await queue.put(make_context_compressing_event(bundle=bundle))
                            continue
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

    # 根因：LangGraph v3 的 AsyncChatModelStream 不能直接 `async for message`
    # 当普通子流消费；那样会丢失 `message.reasoning` / `message.text` 的通道归属，
    # DeepSeek 的思考会被误当正文，或只能等 values snapshot 后一次性补回来。
    if typed_channels := typed_message_delta_channels(item):
        async for nested in iter_typed_message_delta_items(typed_channels):
            yield nested
        return

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


def typed_message_delta_channels(item: Any) -> list[tuple[str, Any]]:
    """Return LangGraph v3 typed message projections when available."""

    channels: list[tuple[str, Any]] = []
    for channel_name, attr_name in (
        ("reasoning", "reasoning"),
        ("content", "text"),
    ):
        channel = getattr(item, attr_name, None)
        if channel is not None and hasattr(channel, "__aiter__"):
            channels.append((channel_name, channel))
    return channels


async def iter_typed_message_delta_items(
    channels: list[tuple[str, Any]],
) -> AsyncIterator[dict[str, str]]:
    """Interleave LangGraph `message.reasoning` and `message.text` deltas."""

    queue: asyncio.Queue[tuple[str, str] | BaseException | object] = asyncio.Queue()
    done_sentinel = object()

    async def pump_channel(channel_name: str, channel: Any) -> None:
        try:
            async for delta in channel:
                if isinstance(delta, str) and delta:
                    await queue.put((channel_name, delta))
        except Exception as exc:  # pragma: no cover - surfaced to caller
            await queue.put(exc)
        finally:
            await queue.put(done_sentinel)

    tasks = [
        asyncio.create_task(pump_channel(channel_name, channel))
        for channel_name, channel in channels
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
            channel_name, delta = item
            yield {"channel": channel_name, "delta": delta}
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def drain_message_projection_item(item: Any) -> None:
    """Drain an internal message projection so LangGraph can keep running."""

    async for _ in flatten_message_projection_items(item):
        pass
