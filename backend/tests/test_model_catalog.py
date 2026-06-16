"""Tests for frontend-facing model discovery."""

from __future__ import annotations

import pytest

import app.chat.model_catalog as model_catalog
from app.chat.model_catalog import discover_model_catalog


@pytest.mark.asyncio
async def test_model_catalog_marks_unconfigured_providers_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    catalog = await discover_model_catalog()

    assert catalog.default_model == "deepseek-v4-pro"
    assert [provider.provider for provider in catalog.providers] == [
        "deepseek",
        "openai",
        "anthropic",
    ]
    assert all(provider.status == "missing" for provider in catalog.providers)
    assert all(provider.models == [] for provider in catalog.providers)


@pytest.mark.asyncio
async def test_model_catalog_uses_provider_api_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def fake_fetch(provider_env: model_catalog.ProviderEnv) -> list[str]:
        assert provider_env.provider == "deepseek"
        return ["deepseek-v4-pro", "deepseek-v4-flash"]

    monkeypatch.setattr(model_catalog, "fetch_provider_model_ids", fake_fetch)

    catalog = await discover_model_catalog()
    deepseek = catalog.providers[0]

    assert catalog.default_model == "deepseek-v4-pro"
    assert deepseek.status == "available"
    assert [model.id for model in deepseek.models] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]


@pytest.mark.asyncio
async def test_model_catalog_falls_back_when_provider_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def broken_fetch(provider_env: model_catalog.ProviderEnv) -> list[str]:
        assert provider_env.provider == "deepseek"
        raise RuntimeError("offline")

    monkeypatch.setattr(model_catalog, "fetch_provider_model_ids", broken_fetch)

    catalog = await discover_model_catalog()
    deepseek = catalog.providers[0]

    assert catalog.default_model == "deepseek-v4-pro"
    assert deepseek.status == "fallback"
    assert "deepseek-v4-pro" in [model.id for model in deepseek.models]
