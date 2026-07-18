"""Create the single ChatLiteLLM model used by the SlotFlow runtime."""

from __future__ import annotations

import inspect
import json
import os
from typing import TYPE_CHECKING, Any

from app.chat import litellm_provider
from app.chat.models import ModelProvider, RunContext
from app.chat.runtime.env import load_positive_int_from_env

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

ChatLiteLLM = litellm_provider.ChatLiteLLM


DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 300
DEFAULT_MODEL_MAX_RETRIES = 5
DEFAULT_MODEL_RETRY_MIN_WAIT_SECONDS = 4
DEFAULT_MODEL_RETRY_MAX_WAIT_SECONDS = 30
DEFAULT_MODEL_RETRY_MULTIPLIER_SECONDS = 1
DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS = 128000
DEFAULT_CONTEXT_RESERVE_TOKENS = 16384


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
            "request_timeout": load_positive_int_from_env(
                "SLOTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS",
                default=DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS,
            ),
            "max_retries": load_positive_int_from_env(
                "SLOTFLOW_MODEL_MAX_RETRIES",
                default=DEFAULT_MODEL_MAX_RETRIES,
            ),
            "retry_min_wait_seconds": load_positive_int_from_env(
                "SLOTFLOW_MODEL_RETRY_MIN_WAIT_SECONDS",
                default=DEFAULT_MODEL_RETRY_MIN_WAIT_SECONDS,
            ),
            "retry_max_wait_seconds": load_positive_int_from_env(
                "SLOTFLOW_MODEL_RETRY_MAX_WAIT_SECONDS",
                default=DEFAULT_MODEL_RETRY_MAX_WAIT_SECONDS,
            ),
            "retry_multiplier_seconds": load_positive_int_from_env(
                "SLOTFLOW_MODEL_RETRY_MULTIPLIER_SECONDS",
                default=DEFAULT_MODEL_RETRY_MULTIPLIER_SECONDS,
            ),
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


def resolve_model_context_budget(
    model_name: str,
    *,
    provider: ModelProvider,
) -> tuple[int, int, str]:
    """Resolve a model window locally; custom relays can override per model."""

    raw_map = (os.environ.get("SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON") or "").strip()
    if raw_map:
        try:
            values = json.loads(raw_map)
        except json.JSONDecodeError as exc:
            raise ValueError("SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON must be valid JSON") from exc
        if not isinstance(values, dict):
            raise ValueError("SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON must be a JSON object")
        configured = values.get(model_name)
        if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
            window, source = configured, "env:model-map"
        elif configured is not None:
            raise ValueError(f"context window for {model_name} must be a positive integer")
        else:
            window, source = _catalog_context_window(model_name, provider)
    else:
        global_override = (os.environ.get("SLOTFLOW_MODEL_CONTEXT_WINDOW_TOKENS") or "").strip()
        if global_override:
            try:
                window = int(global_override)
            except ValueError as exc:
                raise ValueError("SLOTFLOW_MODEL_CONTEXT_WINDOW_TOKENS must be a positive integer") from exc
            if window <= 0:
                raise ValueError("SLOTFLOW_MODEL_CONTEXT_WINDOW_TOKENS must be a positive integer")
            source = "env:global"
        else:
            window, source = _catalog_context_window(model_name, provider)
    reserve = load_positive_int_from_env(
        "SLOTFLOW_CONTEXT_RESERVE_TOKENS",
        default=DEFAULT_CONTEXT_RESERVE_TOKENS,
    )
    if reserve >= window:
        raise ValueError("SLOTFLOW_CONTEXT_RESERVE_TOKENS must be smaller than the model context window")
    return window, window - reserve, source


def _catalog_context_window(model_name: str, provider: ModelProvider) -> tuple[int, str]:
    model_id = (
        model_name
        if provider == "custom"
        else litellm_provider.canonical_model_id(provider, model_name)
    )
    try:
        info = litellm_provider.litellm.get_model_info(model_id)
    except Exception:  # noqa: BLE001 - unknown custom relays use a conservative local default
        info = {}
    for key in ("max_input_tokens", "max_tokens"):
        value = info.get(key) if isinstance(info, dict) else None
        if isinstance(value, int) and value > 0:
            return value, f"litellm:{key}"
    default = load_positive_int_from_env(
        "SLOTFLOW_DEFAULT_CONTEXT_WINDOW_TOKENS",
        default=DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
    )
    return default, "default"
