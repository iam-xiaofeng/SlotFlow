"""Model provider discovery for the chat UI."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.chat.models import ModelCatalogRecord, ModelOptionRecord, ModelProvider, ModelProviderRecord


DEFAULT_CHAT_MODEL = "deepseek-v4-pro"

# Official endpoints used only when the matching *_BASE_URL env var is unset; users
# pointing at a third-party / self-hosted OpenAI-compatible gateway just set the env var.
# The `custom` provider is intentionally absent here: it has no official fallback and
# requires CUSTOM_BASE_URL (+ CUSTOM_API_KEY) to be set explicitly.
PROVIDER_DEFAULT_BASE_URLS: dict[ModelProvider, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


# User-Agent sent on every request to the `custom` relay (discovery + runtime).
# Many third-party OpenAI-compatible relays sit behind a Cloudflare WAF that blocks
# the OpenAI SDK's fingerprint UA (`AsyncOpenAI/Python <ver>` -> HTTP 403
# "Your request was blocked."), which silently breaks every non-DeepSeek model the
# relay serves. A neutral UA sidesteps the block. Live-verified against
# https://metapi.lilililwan.xyz/v1 (2026-06-30): `AsyncOpenAI/Python 2.40.0` -> 403
# for glm/kimi/qwen/minimax (and even deepseek-v4-pro); `python-httpx/0.28.1`,
# `curl/8.5.0`, `SlotFlow/1.0`, and empty UA all -> 200. The WAF is a blacklist on
# the OpenAI fingerprint, not a whitelist, so any neutral UA works.
RELAY_USER_AGENT = os.environ.get("SLOTFLOW_RELAY_USER_AGENT") or "SlotFlow/1.0"


def relay_request_headers(provider_env: "ProviderEnv", *, content_json: bool = False) -> dict[str, str]:
    """Discovery/probe headers that match the runtime's request fingerprint.

    Built on top of ``provider_headers`` (so Anthropic still gets ``x-api-key`` /
    ``anthropic-version`` and everyone else gets ``Authorization: Bearer``) and, for the
    ``custom`` relay only, adds a neutral ``User-Agent``. Many third-party relays sit
    behind a Cloudflare WAF that blocks the OpenAI SDK fingerprint UA
    (``AsyncOpenAI/Python <ver>`` -> HTTP 403 "Your request was blocked."), which would
    otherwise silently break every non-DeepSeek model the relay serves. Discovery (fetch
    + probe) MUST use the same UA the runtime uses, or the selector shows models the
    runtime then can't call ("shows but can't use").
    """

    headers = dict(provider_headers(provider_env))
    if provider_env.provider == "custom":
        headers["User-Agent"] = RELAY_USER_AGENT
    if content_json:
        headers["Content-Type"] = "application/json"
    return headers


@dataclass(frozen=True, slots=True)
class ProviderEnv:
    provider: ModelProvider
    api_key: str | None
    base_url: str
    validate_models: bool = False


async def discover_model_catalog() -> ModelCatalogRecord:
    """Discover selectable models from configured provider credentials."""

    # Discover providers concurrently so one slow/dead endpoint (e.g. a relay whose
    # /models hangs or 502s) doesn't stall the whole catalog behind it.
    providers = list(
        await asyncio.gather(
            *(
                discover_provider_models(load_provider_env(provider))
                for provider in ("deepseek", "openai", "anthropic", "custom")
            )
        )
    )

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

    if not provider_env.base_url:
        # Only the `custom` relay has no official fallback URL; surface the gap clearly.
        return ModelProviderRecord(
            provider=provider_env.provider,
            configured=False,
            base_url=None,
            status="missing",
            message="Base URL is not configured.",
            models=[],
        )

    manual_ids = manual_model_ids(provider_env.provider)
    if manual_ids is not None:
        model_ids = manual_ids
    else:
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

    if provider_env.validate_models:
        model_ids = await filter_usable_openai_compatible_models(provider_env, model_ids)
        if not model_ids:
            return ModelProviderRecord(
                provider=provider_env.provider,
                configured=True,
                base_url=provider_env.base_url,
                status="error",
                message="No discovered models passed the chat-completions availability check.",
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


async def filter_usable_openai_compatible_models(
    provider_env: ProviderEnv,
    model_ids: list[str],
) -> list[str]:
    """Keep only custom relay models that accept a minimal chat completion request."""

    unique_ids = sorted(set(model_ids))
    semaphore = asyncio.Semaphore(4)

    async def is_usable(model_id: str) -> bool:
        async with semaphore:
            return await probe_openai_compatible_chat_model(provider_env, model_id)

    results = await asyncio.gather(*(is_usable(model_id) for model_id in unique_ids))
    return [model_id for model_id, usable in zip(unique_ids, results, strict=True) if usable]


async def probe_openai_compatible_chat_model(
    provider_env: ProviderEnv,
    model_id: str,
) -> bool:
    """Return whether one OpenAI-compatible chat model is callable with this key."""

    url = f"{provider_env.base_url.rstrip('/')}/chat/completions"
    # Use the SAME headers the runtime sends (incl. neutral relay UA) so the probe and
    # the live chat path agree: a model shows in the selector only if the runtime can
    # actually call it with these exact headers.
    headers = relay_request_headers(provider_env, content_json=True)
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError:
        return False
    return response.status_code < 400


def manual_model_ids(provider: ModelProvider) -> list[str] | None:
    """Explicit, comma-separated model ids for a provider whose `/models` endpoint is
    broken or unsupported. Only `custom` supports this (CUSTOM_MODELS); when set we skip
    discovery entirely — which also avoids a slow/timing-out /models call.
    """

    if provider != "custom":
        return None
    raw = os.environ.get("CUSTOM_MODELS")
    if not raw or not raw.strip():
        return None
    return [model_id.strip() for model_id in raw.split(",") if model_id.strip()]


async def fetch_provider_model_ids(provider_env: ProviderEnv) -> list[str]:
    """Fetch model ids from an OpenAI-compatible or Anthropic model list API."""

    url = f"{provider_env.base_url.rstrip('/')}/models"
    # Same neutral relay UA on /models as on /chat/completions (see relay_request_headers).
    headers = relay_request_headers(provider_env)
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
        "custom": "Custom",
    }[provider]


def load_provider_env(provider: ModelProvider) -> ProviderEnv:
    validate_models = False
    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL")
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL")
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("ANTHROPIC_BASE_URL")
    else:
        # custom：用户自建 / 第三方 OpenAI 兼容中转站，URL 必须显式配置，没有官方回落。
        api_key = os.environ.get("CUSTOM_API_KEY")
        base_url = os.environ.get("CUSTOM_BASE_URL")
        # 中转站常列出本 key 无法调用的通用模型；默认用 /chat/completions 探针过滤。
        flag = os.environ.get("CUSTOM_VALIDATE_MODELS", "true").strip().lower()
        validate_models = flag not in {"0", "false", "no"}

    return ProviderEnv(
        provider=provider,
        api_key=api_key.strip() if api_key and api_key.strip() else None,
        base_url=(
            base_url.strip()
            if base_url and base_url.strip()
            else PROVIDER_DEFAULT_BASE_URLS.get(provider, "")
        ),
        validate_models=validate_models,
    )
