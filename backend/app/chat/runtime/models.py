"""Chat model construction for the SlotFlow runtime.

按前端选择的 model id 推断 provider，并创建对应的 LangChain chat model。DeepSeek 用
官方 `langchain_deepseek.ChatDeepSeek`（基于 OpenAI 兼容协议且原生解析 reasoning_content），
SlotFlow 只补一个很薄的桥，把 reasoning 也映射成标准 reasoning content block 供 v3 流消费。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.chat.model_catalog import PROVIDER_DEFAULT_BASE_URLS
from app.chat.models import ModelProvider, RunContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessageChunk


def bridge_reasoning_content_block(message: "BaseMessageChunk") -> None:
    """Mirror DeepSeek reasoning into a standard reasoning content block.

    ChatDeepSeek already extracts `choices[].delta.reasoning_content` into
    `additional_kwargs["reasoning_content"]`. SlotFlow 的 LangGraph v3 流式从 typed
    `message.reasoning` 通道读取思考，而该通道由标准 reasoning content block 喂入；
    因此当某个 chunk 只带 reasoning（还没有正文）时，把它桥接成一个
    `{"type": "reasoning"}` content block。
    """

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict):
        return

    reasoning = additional_kwargs.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        return

    if not message_has_content(message):
        message.content = [{"type": "reasoning", "reasoning": reasoning}]


def message_has_content(message: "BaseMessageChunk") -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return bool(content)
    return content is not None


def create_model_for_context(
    model_factory: "Callable[..., BaseChatModel]",
    *,
    model_name: str,
    run_context: RunContext,
) -> "BaseChatModel":
    """Create a model, passing run context when the factory supports it."""

    import inspect

    signature = inspect.signature(model_factory)
    if "run_context" in signature.parameters:
        return model_factory(model_name, run_context=run_context)
    return model_factory(model_name)


def create_chat_model(
    model_name: str,
    *,
    run_context: RunContext | None = None,
) -> "BaseChatModel":
    """Create a chat model from the provider implied by the selected model id."""

    provider = infer_model_provider(model_name)
    if provider == "anthropic":
        return create_anthropic_chat_model(model_name=model_name, run_context=run_context)
    return create_openai_compatible_chat_model(
        model_name=model_name,
        provider=provider,
        run_context=run_context,
    )


def infer_model_provider(model_name: str) -> ModelProvider:
    """Infer the provider from a frontend-selected model id."""

    normalized = model_name.strip().lower()
    if normalized.startswith("claude-"):
        return "anthropic"
    if normalized.startswith("gpt-") or normalized.startswith("o"):
        return "openai"
    return "deepseek"


def create_openai_compatible_chat_model(
    *,
    model_name: str,
    provider: ModelProvider,
    run_context: RunContext | None = None,
) -> "BaseChatModel":
    """Create DeepSeek/OpenAI-compatible chat models."""

    from langchain_openai import ChatOpenAI

    if provider == "openai":
        api_key_name = "OPENAI_API_KEY"
        base_url = os.environ.get("OPENAI_BASE_URL")
    else:
        api_key_name = "DEEPSEEK_API_KEY"
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or PROVIDER_DEFAULT_BASE_URLS["deepseek"]

    api_key = os.environ.get(api_key_name)
    if not api_key:
        raise RuntimeError(f"{api_key_name} is required for {provider} runtime")

    chat_model_class = _deepseek_chat_model_class() if provider == "deepseek" else ChatOpenAI

    return chat_model_class(
        **build_openai_compatible_model_kwargs(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            provider=provider,
            run_context=run_context,
        )
    )


@lru_cache(maxsize=1)
def _deepseek_chat_model_class() -> type:
    """Build the SlotFlow ChatDeepSeek subclass once, lazily.

    ChatDeepSeek（langchain-deepseek）已原生把 DeepSeek 的 reasoning_content 解析进
    additional_kwargs；这里只补一层桥，让 reasoning 同时进入 LangGraph v3 的 typed
    `message.reasoning` 通道（标准 reasoning content block）。延迟导入并缓存子类，
    避免每次实例化都重新 define。
    """

    from langchain_deepseek import ChatDeepSeek

    class _SlotFlowChatDeepSeek(ChatDeepSeek):
        def _convert_chunk_to_generation_chunk(
            self,
            chunk: dict[str, Any],
            default_chunk_class: type,
            base_generation_info: dict | None,
        ) -> Any:
            generation_chunk = super()._convert_chunk_to_generation_chunk(
                chunk,
                default_chunk_class,
                base_generation_info,
            )
            if generation_chunk is not None:
                bridge_reasoning_content_block(generation_chunk.message)
            return generation_chunk

    return _SlotFlowChatDeepSeek


def DeepSeekChatModel(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - factory keeps a class-like public name
    """ChatDeepSeek variant that also surfaces reasoning via the v3 reasoning channel."""

    return _deepseek_chat_model_class()(*args, **kwargs)


def build_openai_compatible_model_kwargs(
    *,
    model_name: str,
    api_key: str,
    base_url: str | None,
    provider: ModelProvider,
    run_context: RunContext | None = None,
) -> dict[str, Any]:
    """Build ChatOpenAI kwargs, including provider-specific thinking flags."""

    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "streaming": True,
        "timeout": 30,
        "max_retries": 0,
    }
    if run_context and run_context.thinking_enabled:
        if provider == "deepseek":
            # DeepSeek v4 uses an OpenAI-compatible body-level thinking switch.
            kwargs["reasoning_effort"] = "high"
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        elif provider == "openai" and is_openai_reasoning_model(model_name):
            # Only OpenAI reasoning models (o-series / gpt-5) accept reasoning_effort.
            kwargs["reasoning_effort"] = "high"
    return kwargs


def is_openai_reasoning_model(model_name: str) -> bool:
    """Whether an OpenAI model id supports reasoning_effort (o-series / gpt-5)."""

    normalized = model_name.strip().lower()
    return normalized.startswith("o") or normalized.startswith("gpt-5")


def create_anthropic_chat_model(
    *,
    model_name: str,
    run_context: RunContext | None = None,
) -> "BaseChatModel":
    """Create an Anthropic chat model for Claude ids (with extended thinking)."""

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise RuntimeError(
            "langchain-anthropic is required for Anthropic runtime"
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic runtime")

    kwargs: dict[str, object] = {
        "model": model_name,
        "api_key": api_key,
        "streaming": True,
        "timeout": 30,
        "max_retries": 0,
    }
    if run_context and run_context.thinking_enabled:
        # Extended thinking emits "thinking" content blocks; max_tokens must exceed the
        # thinking budget. langchain-anthropic surfaces thinking text via content blocks
        # that our projection layer maps to the reasoning channel.
        kwargs["max_tokens"] = 8192
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url and base_url.strip():
        kwargs["base_url"] = base_url.strip()
    return ChatAnthropic(**kwargs)
