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
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    catalog = await discover_model_catalog()

    assert catalog.default_model == "deepseek-v4-pro"
    assert [provider.provider for provider in catalog.providers] == [
        "deepseek",
        "openai",
        "anthropic",
        "custom",
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
async def test_model_catalog_reports_error_when_provider_discovery_fails(
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

    assert deepseek.status == "error"
    assert deepseek.models == []


def test_load_provider_env_prefers_third_party_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """base_url 读自环境变量，支持第三方 / 自建网关；未设时回落官方地址。"""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://gateway.internal/v1")
    custom = model_catalog.load_provider_env("deepseek")
    assert custom.base_url == "https://gateway.internal/v1"

    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    default = model_catalog.load_provider_env("deepseek")
    assert default.base_url == model_catalog.PROVIDER_DEFAULT_BASE_URLS["deepseek"]


def test_fetch_url_and_headers_match_provider() -> None:
    """探测 URL = {base_url}/models；anthropic 用 x-api-key，其余用 Bearer。"""

    anthropic_env = model_catalog.ProviderEnv(
        provider="anthropic", api_key="ak", base_url="https://api.anthropic.com/v1"
    )
    openai_env = model_catalog.ProviderEnv(
        provider="openai", api_key="ok", base_url="https://api.openai.com/v1"
    )
    assert model_catalog.provider_headers(anthropic_env)["x-api-key"] == "ak"
    assert "Authorization" not in model_catalog.provider_headers(anthropic_env)
    assert model_catalog.provider_headers(openai_env)["Authorization"] == "Bearer ok"


def test_parse_model_ids_filters_non_chat_models() -> None:
    """从 /models 响应里过滤掉非对话模型（embedding/whisper 等）并按产家收敛。"""

    openai_payload = {
        "data": [
            {"id": "gpt-4.1"},
            {"id": "text-embedding-3-small"},
            {"id": "whisper-1"},
        ]
    }
    assert model_catalog.parse_model_ids("openai", openai_payload) == ["gpt-4.1"]

    anthropic_payload = {"data": [{"id": "claude-sonnet-4-5"}, {"id": "not-a-claude"}]}
    assert model_catalog.parse_model_ids("anthropic", anthropic_payload) == ["claude-sonnet-4-5"]

    # custom relays serve mixed families over the OpenAI schema: keep claude/qwen, drop embeddings.
    custom_payload = {
        "data": [
            {"id": "claude-3-5-sonnet"},
            {"id": "qwen-plus"},
            {"id": "text-embedding-3-small"},
        ]
    }
    assert model_catalog.parse_model_ids("custom", custom_payload) == [
        "claude-3-5-sonnet",
        "qwen-plus",
    ]


@pytest.mark.asyncio
async def test_custom_provider_missing_base_url_is_marked_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """custom 中转站没有官方回落地址：只配 key 不配 URL 时应明确标为 missing。"""

    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    catalog = await discover_model_catalog()
    custom = next(provider for provider in catalog.providers if provider.provider == "custom")

    assert custom.status == "missing"
    assert custom.models == []


@pytest.mark.asyncio
async def test_custom_provider_lists_relay_models_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配好 CUSTOM_BASE_URL+KEY 后，中转站的模型应列出并标注 provider=custom。"""

    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")

    async def fake_fetch(provider_env: model_catalog.ProviderEnv) -> list[str]:
        assert provider_env.provider == "custom"
        assert provider_env.base_url == "http://relay.local/v1"
        return ["claude-3-5-sonnet", "qwen-plus"]

    monkeypatch.setattr(model_catalog, "fetch_provider_model_ids", fake_fetch)

    catalog = await discover_model_catalog()
    custom = next(provider for provider in catalog.providers if provider.provider == "custom")

    assert custom.status == "available"
    assert [model.id for model in custom.models] == ["claude-3-5-sonnet", "qwen-plus"]
    assert all(model.provider == "custom" for model in custom.models)
