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


def test_relay_request_headers_adds_neutral_user_agent_for_custom_only() -> None:
    """custom 中转站的发现/探针请求必须带中性 User-Agent（与 runtime 同一 UA）。

    根因（live-verified 2026-06-30）：第三方中转站 Cloudflare WAF 按 OpenAI SDK 指纹 UA
    `AsyncOpenAI/Python <ver>` 拦截（403 "Your request was blocked."）。发现探针与 runtime 必须
    用同一个中性 UA，否则会出现"选择器里能显示但实际调用 403"。DeepSeek/OpenAI/Anthropic 官方
    端点不得加 UA（默认 UA 不被 WAF 拦截，且 Anthropic 用 x-api-key 不用 Bearer）。
    """

    custom_env = model_catalog.ProviderEnv(
        provider="custom", api_key="ck", base_url="https://relay.local/v1"
    )
    deepseek_env = model_catalog.ProviderEnv(
        provider="deepseek", api_key="dk", base_url="https://api.deepseek.com"
    )
    openai_env = model_catalog.ProviderEnv(
        provider="openai", api_key="ok", base_url="https://api.openai.com/v1"
    )
    anthropic_env = model_catalog.ProviderEnv(
        provider="anthropic", api_key="ak", base_url="https://api.anthropic.com/v1"
    )

    # custom：带中性 UA 且非 OpenAI SDK 指纹
    custom_headers = model_catalog.relay_request_headers(custom_env, content_json=True)
    assert custom_headers["User-Agent"] == model_catalog.RELAY_USER_AGENT
    assert "AsyncOpenAI" not in custom_headers["User-Agent"]
    assert custom_headers["Authorization"] == "Bearer ck"
    assert custom_headers["Content-Type"] == "application/json"

    # deepseek/openai 官方端点：不加 UA（用 SDK 默认）
    assert "User-Agent" not in model_catalog.relay_request_headers(deepseek_env)
    assert "User-Agent" not in model_catalog.relay_request_headers(openai_env)

    # anthropic：保留 x-api-key / anthropic-version，不加 Bearer 也不加 UA
    anthropic_headers = model_catalog.relay_request_headers(anthropic_env)
    assert anthropic_headers["x-api-key"] == "ak"
    assert anthropic_headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in anthropic_headers
    assert "User-Agent" not in anthropic_headers


def test_relay_user_agent_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """SLOTFLOW_RELAY_USER_AGENT 可覆盖默认中性 UA（应对中转站更挑剔的 WAF 规则）。"""

    import importlib

    # Reload 读取一次 env 得到覆盖值。必须在 monkeypatch 还原 env *之前* 把模块
    # 常量恢复回默认值，否则污染后续导入 RELAY_USER_AGENT 的测试（reload 只重跑
    # model_catalog 本体，不会更新 models.py 里已绑定的副本，两边值不一致即断言失败）。
    monkeypatch.setenv("SLOTFLOW_RELAY_USER_AGENT", "my-relay-client/2.0")
    assert importlib.reload(model_catalog).RELAY_USER_AGENT == "my-relay-client/2.0"
    monkeypatch.delenv("SLOTFLOW_RELAY_USER_AGENT", raising=False)
    importlib.reload(model_catalog)  # restore default before monkeypatch finalizes
    assert model_catalog.RELAY_USER_AGENT == "SlotFlow/1.0"


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
    monkeypatch.setenv("CUSTOM_VALIDATE_MODELS", "false")

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


@pytest.mark.asyncio
async def test_custom_provider_uses_manual_models_without_hitting_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOM_MODELS 让 /models 坏掉的中转站也能填充选择器；设置后不得再调 /models。"""

    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")
    monkeypatch.setenv("CUSTOM_MODELS", "claude-3-5-sonnet, gpt-4o ,")
    monkeypatch.setenv("CUSTOM_VALIDATE_MODELS", "false")

    async def fail_fetch(provider_env: model_catalog.ProviderEnv) -> list[str]:
        raise AssertionError("must not hit /models when CUSTOM_MODELS is set")

    monkeypatch.setattr(model_catalog, "fetch_provider_model_ids", fail_fetch)

    catalog = await discover_model_catalog()
    custom = next(provider for provider in catalog.providers if provider.provider == "custom")

    assert custom.status == "available"
    assert [model.id for model in custom.models] == ["claude-3-5-sonnet", "gpt-4o"]


@pytest.mark.asyncio
async def test_custom_provider_hides_models_that_fail_chat_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some relays list generic model ids that this key cannot call; hide those."""

    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")

    async def fake_fetch(provider_env: model_catalog.ProviderEnv) -> list[str]:
        assert provider_env.provider == "custom"
        return ["gpt-5.5", "qwen-plus"]

    async def fake_probe(provider_env: model_catalog.ProviderEnv, model_id: str) -> bool:
        assert provider_env.provider == "custom"
        return model_id == "qwen-plus"

    monkeypatch.setattr(model_catalog, "fetch_provider_model_ids", fake_fetch)
    monkeypatch.setattr(model_catalog, "probe_openai_compatible_chat_model", fake_probe)

    catalog = await discover_model_catalog()
    custom = next(provider for provider in catalog.providers if provider.provider == "custom")

    assert custom.status == "available"
    assert [model.id for model in custom.models] == ["qwen-plus"]
