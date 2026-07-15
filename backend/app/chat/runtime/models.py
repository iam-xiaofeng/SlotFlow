"""Create the single ChatLiteLLM model used by the SlotFlow runtime."""

from __future__ import annotations

import inspect
import os
from typing import TYPE_CHECKING, Any

from app.chat import litellm_provider
from app.chat.models import ModelProvider, RunContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

ChatLiteLLM = litellm_provider.ChatLiteLLM


def create_model_for_context(
    model_factory: "Callable[..., BaseChatModel]",
    *,
    model_name: str,
    run_context: RunContext,
) -> "BaseChatModel":
    """Create a model, passing run context when the factory supports it."""

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
    """Create one environment-configured LiteLLM model for this run."""

    resolved_provider = provider or resolve_model_provider(model_name, run_context)
    return ChatLiteLLM(
        **build_litellm_model_kwargs(
            model_name=model_name,
            provider=resolved_provider,
            run_context=run_context,
        )
    )


def resolve_model_provider(
    model_name: str,
    run_context: RunContext | None,
) -> ModelProvider:
    """Prefer catalog provenance and otherwise ask LiteLLM to resolve the model."""

    if run_context is not None and run_context.model_provider:
        return run_context.model_provider
    return infer_model_provider(model_name)


def infer_model_provider(model_name: str) -> ModelProvider:
    """Resolve provider only through LiteLLM; unqualified unknown ids are invalid."""

    return litellm_provider.infer_provider(model_name)


def build_litellm_model_kwargs(
    *,
    model_name: str,
    provider: ModelProvider,
    run_context: RunContext | None = None,
) -> dict[str, Any]:
    """Build ChatLiteLLM settings from LiteLLM ids and capability metadata."""

    if provider == "custom":
        api_key = read_required_env("CUSTOM_API_KEY")
        api_base = read_required_env("CUSTOM_BASE_URL")
        kwargs: dict[str, Any] = {
            "model": model_name,
            "custom_llm_provider": "openai",
            "api_key": api_key,
            "api_base": api_base,
            "extra_headers": {
                "User-Agent": litellm_provider.CUSTOM_RELAY_USER_AGENT,
            },
        }
        capability_model_id = model_name
    else:
        capability_model_id = litellm_provider.canonical_model_id(provider, model_name)
        kwargs = {"model": capability_model_id}

    kwargs.update(
        {
            "streaming": True,
            "request_timeout": 30,
            "max_retries": 2,
        }
    )

    model_kwargs: dict[str, Any] = {"_skip_responses_api_bridge": True}
    if provider != "custom" and litellm_provider.supports_reasoning_effort(
        capability_model_id
    ):
        model_kwargs["reasoning_effort"] = (
            "high" if run_context and run_context.thinking_enabled else "none"
        )
    kwargs["model_kwargs"] = model_kwargs
    return kwargs


def read_required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for custom runtime")
    return value
