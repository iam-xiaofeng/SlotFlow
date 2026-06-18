"""Model provider discovery for the chat UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.chat.models import ModelCatalogRecord, ModelOptionRecord, ModelProvider, ModelProviderRecord


DEFAULT_CHAT_MODEL = "deepseek-v4-pro"

# Official endpoints used only when the matching *_BASE_URL env var is unset; users
# pointing at a third-party / self-hosted OpenAI-compatible gateway just set the env var.
PROVIDER_DEFAULT_BASE_URLS: dict[ModelProvider, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


@dataclass(frozen=True, slots=True)
class ProviderEnv:
    provider: ModelProvider
    api_key: str | None
    base_url: str


async def discover_model_catalog() -> ModelCatalogRecord:
    """Discover selectable models from configured provider credentials."""

    providers: list[ModelProviderRecord] = []
    for provider in ("deepseek", "openai", "anthropic"):
        providers.append(await discover_provider_models(load_provider_env(provider)))

    selectable_models = [
        model
        for provider in providers
        for model in provider.models
        if model.available
    ]
    default_model = choose_default_model(selectable_models)
    return ModelCatalogRecord(default_model=default_model, providers=providers)


async def discover_provider_models(provider_env: ProviderEnv) -> ModelProviderRecord:
    """Return provider status and models without leaking API keys."""

    if not provider_env.api_key:
        return ModelProviderRecord(
            provider=provider_env.provider,
            configured=False,
            base_url=provider_env.base_url,
            status="missing",
            message="API key is not configured.",
            models=[],
        )

    try:
        model_ids = await fetch_provider_model_ids(provider_env)
    except Exception as exc:  # noqa: BLE001 - expose only sanitized status to UI
        return ModelProviderRecord(
            provider=provider_env.provider,
            configured=True,
            base_url=provider_env.base_url,
            status="error",
            message=f"Model discovery failed: {exc.__class__.__name__}",
            models=[],
        )

    if not model_ids:
        return ModelProviderRecord(
            provider=provider_env.provider,
            configured=True,
            base_url=provider_env.base_url,
            status="error",
            message="Provider returned no chat models for this endpoint.",
            models=[],
        )

    return ModelProviderRecord(
        provider=provider_env.provider,
        configured=True,
        base_url=provider_env.base_url,
        status="available",
        models=[
            ModelOptionRecord(
                id=model_id,
                provider=provider_env.provider,
                label=model_label(provider_env.provider, model_id),
                source="api",
            )
            for model_id in sorted(set(model_ids))
        ],
    )


async def fetch_provider_model_ids(provider_env: ProviderEnv) -> list[str]:
    """Fetch model ids from an OpenAI-compatible or Anthropic model list API."""

    url = f"{provider_env.base_url.rstrip('/')}/models"
    headers = provider_headers(provider_env)
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    return parse_model_ids(provider_env.provider, response.json())


def provider_headers(provider_env: ProviderEnv) -> dict[str, str]:
    if provider_env.provider == "anthropic":
        return {
            "x-api-key": provider_env.api_key or "",
            "anthropic-version": "2023-06-01",
        }
    return {"Authorization": f"Bearer {provider_env.api_key}"}


def parse_model_ids(provider: ModelProvider, payload: Any) -> list[str]:
    """Normalize common provider model-list payloads."""

    if not isinstance(payload, dict):
        return []

    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []

    model_ids: list[str] = []
    for item in raw_models:
        model_id: str | None = None
        if isinstance(item, dict):
            raw_id = item.get("id") or item.get("name")
            if isinstance(raw_id, str):
                model_id = raw_id
        elif isinstance(item, str):
            model_id = item
        if model_id and is_chat_model_id(provider, model_id):
            model_ids.append(model_id)
    return model_ids


def is_chat_model_id(provider: ModelProvider, model_id: str) -> bool:
    """Filter obvious non-chat models from broad provider lists."""

    lowered = model_id.lower()
    if provider == "anthropic":
        return lowered.startswith("claude-")
    if provider == "deepseek":
        return lowered.startswith("deepseek-")
    blocked_terms = ("embedding", "whisper", "tts", "dall-e", "moderation")
    return not any(term in lowered for term in blocked_terms)


def choose_default_model(models: list[ModelOptionRecord]) -> str:
    model_ids = [model.id for model in models]
    if DEFAULT_CHAT_MODEL in model_ids:
        return DEFAULT_CHAT_MODEL
    if model_ids:
        return model_ids[0]
    return DEFAULT_CHAT_MODEL


def model_label(provider: ModelProvider, model_id: str) -> str:
    return f"{provider_title(provider)} · {model_id}"


def provider_title(provider: ModelProvider) -> str:
    return {
        "deepseek": "DeepSeek",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
    }[provider]


def load_provider_env(provider: ModelProvider) -> ProviderEnv:
    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL")
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL")
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("ANTHROPIC_BASE_URL")

    return ProviderEnv(
        provider=provider,
        api_key=api_key.strip() if api_key and api_key.strip() else None,
        base_url=(base_url.strip() if base_url and base_url.strip() else PROVIDER_DEFAULT_BASE_URLS[provider]),
    )
