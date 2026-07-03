"""Small shared helpers for the SlotFlow harness.

集中放置 tools / middleware / subagents 之间重复出现的小工具，避免同一段逻辑在多个
注册表里各写一份。这里只依赖 LangChain 基础类型，不导入 harness 的其他子模块，
以免产生循环引用。
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage


class _Named(Protocol):
    name: str


_NamedT = TypeVar("_NamedT", bound=_Named)


def dedupe_by_name(items: list[_NamedT]) -> list[_NamedT]:
    """按 `.name` 去重，保留更早出现的元素（tools 等注册表条目都有 `name`）。"""

    seen: set[str] = set()
    unique: list[_NamedT] = []
    for item in items:
        if item.name in seen:
            continue
        unique.append(item)
        seen.add(item.name)
    return unique


def model_supports_tools(model: str | BaseChatModel) -> bool:
    """Whether the selected model can bind LangChain tools.

    字符串模型名交给 LangChain 解析，默认支持；具体 chat model 则看它是否真正覆盖了
    `bind_tools`（未覆盖说明该实现不支持工具调用）。
    """

    if isinstance(model, str):
        return True
    return type(model).bind_tools is not BaseChatModel.bind_tools


def message_role(message: Any) -> str | None:
    """Best-effort role of a LangChain message object or a plain message dict."""

    if isinstance(message, BaseMessage):
        return message.type
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        return str(role) if role is not None else None
    role = getattr(message, "role", None) or getattr(message, "type", None)
    return str(role) if role is not None else None


def message_text(message: Any) -> str:
    """Best-effort 取一条消息（LangChain 对象或 dict）的纯文本内容。"""

    if isinstance(message, dict):
        return message_content_text(message.get("content"))
    return message_content_text(getattr(message, "content", ""))


def message_content_text(content: Any) -> str:
    """把消息 content（str / content-block 列表 / dict）拍平成纯文本。

    这是 harness 侧唯一的消息拍平实现（clarify_gate / skills_preflight /
    long_term_memory / todo / title_generation 共用）。注意 SSE 投影层的
    ``projections.normalize_message_content`` 语义不同（未知 dict 走 repr 兜底、
    跳过 reasoning 块），面向前端展示，不要与本函数合并。
    """

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
        nested = content.get("content")
        if nested is not None:
            return message_content_text(nested)
        return ""
    if isinstance(content, list):
        parts = [message_content_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""


def latest_user_message_text(messages: list[Any]) -> str:
    """取最近一条用户消息的纯文本；没有用户消息时返回空串。"""

    for message in reversed(messages):
        if message_role(message) in ("user", "human"):
            return message_text(message)
    return ""
