"""Chat model construction for the SlotFlow runtime.

按前端选择的 model id 推断 provider，并创建对应的 LangChain chat model。DeepSeek 走
OpenAI 兼容协议，但官方流里多了 `reasoning_content`，需要一个保留它的 ChatOpenAI 变体。
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


def preserve_deepseek_reasoning_delta(
    message: "BaseMessageChunk",
    delta: dict[str, Any],
) -> None:
    """Restore DeepSeek reasoning fields dropped by langchain-openai.

    The OpenAI-compatible DeepSeek stream includes `choices[].delta.reasoning_content`,
    but langchain-openai currently only converts `content`, tool calls, and function
    calls into AIMessageChunk fields. This provider-only hook keeps that official
    DeepSeek field as a standard LangChain reasoning content block, so LangGraph v3
    can expose it through `message.reasoning` instead of an empty generic chunk.
    """

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict):
        return

    reasoning = delta.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        return

    additional_kwargs["reasoning_content"] = reasoning
    if not message_has_content(message):
        message.content = [{"type": "reasoning", "reasoning": reasoning}]


def message_has_content(message: "BaseMessageChunk") -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return bool(content)
    return content is not None


def deepseek_delta_from_stream_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    choices = (
        chunk.get("choices", [])
        or chunk.get("chunk", {}).get("choices", [])
    )
    if not choices:
        return {}
    delta = choices[0].get("delta")
    return delta if isinstance(delta, dict) else {}


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
        return create_anthropic_chat_model(model_name=model_name)
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

    chat_model_class = DeepSeekChatOpenAI if provider == "deepseek" else ChatOpenAI

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
def _deepseek_chat_openai_class() -> type:
    """Build the DeepSeek-aware ChatOpenAI subclass once, lazily.

    langchain-openai 只在导入后才可用，因此把子类定义延迟到首次构造模型时，并缓存
    复用，避免每次实例化都重新 define 一个类。
    """

    from langchain_openai import ChatOpenAI

    class _DeepSeekChatOpenAI(ChatOpenAI):
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
                preserve_deepseek_reasoning_delta(
                    generation_chunk.message,
                    deepseek_delta_from_stream_chunk(chunk),
                )
            return generation_chunk

    return _DeepSeekChatOpenAI


def DeepSeekChatOpenAI(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - factory keeps a class-like public name
    """ChatOpenAI variant that preserves DeepSeek reasoning deltas."""

    return _deepseek_chat_openai_class()(*args, **kwargs)


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
    if provider == "deepseek" and run_context and run_context.thinking_enabled:
        kwargs["reasoning_effort"] = "high"
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def create_anthropic_chat_model(*, model_name: str) -> "BaseChatModel":
    """Create an Anthropic chat model for Claude ids."""

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
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url and base_url.strip():
        kwargs["base_url"] = base_url.strip()
    return ChatAnthropic(**kwargs)
