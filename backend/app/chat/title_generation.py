"""Automatic thread title generation."""

from __future__ import annotations

import os
import re

from app.chat.models import MessageRecord, ModelProvider, ThreadRecord
from app.chat.runtime import create_chat_model
from app.harness.utils import message_content_text


MAX_TITLE_WORDS = 8
MAX_TITLE_CHARS = 60
TITLE_PROMPT_TEMPLATE = (
    "Generate a concise title (max {max_words} words) for this conversation.\n"
    "User: {user_msg}\n"
    "Assistant: {assistant_msg}\n\n"
    "Return ONLY the title, no quotes, no explanation."
)


async def maybe_generate_thread_title(
    *,
    thread: ThreadRecord,
    messages: list[MessageRecord],
    model_name: str,
    provider: ModelProvider | None = None,
) -> str | None:
    if not should_generate_title(messages):
        return None

    user_message = next(message for message in messages if message.role == "user")
    assistant_message = next(message for message in messages if message.role == "assistant")
    fallback = fallback_title(user_message.content)

    if not title_model_enabled():
        return fallback

    prompt = TITLE_PROMPT_TEMPLATE.format(
        max_words=MAX_TITLE_WORDS,
        user_msg=message_content_text(user_message.content)[:500],
        assistant_msg=strip_think_tags(message_content_text(assistant_message.content))[:500],
    )

    try:
        # SLOTFLOW_TITLE_MODEL 覆盖时按它自己的 id 推断 provider；否则沿用本轮对话所选
        # 模型的来源 provider，custom 中转站才不会被错误路由到 deepseek。
        title_model = os.environ.get("SLOTFLOW_TITLE_MODEL", "").strip()
        if title_model:
            model = create_chat_model(title_model)
        else:
            model = create_chat_model(model_name, provider=provider)
        response = await model.ainvoke(
            prompt,
            config={
                "run_name": "title_agent",
                "tags": ["middleware:title"],
                "metadata": {"thread_id": thread.id, "slotflow_middleware": "title"},
            },
        )
        title = parse_title(getattr(response, "content", ""))
        return title or fallback
    except Exception:
        return fallback


def should_generate_title(messages: list[MessageRecord]) -> bool:
    user_messages = [message for message in messages if message.role == "user"]
    assistant_messages = [message for message in messages if message.role == "assistant"]
    return len(user_messages) == 1 and len(assistant_messages) >= 1


def title_model_enabled() -> bool:
    return os.environ.get("SLOTFLOW_TITLE_MODEL_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def strip_think_tags(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def parse_title(content: object) -> str:
    title = strip_think_tags(message_content_text(content)).strip().strip('"').strip("'")
    return title[:MAX_TITLE_CHARS].strip()


def fallback_title(user_message: str) -> str:
    compact = " ".join(strip_think_tags(user_message).split())
    if not compact:
        return "新会话"
    if len(compact) <= MAX_TITLE_CHARS:
        return compact
    return compact[:MAX_TITLE_CHARS].rstrip() + "..."
