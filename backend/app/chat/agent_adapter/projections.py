"""把 LangGraph v3 projection item 解析成 SlotFlow 业务事件与普通数据。

这一组都是纯函数：输入是真实 LangGraph/LangChain 投影里的 dict 或消息对象，输出是
SlotFlow 自己的 `AgentEvent` 或 JSON 友好的普通结构。DeepSeek 的 reasoning、官方
content block、summarization 内部消息等差异都在这里被吸收，异步流式层只需面对干净的
映射函数。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.chat.agent_adapter.events import AgentEvent, assistant_message_id
from app.chat.models import RunConfigBundle


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


_TOOL_STATUS_SKIP = {
    # 这两个工具有自己的专属 UI(澄清选择卡片/todo 面板),再叠一个状态芯片是噪音。
    "ask_clarification",
    "write_todos",
}

_TOOL_STATUS_MESSAGES = {
    "sandbox_exec": "正在初始化 Docker 沙箱并执行代码",
    "artifact_write": "正在写入产物文件",
    "workspace_read": "正在读取工作区文件",
    "workspace_grep": "正在检索工作区",
    "web_search": "正在搜索网页",
    "web_fetch": "正在抓取网页",
    "task_tool": "正在派发子任务",
    "memory_save": "正在保存长期记忆",
    "memory_list": "正在查询长期记忆",
    "skill_match": "正在匹配技能",
    "skill_install": "正在安装技能",
}


def tool_status_event_from_tool_call(
    item: Any,
    *,
    bundle: RunConfigBundle,
) -> AgentEvent | None:
    """Create a visible tool status event from a tool-call projection.

    LangGraph v3 currently exposes model tool calls through the ``tool_calls`` projection,
    but custom ``get_stream_writer()`` payloads are not exposed as a projection channel.
    The tool-call boundary is the earliest stable point where the UI can show what the
    agent is doing — without this, every tool run (artifact_write / web_search / ...)
    is invisible and the user only sees a frozen "thinking" indicator.
    """

    tool_call = normalize_mapping(item)
    tool_name = extract_tool_call_name(tool_call)
    if tool_name is None or tool_name in _TOOL_STATUS_SKIP:
        return None

    command = extract_sandbox_command(tool_call) if tool_name == "sandbox_exec" else None
    return AgentEvent(
        event="tool.status",
        data={
            "thread_id": bundle.context.thread_id,
            "run_id": bundle.context.run_id,
            "tool_name": tool_name,
            "phase": "running",
            "message": _TOOL_STATUS_MESSAGES.get(tool_name, "正在调用工具"),
            "command": truncate_status_text(command, 240) if command else None,
            "source": "slotflow_tool_call_projection",
        },
    )


def extract_tool_call_name(tool_call: dict[str, Any]) -> str | None:
    name = tool_call.get("name")
    if isinstance(name, str):
        return name

    nested = tool_call.get("tool_call")
    if isinstance(nested, dict):
        nested_name = nested.get("name")
        if isinstance(nested_name, str):
            return nested_name

    return None


def extract_sandbox_command(tool_call: dict[str, Any]) -> str | None:
    args = tool_call.get("args")
    if isinstance(args, dict):
        command = args.get("command")
        if isinstance(command, str):
            return command
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return args
        if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
            return parsed["command"]
    return None


def truncate_status_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}…"


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

    if isinstance(item, dict):
        explicit_channel = item.get("channel")
        delta = item.get("delta")
        if explicit_channel == "reasoning" and isinstance(delta, str):
            return {"reasoning_content": delta}
        if explicit_channel == "content" and isinstance(delta, str):
            return {"content": delta}

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
        return has_lc_source_summarization(item[0]) or has_lc_source_summarization(item[1])
    return has_lc_source_summarization(item)


def has_lc_source_summarization(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("lc_source") == "summarization":
            return True
        if is_summarization_node_name(value.get("_node") or value.get("node") or value.get("langgraph_node")):
            return True
        for key in ("metadata", "config", "additional_kwargs", "response_metadata"):
            nested = value.get(key)
            if nested is not None and has_lc_source_summarization(nested):
                return True
        return False

    for attr in ("_node", "node", "langgraph_node", "name"):
        try:
            value_text = getattr(value, attr, None)
        except Exception:
            continue
        if is_summarization_node_name(value_text):
            return True

    for attr in ("metadata", "additional_kwargs", "response_metadata"):
        try:
            nested = getattr(value, attr, None)
        except Exception:
            continue
        if nested is not None and has_lc_source_summarization(nested):
            return True
    return False


def is_summarization_node_name(value: Any) -> bool:
    return isinstance(value, str) and "SummarizationMiddleware" in value


def extract_content_block_delta(item: Any) -> dict[str, str]:
    reasoning = extract_reasoning_from_content_block(item)
    if reasoning:
        return {"reasoning_content": reasoning}

    text = extract_text_block_text(item)
    if text:
        return {"content": text}

    return {}


def extract_reasoning_text(item: Any) -> str:
    """Return reasoning from LangChain standard blocks, then provider fallbacks."""

    reasoning = extract_standard_reasoning_text(item)
    if reasoning:
        return reasoning
    return extract_provider_reasoning_content(item)


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
    # Reasoning content blocks differ by provider: DeepSeek/OpenAI-compatible use a
    # {"type": "reasoning", "reasoning": "..."} block (our ChatDeepSeek bridge emits
    # this shape); Anthropic extended thinking uses {"type": "thinking", "thinking": ...}.
    if not isinstance(item, dict) or item.get("type") not in ("reasoning", "thinking"):
        return ""

    for key in ("reasoning", "thinking", "text", "content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value

    # OpenAI Responses API emits reasoning as summary sub-blocks under the default
    # responses/v1 output_version, not as a flat string. Flatten those texts so gpt-5 /
    # o-series thinking reaches the reasoning channel instead of being silently dropped.
    summary = item.get("summary")
    if isinstance(summary, list):
        parts: list[str] = []
        for sub_block in summary:
            if isinstance(sub_block, dict):
                text = sub_block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if parts:
            return "".join(parts)
    return ""


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


def extract_provider_reasoning_content(item: Any) -> str:
    additional_kwargs = (
        item.get("additional_kwargs")
        if isinstance(item, dict)
        else getattr(item, "additional_kwargs", None)
    )
    if not isinstance(additional_kwargs, dict):
        return ""

    # DeepSeek/OpenAI-compatible providers expose reasoning here under either key.
    for key in ("reasoning_content", "reasoning"):
        value = additional_kwargs.get(key)
        if isinstance(value, str) and value:
            return value
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


def clarification_event_from_interrupt(
    value: Any, *, bundle: RunConfigBundle
) -> AgentEvent | None:
    """Turn a pending LangGraph ``interrupt`` value into a clarification.requested event.

    The clarification payload is the interrupt's value (built by ``build_clarification_payload``).
    Because this is derived from a PENDING interrupt — not scanned out of message history — an
    already-answered clarification (which leaves no pending interrupt) is never re-surfaced. This
    is the root-cause fix for the "answered clarification pops up again" bug.
    """

    payload = parse_clarification_payload(value)
    if payload is None:
        return None
    payload.setdefault("thread_id", bundle.context.thread_id)
    payload.setdefault("run_id", bundle.context.run_id)
    return AgentEvent(event="clarification.requested", data=payload)


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
        if not isinstance(content, str):
            content = item.get("text")
        status = item.get("status")
        if not isinstance(content, str) or not content.strip():
            continue
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        todos.append({"content": content, "status": status})
    return todos


def parse_clarification_payload(content: Any) -> dict[str, Any] | None:
    """Parse a clarification payload (an interrupt value built by build_clarification_payload)."""

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
        tool_call_count = count_message_tool_calls(message)
        if tool_call_count:
            normalized["has_tool_calls"] = True
            normalized["tool_call_count"] = tool_call_count
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
    tool_call_count = count_message_tool_calls(message)
    if tool_call_count:
        normalized["has_tool_calls"] = True
        normalized["tool_call_count"] = tool_call_count
    message_id = getattr(message, "id", None)
    if isinstance(message_id, str):
        normalized["id"] = message_id
    name = getattr(message, "name", None)
    if isinstance(name, str):
        normalized["name"] = name
    return normalized


def count_message_tool_calls(message: Any) -> int:
    """Return whether an assistant message is an intermediate tool-call step."""

    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            return len(tool_calls)
        additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict):
            raw_tool_calls = additional_kwargs.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                return len(raw_tool_calls)
        return 0

    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list):
        return len(tool_calls)
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            return len(raw_tool_calls)
    return 0


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
        if item.get("type") in ("reasoning", "thinking"):
            return ""
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
