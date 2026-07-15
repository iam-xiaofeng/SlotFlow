"""Frontend model catalog derived from LiteLLM metadata."""

from __future__ import annotations

import asyncio
import os

from app.chat import litellm_provider
from app.chat.models import ModelCatalogRecord, ModelOptionRecord, ModelProviderRecord


DEFAULT_CHAT_MODEL = "deepseek/deepseek-v4-pro"


async def discover_model_catalog() -> ModelCatalogRecord:
    """Return configured LiteLLM providers and Agent-capable models."""

    native_provider_names = litellm_provider.configured_native_provider_names()
    providers = list(
        await asyncio.gather(
            *(
                asyncio.to_thread(discover_native_provider_models, provider)
                for provider in native_provider_names
            ),
            discover_custom_provider_models(),
        )
    )
    selectable_models = [
        model
        for provider in providers
        for model in provider.models
        if model.available
    ]
    return ModelCatalogRecord(
        default_model=choose_default_model(selectable_models),
        providers=providers,
    )


def discover_native_provider_models(provider: str) -> ModelProviderRecord:
    """Build one configured provider record from LiteLLM's bundled catalog."""

    model_ids = litellm_provider.agent_models_for_provider(provider)
    if not model_ids:
        return ModelProviderRecord(
            provider=provider,
            configured=True,
            status="error",
            message="LiteLLM reports no tool-calling chat models for this provider.",
            models=[],
        )
    return ModelProviderRecord(
        provider=provider,
        configured=True,
        status="available",
        models=[
            ModelOptionRecord(
                id=model_id,
                provider=provider,
                label=model_label(provider, model_id),
                source="litellm",
            )
            for model_id in model_ids
        ],
    )


async def discover_custom_provider_models() -> ModelProviderRecord:
    """Discover the explicitly configured OpenAI-compatible custom relay."""

    api_key = read_env("CUSTOM_API_KEY")
    api_base = read_env("CUSTOM_BASE_URL")
    if not api_key or not api_base:
        return ModelProviderRecord(
            provider="custom",
            configured=False,
            base_url=api_base,
            status="missing",
            message="CUSTOM_API_KEY and CUSTOM_BASE_URL are required.",
            models=[],
        )

    manual_models = manual_custom_model_ids()
    try:
        model_ids = manual_models or await asyncio.to_thread(
            litellm_provider.discover_custom_openai_models,
            api_key=api_key,
            api_base=api_base,
        )
    except Exception as exc:  # noqa: BLE001 - expose only a sanitized status
        return ModelProviderRecord(
            provider="custom",
            configured=True,
            base_url=api_base,
            status="error",
            message=f"Model discovery failed: {exc.__class__.__name__}",
            models=[],
        )

    if not model_ids:
        return ModelProviderRecord(
            provider="custom",
            configured=True,
            base_url=api_base,
            status="error",
            message="Custom relay returned no models.",
            models=[],
        )
    return ModelProviderRecord(
        provider="custom",
        configured=True,
        base_url=api_base,
        status="available",
        models=[
            ModelOptionRecord(
                id=model_id,
                provider="custom",
                label=model_label("custom", model_id),
                source="api" if manual_models is None else "environment",
            )
            for model_id in sorted(set(model_ids))
        ],
    )


def manual_custom_model_ids() -> tuple[str, ...] | None:
    """Return CUSTOM_MODELS when relay discovery is unavailable."""

    raw = read_env("CUSTOM_MODELS")
    if not raw:
        return None
    return tuple(sorted({model_id.strip() for model_id in raw.split(",") if model_id.strip()}))


def choose_default_model(models: list[ModelOptionRecord]) -> str:
    model_ids = [model.id for model in models]
    if DEFAULT_CHAT_MODEL in model_ids:
        return DEFAULT_CHAT_MODEL
    if model_ids:
        return model_ids[0]
    return DEFAULT_CHAT_MODEL


def model_label(provider: str, model_id: str) -> str:
    display_model_id = model_id.removeprefix(f"{provider}/")
    return f"{provider} · {display_model_id}"


def read_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None
