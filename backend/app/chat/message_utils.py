"""Shared message normalization utilities.

统一的消息规范化逻辑，避免在多个地方重复实现。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


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
    """统一 message content 为字符串。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts) if parts else ""
    return str(content) if content is not None else ""


def extract_reasoning_text(message: Any) -> str | None:
    """提取 reasoning_content（如果存在）。"""

    if isinstance(message, dict):
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
        response_metadata = message.get("response_metadata")
        if isinstance(response_metadata, dict):
            reasoning_from_meta = response_metadata.get("reasoning_content")
            if isinstance(reasoning_from_meta, str) and reasoning_from_meta.strip():
                return reasoning_from_meta
        return None

    reasoning = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        reasoning_from_meta = response_metadata.get("reasoning_content")
        if isinstance(reasoning_from_meta, str) and reasoning_from_meta.strip():
            return reasoning_from_meta
    return None
