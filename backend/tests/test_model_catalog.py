"""Tests for the LiteLLM-derived frontend model catalog."""

from __future__ import annotations

import pytest

import app.chat.litellm_provider as litellm_provider
from app.chat.model_catalog import discover_model_catalog


def clear_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CUSTOM_API_KEY", "CUSTOM_BASE_URL", "CUSTOM_MODELS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_model_catalog_has_no_fake_native_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_custom_env(monkeypatch)
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: (),
    )

    catalog = await discover_model_catalog()

    assert catalog.default_model == "deepseek/deepseek-v4-pro"
    assert [provider.provider for provider in catalog.providers] == ["custom"]
    assert catalog.providers[0].status == "missing"
    assert catalog.providers[0].models == []


@pytest.mark.asyncio
async def test_model_catalog_lists_every_configured_native_provider_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_custom_env(monkeypatch)
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: ("gemini", "mistral"),
    )
    catalog_models = {
        "gemini": (
            "gemini/gemini-2.5-flash",
            "gemini/gemini-2.5-pro",
        ),
        "mistral": ("mistral/mistral-large-latest",),
    }
    monkeypatch.setattr(
        litellm_provider,
        "agent_models_for_provider",
        lambda provider: catalog_models[provider],
    )

    catalog = await discover_model_catalog()

    assert [provider.provider for provider in catalog.providers] == [
        "gemini",
        "mistral",
        "custom",
    ]
    assert [model.id for model in catalog.providers[0].models] == [
        "gemini/gemini-2.5-flash",
        "gemini/gemini-2.5-pro",
    ]
    assert catalog.providers[0].models[0].source == "litellm"
    assert catalog.default_model == "gemini/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_model_catalog_prefers_slotflow_default_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_custom_env(monkeypatch)
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: ("deepseek",),
    )
    monkeypatch.setattr(
        litellm_provider,
        "agent_models_for_provider",
        lambda provider: (
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro",
        ),
    )

    catalog = await discover_model_catalog()

    assert catalog.default_model == "deepseek/deepseek-v4-pro"


@pytest.mark.asyncio
async def test_native_provider_without_agent_models_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_custom_env(monkeypatch)
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: ("example",),
    )
    monkeypatch.setattr(
        litellm_provider,
        "agent_models_for_provider",
        lambda provider: (),
    )

    catalog = await discover_model_catalog()
    provider = catalog.providers[0]

    assert provider.provider == "example"
    assert provider.status == "error"
    assert provider.models == []


def test_configured_native_providers_come_from_litellm_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider.litellm,
        "get_valid_models",
        lambda **kwargs: ["gpt-5", "gemini/gemini-2.5-pro"],
    )
    monkeypatch.setattr(
        litellm_provider.litellm,
        "models_by_provider",
        {
            "openai": ["gpt-5", "text-embedding-3-small"],
            "gemini": ["gemini/gemini-2.5-pro"],
            "mistral": ["mistral/mistral-large-latest"],
        },
    )
    monkeypatch.setattr(
        litellm_provider,
        "agent_models_for_provider",
        lambda provider: ("agent-model",) if provider != "mistral" else (),
    )

    assert litellm_provider.configured_native_provider_names() == (
        "gemini",
        "openai",
    )


def test_agent_model_filter_uses_litellm_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    litellm_provider.agent_models_for_provider.cache_clear()
    monkeypatch.setitem(
        litellm_provider.litellm.models_by_provider,
        "example",
        ["chat-tools", "chat-no-tools", "embedding"],
    )
    monkeypatch.setattr(
        litellm_provider.litellm,
        "get_model_info",
        lambda model: {
            "mode": "embedding" if model.endswith("embedding") else "chat"
        },
    )
    monkeypatch.setattr(
        litellm_provider.litellm,
        "supports_function_calling",
        lambda model: model.endswith("chat-tools"),
    )

    try:
        assert litellm_provider.agent_models_for_provider("example") == (
            "example/chat-tools",
        )
    finally:
        litellm_provider.agent_models_for_provider.cache_clear()


def test_custom_discovery_delegates_to_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_valid_models(**kwargs: object) -> list[str]:
        captured.update(kwargs)
        return ["openai/qwen-plus", "claude-3-5-sonnet"]

    monkeypatch.setattr(
        litellm_provider.litellm,
        "get_valid_models",
        fake_get_valid_models,
    )

    models = litellm_provider.discover_custom_openai_models(
        api_key="key",
        api_base="https://relay.local/v1",
    )

    assert models == ("claude-3-5-sonnet", "qwen-plus")
    assert captured["check_provider_endpoint"] is True
    assert captured["custom_llm_provider"] == "openai"
    params = captured["litellm_params"]
    assert params.api_key == "key"
    assert params.api_base == "https://relay.local/v1"
    assert params.extra_headers == {
        "User-Agent": litellm_provider.CUSTOM_RELAY_USER_AGENT
    }


@pytest.mark.asyncio
async def test_custom_provider_requires_key_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: (),
    )
    monkeypatch.setenv("CUSTOM_API_KEY", "key")
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    catalog = await discover_model_catalog()
    custom = catalog.providers[0]

    assert custom.provider == "custom"
    assert custom.status == "missing"
    assert custom.models == []


@pytest.mark.asyncio
async def test_custom_provider_uses_litellm_endpoint_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: (),
    )
    monkeypatch.setenv("CUSTOM_API_KEY", "key")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://relay.local/v1")
    calls: list[tuple[str, str]] = []

    def fake_discover(*, api_key: str, api_base: str) -> tuple[str, ...]:
        calls.append((api_key, api_base))
        return ("claude-3-5-sonnet", "qwen-plus")

    monkeypatch.setattr(
        litellm_provider,
        "discover_custom_openai_models",
        fake_discover,
    )

    catalog = await discover_model_catalog()
    custom = catalog.providers[0]

    assert calls == [("key", "https://relay.local/v1")]
    assert custom.status == "available"
    assert [model.id for model in custom.models] == [
        "claude-3-5-sonnet",
        "qwen-plus",
    ]
    assert all(model.provider == "custom" for model in custom.models)


@pytest.mark.asyncio
async def test_custom_provider_manual_models_skip_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: (),
    )
    monkeypatch.setenv("CUSTOM_API_KEY", "key")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://relay.local/v1")
    monkeypatch.setenv("CUSTOM_MODELS", "qwen-plus, gpt-5, qwen-plus")
    monkeypatch.setattr(
        litellm_provider,
        "discover_custom_openai_models",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected discovery")),
    )

    catalog = await discover_model_catalog()
    custom = catalog.providers[0]

    assert [model.id for model in custom.models] == ["gpt-5", "qwen-plus"]
    assert all(model.source == "environment" for model in custom.models)


@pytest.mark.asyncio
async def test_custom_provider_sanitizes_discovery_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "configured_native_provider_names",
        lambda: (),
    )
    monkeypatch.setenv("CUSTOM_API_KEY", "key")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://relay.local/v1")
    monkeypatch.setattr(
        litellm_provider,
        "discover_custom_openai_models",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )

    catalog = await discover_model_catalog()
    custom = catalog.providers[0]

    assert custom.status == "error"
    assert custom.message == "Model discovery failed: RuntimeError"
    assert "secret" not in custom.message
