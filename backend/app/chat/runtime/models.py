"""Chat model construction for the SlotFlow runtime.

按前端选择的 model id 推断 provider，并创建对应的 LangChain chat model。DeepSeek 用
官方 `langchain_deepseek.ChatDeepSeek`（基于 OpenAI 兼容协议且原生解析 reasoning_content），
SlotFlow 只补一个很薄的桥，把 reasoning 也映射成标准 reasoning content block 供 v3 流消费。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.chat.model_catalog import PROVIDER_DEFAULT_BASE_URLS, RELAY_USER_AGENT
from app.chat.models import ModelProvider, RunContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.messages import BaseMessage
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


def inject_reasoning_content_into_deepseek_payload(
    *,
    payload: dict[str, Any],
    messages: list["BaseMessage"],
) -> None:
    """Preserve DeepSeek thinking-mode reasoning on multi-step requests.

    DeepSeek's thinking mode requires the assistant message's returned
    ``reasoning_content`` to be passed back on later turns (notably the ReAct
    request after tool results). ``langchain_deepseek`` parses streaming deltas
    into ``AIMessage.additional_kwargs['reasoning_content']``, but the inherited
    OpenAI message converter does not serialize that provider-specific field.
    Patch the final chat/completions payload in-place so tool loops keep the
    exact reasoning text DeepSeek expects.
    """

    from langchain_core.messages import AIMessage

    payload_messages = payload.get("messages")
    if not isinstance(payload_messages, list):
        return

    for source_message, payload_message in zip(messages, payload_messages, strict=False):
        if not isinstance(source_message, AIMessage) or not isinstance(payload_message, dict):
            continue
        if payload_message.get("role") != "assistant":
            continue
        reasoning_content = source_message.additional_kwargs.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            payload_message["reasoning_content"] = reasoning_content


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
    provider: ModelProvider | None = None,
) -> "BaseChatModel":
    """Create a chat model for the run.

    Provider 优先取显式传入的 `provider`，其次取前端选择时携带的来源
    （`run_context.model_provider`），因为第三方中转站可能用 OpenAI 协议提供
    `claude-*` / `gpt-*` / `qwen-*` 等任意模型，按 id 前缀猜测会路由到错误的
    client/key/endpoint。只有都缺失时（老客户端等）才回退到按 model id 前缀推断。
    """

    resolved = provider or resolve_model_provider(model_name, run_context)
    if resolved == "anthropic":
        return create_anthropic_chat_model(model_name=model_name, run_context=run_context)
    return create_openai_compatible_chat_model(
        model_name=model_name,
        provider=resolved,
        run_context=run_context,
    )


def resolve_model_provider(
    model_name: str,
    run_context: RunContext | None,
) -> ModelProvider:
    """Prefer the catalog-carried provider; fall back to model-id inference."""

    if run_context is not None and run_context.model_provider:
        return run_context.model_provider
    return infer_model_provider(model_name)


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
    """Create DeepSeek / OpenAI / custom-relay chat models over the OpenAI protocol."""

    from langchain_openai import ChatOpenAI

    if provider == "openai":
        api_key_name = "OPENAI_API_KEY"
        base_url = os.environ.get("OPENAI_BASE_URL")
    elif provider == "custom":
        # 用户自建 / 第三方 OpenAI 兼容中转站：URL 必须显式配置，没有官方回落地址。
        api_key_name = "CUSTOM_API_KEY"
        base_url = (os.environ.get("CUSTOM_BASE_URL") or "").strip() or None
        if not base_url:
            raise RuntimeError("CUSTOM_BASE_URL is required for custom runtime")
    else:
        api_key_name = "DEEPSEEK_API_KEY"
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or PROVIDER_DEFAULT_BASE_URLS["deepseek"]

    api_key = os.environ.get(api_key_name)
    if not api_key:
        raise RuntimeError(f"{api_key_name} is required for {provider} runtime")

    # DeepSeek 与 custom 中转站都可能用 `delta.reasoning_content` 发思考，需要带桥接的
    # 子类把它解析进 v3 reasoning 通道；官方 openai 用标准 ChatOpenAI。
    use_reasoning_bridge = provider in ("deepseek", "custom")
    chat_model_class = _deepseek_chat_model_class() if use_reasoning_bridge else ChatOpenAI

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
        def _get_request_payload(
            self,
            input_: Any,
            *,
            stop: list[str] | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            inject_reasoning_content_into_deepseek_payload(
                payload=payload,
                messages=self._convert_input(input_).to_messages(),
            )
            return payload

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
        # Long multi-step (ultra) runs make many provider calls; a single transient
        # APIConnectionError / 429 / 5xx must NOT kill the whole run. Retry with backoff.
        "max_retries": 2,
    }
    if provider == "custom":
        # Third-party OpenAI-compatible relays commonly sit behind a Cloudflare WAF that
        # blocks the OpenAI SDK's fingerprint UA (`AsyncOpenAI/Python <ver>` -> HTTP 403
        # "Your request was blocked."). Both ChatDeepSeek (used here for reasoning
        # bridging) and plain ChatOpenAI build on the same `openai.AsyncOpenAI` client,
        # which injects that UA by default, so *every* custom-relay model would 403
        # without this override. Override via `default_headers` so the live chat path uses
        # the SAME neutral UA the discovery probe uses — "shows in selector" == "usable".
        # See `model_catalog.RELAY_USER_AGENT` / `relay_request_headers` for the discovery
        # side and HARNESS_NOTES.md for the live verification.
        kwargs["default_headers"] = {"User-Agent": RELAY_USER_AGENT}
    if provider == "deepseek":
        # DeepSeek v4 keeps thinking ON by default, so the off state must be sent
        # explicitly — omitting the flag is NOT "no thinking".
        if run_context and run_context.thinking_enabled:
            kwargs["reasoning_effort"] = "high"
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif (
        provider == "openai"
        and run_context
        and run_context.thinking_enabled
        and is_openai_reasoning_model(model_name)
    ):
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
        # Retry transient APIConnectionError / 429 / 5xx so a blip doesn't kill long runs.
        "max_retries": 2,
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
