"""SlotFlow 本地 runtime 装配层测试。

这一层不直接依赖外部应用包，而是把 SlotFlow 自己需要的最小运行时装配收拢出来：

- 创建真实 LangGraph/DeepSeek-compatible agent graph
- 显式挂接 checkpointer
- 保持 AgentAdapter / AgentEvent 外部契约不变
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.chat.agent_adapter import collect_agent_events
from app.chat.models import ChatStreamRequest
import app.chat.runtime as runtime_module
from app.chat.run_config import build_run_config
from app.chat.runtime import (
    DEFAULT_CHECKPOINTER_SQLITE_PATH,
    DeepSeekChatModel,
    RuntimeBackedAgentAdapter,
    SlotFlowRuntimeConfig,
    aclose_checkpointer,
    create_async_checkpointer,
    create_chat_model,
    create_checkpointer,
    load_runtime_config_from_env,
    build_openai_compatible_model_kwargs,
)
from app.harness.mcp import MultiServerMcpToolProvider, SlotFlowMcpConfig, SlotFlowMcpServerConfig
from app.harness.middleware import SlotFlowMiddlewareConfig


def _bundle(
    *,
    thread_id: str = "thread_runtime",
    run_id: str = "run_runtime",
    request: ChatStreamRequest | None = None,
):
    return build_run_config(
        thread_id=thread_id,
        run_id=run_id,
        request=request or ChatStreamRequest(message="解释 runtime"),
    )


class AsyncCapturingMcpToolProvider:
    """Test MCP provider that records async preload calls."""

    def __init__(self) -> None:
        self.aload_calls: list[SlotFlowMcpConfig] = []
        self.load_calls: list[SlotFlowMcpConfig] = []

    async def aload_tools(self, config: SlotFlowMcpConfig):
        self.aload_calls.append(config)
        return []

    def load_tools(self, config: SlotFlowMcpConfig):
        self.load_calls.append(config)
        return []


def isolate_user_config_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLOTFLOW_MCP_CONFIG_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("SLOTFLOW_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("SLOTFLOW_SKILLS_CONFIG_PATH", str(tmp_path / "skills.json"))


def test_load_runtime_config_from_env_uses_small_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """默认配置只描述真实 runtime，不再携带测试/静态模式。"""

    monkeypatch.delenv("SLOTFLOW_CHECKPOINTER_BACKEND", raising=False)
    monkeypatch.delenv("SLOTFLOW_CHECKPOINTER_SQLITE_PATH", raising=False)
    monkeypatch.delenv("SLOTFLOW_CHECKPOINTER_POSTGRES_URI", raising=False)
    monkeypatch.delenv("SLOTFLOW_CHECKPOINTER_SETUP", raising=False)
    monkeypatch.delenv("SLOTFLOW_SYSTEM_PROMPT", raising=False)
    isolate_user_config_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("SLOTFLOW_ENABLED_SKILLS", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_SERVERS", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_CONFIG_JSON", raising=False)
    monkeypatch.delenv("SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_ARTIFACT_DISCOVERY_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUMMARIZATION_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUMMARIZATION_KEEP_MESSAGES", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUMMARIZATION_TRIM_TOKENS", raising=False)
    monkeypatch.delenv("SLOTFLOW_LONG_TERM_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION", raising=False)
    monkeypatch.delenv("SLOTFLOW_SKILLS_PREFLIGHT_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_CLARIFY_GATE", raising=False)
    monkeypatch.delenv("SLOTFLOW_UPLOADS_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_TODO_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUBAGENT_LIMIT", raising=False)
    monkeypatch.delenv("SLOTFLOW_SUBAGENT_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("SLOTFLOW_MEMORY_SQLITE_PATH", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES", raising=False)

    config = load_runtime_config_from_env()

    assert config == SlotFlowRuntimeConfig(
        model_name="deepseek-v4-pro",
        checkpointer_backend="memory",
        checkpointer_sqlite_path=DEFAULT_CHECKPOINTER_SQLITE_PATH,
        skills_root=tmp_path / "skills",
    )


def test_load_runtime_config_from_env_reads_checkpointer_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """runtime 可以从环境变量读取持久化 checkpointer 配置。"""

    sqlite_path = tmp_path / "checkpoints.sqlite3"
    monkeypatch.setenv("SLOTFLOW_CHECKPOINTER_BACKEND", "sqlite")
    monkeypatch.setenv("SLOTFLOW_CHECKPOINTER_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv(
        "SLOTFLOW_CHECKPOINTER_POSTGRES_URI",
        "postgresql://slotflow:slotflow@localhost:5432/slotflow",
    )
    monkeypatch.setenv("SLOTFLOW_CHECKPOINTER_SETUP", "false")

    config = load_runtime_config_from_env()

    assert config.checkpointer_backend == "sqlite"
    assert config.checkpointer_sqlite_path == sqlite_path
    assert config.checkpointer_postgres_uri == "postgresql://slotflow:slotflow@localhost:5432/slotflow"
    assert config.checkpointer_setup is False


def test_load_runtime_config_from_env_reads_mcp_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime only reads MCP config; actual tool loading remains in harness."""

    isolate_user_config_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("SLOTFLOW_MCP_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_MCP_SERVERS", "filesystem, search")
    monkeypatch.delenv("SLOTFLOW_MCP_CONFIG_JSON", raising=False)

    config = load_runtime_config_from_env()

    assert config.mcp_config == SlotFlowMcpConfig(
        enabled=True,
        servers=(SlotFlowMcpServerConfig(name="search"),),
    )
    assert config.mcp_tool_provider is None


def test_load_runtime_config_from_env_reads_harness_feature_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Feature flags in backend/.env map to the graph behavior config."""

    isolate_user_config_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_ARTIFACT_DISCOVERY_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_SUMMARIZATION_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS", "1200")
    monkeypatch.setenv("SLOTFLOW_SUMMARIZATION_KEEP_MESSAGES", "8")
    monkeypatch.setenv("SLOTFLOW_SUMMARIZATION_TRIM_TOKENS", "900")
    monkeypatch.setenv("SLOTFLOW_LONG_TERM_MEMORY_ENABLED", "false")
    monkeypatch.setenv("SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION", "false")
    monkeypatch.setenv("SLOTFLOW_SKILLS_PREFLIGHT_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_CLARIFY_GATE", "false")
    monkeypatch.setenv("SLOTFLOW_UPLOADS_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_TODO_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_SUBAGENT_LIMIT", "false")
    monkeypatch.setenv("SLOTFLOW_SUBAGENT_MAX_CONCURRENT", "1")

    config = load_runtime_config_from_env()

    assert config.middleware_config.runtime_summary_enabled is False
    assert config.middleware_config.artifact_discovery_enabled is False
    assert config.middleware_config.summarization_enabled is False
    assert config.middleware_config.summarization_trigger_tokens == 1200
    assert config.middleware_config.summarization_keep_messages == 8
    assert config.middleware_config.summarization_trim_tokens == 900
    assert config.middleware_config.long_term_memory_enabled is False
    assert config.middleware_config.proactive_memory_extraction_enabled is False
    assert config.middleware_config.skills_preflight_enabled is False
    assert config.middleware_config.clarify_gate_enabled is False
    assert config.middleware_config.uploads_enabled is False
    assert config.middleware_config.todo_enabled is False
    assert config.middleware_config.subagent_limit_enabled is False
    assert config.middleware_config.subagent_max_concurrent == 1
    assert config.memory_store is None


def test_deepseek_thinking_kwargs_follow_run_context() -> None:
    pro_context = _bundle(
        request=ChatStreamRequest(message="复杂分析", mode="pro")
    ).context
    no_thinking_context = _bundle(
        request=ChatStreamRequest(
            message="复杂分析但关闭原生思考",
            mode="pro",
            thinking_enabled=False,
        )
    ).context
    flash_context = _bundle(
        request=ChatStreamRequest(message="快速回答", mode="flash")
    ).context

    pro_kwargs = build_openai_compatible_model_kwargs(
        model_name="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
        run_context=pro_context,
    )
    no_thinking_kwargs = build_openai_compatible_model_kwargs(
        model_name="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
        run_context=no_thinking_context,
    )
    flash_kwargs = build_openai_compatible_model_kwargs(
        model_name="deepseek-v4-flash",
        api_key="key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
        run_context=flash_context,
    )

    assert pro_kwargs["reasoning_effort"] == "high"
    assert pro_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" not in no_thinking_kwargs
    assert no_thinking_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in flash_kwargs
    assert flash_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_openai_reasoning_effort_only_for_reasoning_models() -> None:
    """OpenAI 推理档：仅 o 系列 / gpt-5 才注入 reasoning_effort，gpt-4* 不注入。"""

    from app.chat.runtime.models import is_openai_reasoning_model

    pro_context = _bundle(
        request=ChatStreamRequest(message="复杂分析", model_name="o3", mode="pro")
    ).context
    reasoning_kwargs = build_openai_compatible_model_kwargs(
        model_name="o3",
        api_key="key",
        base_url=None,
        provider="openai",
        run_context=pro_context,
    )
    plain_kwargs = build_openai_compatible_model_kwargs(
        model_name="gpt-4.1",
        api_key="key",
        base_url=None,
        provider="openai",
        run_context=pro_context,
    )

    assert reasoning_kwargs["reasoning_effort"] == "high"
    assert "extra_body" not in reasoning_kwargs
    assert "reasoning_effort" not in plain_kwargs
    assert is_openai_reasoning_model("o3")
    assert not is_openai_reasoning_model("gpt-4.1")


def test_custom_provider_kwargs_send_no_deepseek_thinking_flags() -> None:
    """custom 中转站协议未知：不发 deepseek 的 extra_body.thinking，避免被未知网关 400。"""

    pro_context = _bundle(
        request=ChatStreamRequest(
            message="x", model_name="qwen-plus", mode="pro", provider="custom"
        )
    ).context
    kwargs = build_openai_compatible_model_kwargs(
        model_name="qwen-plus",
        api_key="key",
        base_url="http://relay.local/v1",
        provider="custom",
        run_context=pro_context,
    )

    assert "extra_body" not in kwargs
    assert "reasoning_effort" not in kwargs
    assert kwargs["base_url"] == "http://relay.local/v1"


def test_custom_provider_kwargs_override_relay_user_agent() -> None:
    """custom 中转站：注入中性 User-Agent，覆盖 OpenAI SDK 的 `AsyncOpenAI/Python` 指纹 UA。

    根因（live-verified 2026-06-30 against https://metapi.lilililwan.xyz/v1）：很多第三方
    中转站前置 Cloudflare WAF 按 OpenAI SDK 的 `User-Agent: AsyncOpenAI/Python <ver>` 指纹拦截
    （HTTP 403 "Your request was blocked."），导致非 deepseek 系列模型"能显示但用不了"——
    因为发现探针用裸 httpx（中性 UA）能过，而真正跑对话的 OpenAI SDK 客户端用被拦 UA。
    ChatDeepSeek 与 ChatOpenAI 共用同一 `openai.AsyncOpenAI` 客户端，都会注入该 UA，故必须在此覆盖。
    中转站是黑名单（任何非 OpenAI 指纹的 UA 都放行），不是白名单。
    """

    from app.chat.model_catalog import RELAY_USER_AGENT

    context = _bundle(
        request=ChatStreamRequest(
            message="x", model_name="glm-5.2", mode="pro", provider="custom"
        )
    ).context
    custom_kwargs = build_openai_compatible_model_kwargs(
        model_name="glm-5.2",
        api_key="key",
        base_url="http://relay.local/v1",
        provider="custom",
        run_context=context,
    )
    assert custom_kwargs["default_headers"] == {"User-Agent": RELAY_USER_AGENT}
    # 中转站 UA 必须是中性、非 OpenAI SDK 指纹：
    assert "AsyncOpenAI" not in RELAY_USER_AGENT

    # DeepSeek / OpenAI 官方端点不得改 UA（默认 SDK UA 没有被 WAF 拦截）：
    deepseek_kwargs = build_openai_compatible_model_kwargs(
        model_name="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
        run_context=context,
    )
    assert "default_headers" not in deepseek_kwargs
    openai_kwargs = build_openai_compatible_model_kwargs(
        model_name="o3",
        api_key="key",
        base_url=None,
        provider="openai",
        run_context=context,
    )
    assert "default_headers" not in openai_kwargs


def test_resolve_model_provider_prefers_carried_provenance() -> None:
    """携带的来源 provider 覆盖 id 前缀推断；缺失时才回退到推断。"""

    from app.chat.runtime.models import infer_model_provider, resolve_model_provider

    carried = _bundle(
        request=ChatStreamRequest(
            message="x", model_name="claude-3-5-sonnet", provider="custom"
        )
    ).context
    inferred = _bundle(
        request=ChatStreamRequest(message="x", model_name="claude-3-5-sonnet")
    ).context

    assert resolve_model_provider("claude-3-5-sonnet", carried) == "custom"
    assert resolve_model_provider("claude-3-5-sonnet", inferred) == "anthropic"
    assert infer_model_provider("claude-3-5-sonnet") == "anthropic"


def test_create_chat_model_routes_custom_relay_over_openai_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中转站用 OpenAI 协议提供 claude-*：必须走兼容 client 指向 CUSTOM_BASE_URL，
    而不是原生 Anthropic SDK。这正是按 id 前缀路由会出错的场景。"""

    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")

    context = _bundle(
        request=ChatStreamRequest(
            message="用中转站的 claude",
            model_name="claude-3-5-sonnet",
            provider="custom",
        )
    ).context
    model = create_chat_model("claude-3-5-sonnet", run_context=context)

    assert model.__class__.__name__ != "ChatAnthropic"
    assert model.openai_api_base == "http://relay.local/v1"


def test_create_chat_model_custom_requires_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """custom 没有官方回落地址：缺 CUSTOM_BASE_URL 时显式报错而不是悄悄走错端点。"""

    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="CUSTOM_BASE_URL"):
        create_chat_model("qwen-plus", provider="custom")


def test_anthropic_extended_thinking_enabled_in_thinking_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic 思考档应开启 extended thinking，并把 max_tokens 抬到预算之上。"""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    from app.chat.runtime.models import create_anthropic_chat_model

    thinking_context = _bundle(
        request=ChatStreamRequest(message="想一想", model_name="claude-sonnet-4-5", mode="pro")
    ).context
    model = create_anthropic_chat_model(
        model_name="claude-sonnet-4-5",
        run_context=thinking_context,
    )

    assert model.thinking == {"type": "enabled", "budget_tokens": 4096}
    assert model.max_tokens == 8192


def test_deepseek_chat_model_preserves_reasoning_stream_delta() -> None:
    model = DeepSeekChatModel(
        model="deepseek-v4-pro",
        api_key="key",
        base_url="https://api.deepseek.com",
        streaming=True,
    )

    chunk = model._convert_chunk_to_generation_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": "",
                        "reasoning_content": "先理解问题",
                    },
                    "finish_reason": None,
                }
            ]
        },
        AIMessageChunk,
        None,
    )

    assert chunk is not None
    assert chunk.message.content == [{"type": "reasoning", "reasoning": "先理解问题"}]
    assert chunk.message.content_blocks == [{"type": "reasoning", "reasoning": "先理解问题"}]
    assert chunk.message.additional_kwargs["reasoning_content"] == "先理解问题"


def test_load_runtime_config_from_env_reads_real_mcp_json_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Full MCP JSON config enables the real MultiServerMCPClient provider."""

    isolate_user_config_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("SLOTFLOW_MCP_ENABLED", raising=False)
    monkeypatch.setenv(
        "SLOTFLOW_MCP_CONFIG_JSON",
        """
        {
            "filesystem": {
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "fake_server"]
            },
            "search": {
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "search_server"]
            },
            "disabled": {
                "enabled": false,
                "transport": "streamable_http",
                "url": "http://localhost:8000/mcp"
            }
        }
        """,
    )

    config = load_runtime_config_from_env()

    assert config.mcp_config == SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="search",
                config={
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-m", "search_server"],
                },
            ),
            SlotFlowMcpServerConfig(
                name="disabled",
                enabled=False,
                config={
                    "transport": "streamable_http",
                    "url": "http://localhost:8000/mcp",
                },
            ),
        ),
    )
    assert isinstance(config.mcp_tool_provider, MultiServerMcpToolProvider)


def test_load_runtime_config_from_env_reads_middleware_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime reads middleware switches but does not instantiate middleware."""

    monkeypatch.setenv("SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_SUMMARIZATION_MIDDLEWARE", "false")

    config = load_runtime_config_from_env()

    assert config.middleware_config == SlotFlowMiddlewareConfig(
        runtime_summary_enabled=False,
        summarization_enabled=False,
    )


def test_create_checkpointer_supports_none_and_memory() -> None:
    """同步 checkpointer 工厂只负责 none / memory。"""

    assert create_checkpointer(
        SlotFlowRuntimeConfig(checkpointer_backend="none")
    ) is None
    assert isinstance(
        create_checkpointer(
            SlotFlowRuntimeConfig(checkpointer_backend="memory")
        ),
        InMemorySaver,
    )


@pytest.mark.asyncio
async def test_create_async_checkpointer_supports_sqlite(tmp_path: Path) -> None:
    """SQLite checkpointer 使用官方 AsyncSqliteSaver。"""

    sqlite_path = tmp_path / "checkpoints.sqlite3"
    checkpointer = await create_async_checkpointer(
        SlotFlowRuntimeConfig(
            checkpointer_backend="sqlite",
            checkpointer_sqlite_path=sqlite_path,
        )
    )
    try:
        assert isinstance(checkpointer, AsyncSqliteSaver)
        assert _sqlite_table_names(sqlite_path) >= {"checkpoints", "writes"}
    finally:
        await aclose_checkpointer(checkpointer)


@pytest.mark.asyncio
async def test_create_async_checkpointer_requires_postgres_uri() -> None:
    """Postgres 后端必须显式给连接串，不能悄悄落到本地默认值。"""

    with pytest.raises(ValueError, match="SLOTFLOW_CHECKPOINTER_POSTGRES_URI"):
        await create_async_checkpointer(
            SlotFlowRuntimeConfig(
                checkpointer_backend="postgres",
            )
        )


@pytest.mark.asyncio
async def test_create_async_checkpointer_delegates_to_postgres_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres 创建逻辑有单独边界，测试不需要真的启动 PostgreSQL。"""

    calls: list[tuple[str, bool]] = []
    sentinel = object()

    async def fake_create_postgres_checkpointer(conn_string: str, *, setup: bool):
        calls.append((conn_string, setup))
        return sentinel

    monkeypatch.setattr(
        runtime_module.checkpointer,
        "create_postgres_checkpointer",
        fake_create_postgres_checkpointer,
    )

    checkpointer = await create_async_checkpointer(
        SlotFlowRuntimeConfig(
            checkpointer_backend="postgres",
            checkpointer_postgres_uri="postgresql://slotflow@localhost/slotflow",
            checkpointer_setup=False,
        )
    )

    assert checkpointer is sentinel
    assert calls == [("postgresql://slotflow@localhost/slotflow", False)]


@pytest.mark.asyncio
async def test_runtime_backed_adapter_uses_request_model_and_keeps_thread_state() -> None:
    """每次 run 可动态选模型，并通过共享 checkpointer 保留多轮状态。"""

    calls: list[str] = []
    responses = iter(["first answer", "second answer"])

    def model_factory(model_name: str) -> FakeListChatModel:
        calls.append(model_name)
        return FakeListChatModel(responses=[next(responses)])

    adapter = RuntimeBackedAgentAdapter(
        SlotFlowRuntimeConfig(checkpointer_backend="memory"),
        model_factory=model_factory,
    )

    first_request = ChatStreamRequest(message="first question", model_name="fake-one")
    first_events = await collect_agent_events(
        adapter.stream_events(
            request=first_request,
            bundle=_bundle(
                thread_id="thread_same",
                run_id="run_one",
                request=first_request,
            ),
        )
    )
    first_snapshot = next(event.data for event in first_events if event.event == "state.snapshot")

    second_request = ChatStreamRequest(message="second question", model_name="fake-two")
    second_events = await collect_agent_events(
        adapter.stream_events(
            request=second_request,
            bundle=_bundle(
                thread_id="thread_same",
                run_id="run_two",
                request=second_request,
            ),
        )
    )
    second_snapshot = next(event.data for event in second_events if event.event == "state.snapshot")

    assert calls == ["fake-one", "fake-two"]
    assert first_snapshot["messages"][-1]["content"] == "first answer"
    assert [message["content"] for message in second_snapshot["messages"]] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ]


@pytest.mark.asyncio
async def test_runtime_backed_adapter_preloads_async_mcp_tools_before_building_graph() -> None:
    """Runtime uses async MCP provider before the synchronous harness tool registry runs."""

    provider = AsyncCapturingMcpToolProvider()
    mcp_config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="filesystem",
                config={"transport": "stdio", "command": "python", "args": []},
            ),
        ),
    )
    adapter = RuntimeBackedAgentAdapter(
        SlotFlowRuntimeConfig(
            checkpointer_backend="memory",
            mcp_config=mcp_config,
            mcp_tool_provider=provider,
        ),
        model_factory=lambda model_name: FakeListChatModel(responses=["mcp-ready"]),
    )
    request = ChatStreamRequest(message="use mcp", model_name="fake-mcp")

    events = await collect_agent_events(
        adapter.stream_events(
            request=request,
            bundle=_bundle(
                thread_id="thread_mcp",
                run_id="run_mcp",
                request=request,
            ),
        )
    )

    assert provider.aload_calls == [mcp_config]
    assert provider.load_calls == [mcp_config]
    assert events[-1].event == "run.finished"


@pytest.mark.asyncio
async def test_runtime_backed_adapter_sqlite_checkpointer_survives_adapter_restart(
    tmp_path: Path,
) -> None:
    """SQLite checkpointer 会把同一个 thread_id 的 graph 状态落盘。"""

    calls: list[str] = []
    responses = iter(["first durable answer", "second durable answer"])
    sqlite_path = tmp_path / "checkpoints.sqlite3"

    def model_factory(model_name: str) -> FakeListChatModel:
        calls.append(model_name)
        return FakeListChatModel(responses=[next(responses)])

    first_config = SlotFlowRuntimeConfig(
        checkpointer_backend="sqlite",
        checkpointer_sqlite_path=sqlite_path,
    )
    first_adapter = RuntimeBackedAgentAdapter(
        first_config,
        model_factory=model_factory,
    )
    first_request = ChatStreamRequest(message="first durable question", model_name="fake-one")
    try:
        first_events = await collect_agent_events(
            first_adapter.stream_events(
                request=first_request,
                bundle=_bundle(
                    thread_id="thread_durable",
                    run_id="run_durable_one",
                    request=first_request,
                ),
            )
        )
        first_snapshot = next(event.data for event in first_events if event.event == "state.snapshot")
        assert first_snapshot["messages"][-1]["content"] == "first durable answer"
    finally:
        await first_adapter.aclose()

    second_config = SlotFlowRuntimeConfig(
        checkpointer_backend="sqlite",
        checkpointer_sqlite_path=sqlite_path,
    )
    second_adapter = RuntimeBackedAgentAdapter(
        second_config,
        model_factory=model_factory,
    )
    second_request = ChatStreamRequest(message="second durable question", model_name="fake-two")
    try:
        second_events = await collect_agent_events(
            second_adapter.stream_events(
                request=second_request,
                bundle=_bundle(
                    thread_id="thread_durable",
                    run_id="run_durable_two",
                    request=second_request,
                ),
            )
        )
        second_snapshot = next(event.data for event in second_events if event.event == "state.snapshot")
    finally:
        await second_adapter.aclose()

    assert calls == ["fake-one", "fake-two"]
    assert [message["content"] for message in second_snapshot["messages"]] == [
        "first durable question",
        "first durable answer",
        "second durable question",
        "second durable answer",
    ]


def _sqlite_table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        connection.close()
