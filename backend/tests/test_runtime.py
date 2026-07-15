"""SlotFlow 本地 runtime 装配层测试。

这一层不直接依赖外部应用包，而是把 SlotFlow 自己需要的最小运行时装配收拢出来：

- 创建真实 LangGraph/ChatLiteLLM-backed agent graph
- 显式挂接 checkpointer
- 保持 AgentAdapter / AgentEvent 外部契约不变
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.chat.agent_adapter import collect_agent_events
from app.chat.models import ChatStreamRequest
import app.chat.litellm_provider as litellm_provider
import app.chat.runtime as runtime_module
from app.chat.run_config import build_run_config
from app.chat.runtime.models import ChatLiteLLM
from app.chat.runtime import (
    DEFAULT_CHECKPOINTER_SQLITE_PATH,
    RuntimeBackedAgentAdapter,
    SlotFlowRuntimeConfig,
    aclose_checkpointer,
    create_async_checkpointer,
    create_chat_model,
    create_checkpointer,
    load_runtime_config_from_env,
    build_litellm_model_kwargs,
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
    monkeypatch.delenv("SLOTFLOW_SUBAGENT_RECURSION_LIMIT", raising=False)
    monkeypatch.delenv("SLOTFLOW_MEMORY_SQLITE_PATH", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES", raising=False)

    config = load_runtime_config_from_env()

    assert config == SlotFlowRuntimeConfig(
        model_name="deepseek/deepseek-v4-pro",
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
    monkeypatch.setenv("SLOTFLOW_SUBAGENT_RECURSION_LIMIT", "73")

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
    assert config.subagent_config.recursion_limit == 73
    assert config.memory_store is None


def test_litellm_dotenv_loading_is_disabled() -> None:
    """Importing LiteLLM must not hydrate credentials from backend/.env."""

    assert litellm_provider.os.environ["LITELLM_MODE"] == "PRODUCTION"
    assert litellm_provider.os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


@pytest.mark.parametrize(
    ("thinking_enabled", "expected_effort"),
    [(True, "high"), (False, "none")],
)
def test_reasoning_effort_uses_litellm_capability_metadata(
    monkeypatch: pytest.MonkeyPatch,
    thinking_enabled: bool,
    expected_effort: str,
) -> None:
    context = _bundle(
        request=ChatStreamRequest(
            message="analyze",
            model_name="gemini/gemini-2.5-pro",
            mode="pro",
            thinking_enabled=thinking_enabled,
        )
    ).context
    monkeypatch.setattr(
        litellm_provider,
        "supports_reasoning_effort",
        lambda model_id: model_id == "gemini/gemini-2.5-pro",
    )

    kwargs = build_litellm_model_kwargs(
        model_name="gemini/gemini-2.5-pro",
        provider="gemini",
        run_context=context,
    )

    assert kwargs["model"] == "gemini/gemini-2.5-pro"
    assert kwargs["model_kwargs"] == {
        "_skip_responses_api_bridge": True,
        "reasoning_effort": expected_effort,
    }


def test_model_without_reasoning_effort_uses_only_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "supports_reasoning_effort",
        lambda model_id: False,
    )

    kwargs = build_litellm_model_kwargs(
        model_name="mistral/mistral-large-latest",
        provider="mistral",
    )

    assert kwargs["model"] == "mistral/mistral-large-latest"
    assert kwargs["model_kwargs"] == {"_skip_responses_api_bridge": True}


def test_official_openai_models_use_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "supports_reasoning_effort",
        lambda model_id: False,
    )

    kwargs = build_litellm_model_kwargs(
        model_name="openai/gpt-4.1",
        provider="openai",
    )

    assert kwargs["model"] == "openai/gpt-4.1"
    assert kwargs["model_kwargs"] == {"_skip_responses_api_bridge": True}


def test_custom_provider_uses_openai_transport_without_native_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "key")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")
    context = _bundle(
        request=ChatStreamRequest(
            message="x", model_name="qwen-plus", mode="pro", provider="custom"
        )
    ).context

    kwargs = build_litellm_model_kwargs(
        model_name="qwen-plus",
        provider="custom",
        run_context=context,
    )

    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["api_base"] == "http://relay.local/v1"
    assert kwargs["extra_headers"] == {
        "User-Agent": litellm_provider.CUSTOM_RELAY_USER_AGENT
    }
    assert kwargs["model_kwargs"] == {"_skip_responses_api_bridge": True}


def test_resolve_model_provider_prefers_catalog_provenance() -> None:
    from app.chat.runtime.models import infer_model_provider, resolve_model_provider

    carried = _bundle(
        request=ChatStreamRequest(
            message="x", model_name="claude-3-5-sonnet", provider="custom"
        )
    ).context

    assert resolve_model_provider("claude-3-5-sonnet", carried) == "custom"
    assert infer_model_provider("gemini/gemini-2.5-pro") == "gemini"
    with pytest.raises(Exception):
        infer_model_provider("claude-3-5-sonnet")


@pytest.mark.parametrize(
    ("provider", "model_name", "runtime_model_name"),
    [
        ("deepseek", "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
        ("openai", "openai/gpt-5", "openai/gpt-5"),
        ("anthropic", "anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
        ("gemini", "gemini/gemini-2.5-pro", "gemini/gemini-2.5-pro"),
        ("mistral", "mistral/mistral-large-latest", "mistral/mistral-large-latest"),
    ],
)
def test_create_chat_model_uses_litellm_for_native_providers(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model_name: str,
    runtime_model_name: str,
) -> None:
    monkeypatch.setattr(
        litellm_provider,
        "supports_reasoning_effort",
        lambda model_id: False,
    )

    model = create_chat_model(model_name, provider=provider)

    assert isinstance(model, ChatLiteLLM)
    assert model.model == runtime_model_name
    assert model.custom_llm_provider is None
    assert model.model_kwargs["_skip_responses_api_bridge"] is True


def test_create_chat_model_routes_custom_relay_through_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://relay.local/v1")
    context = _bundle(
        request=ChatStreamRequest(
            message="use relay Claude",
            model_name="claude-3-5-sonnet",
            provider="custom",
        )
    ).context

    model = create_chat_model("claude-3-5-sonnet", run_context=context)

    assert isinstance(model, ChatLiteLLM)
    assert model.custom_llm_provider == "openai"
    assert model.api_base == "http://relay.local/v1"


def test_create_chat_model_custom_requires_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "ck")
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="CUSTOM_BASE_URL"):
        create_chat_model("qwen-plus", provider="custom")


def test_litellm_passes_reasoning_back_after_tool_call() -> None:
    model = ChatLiteLLM(
        model="deepseek/deepseek-v4-pro",
        api_key="key",
        streaming=True,
    )
    messages, _ = model._create_message_dicts(
        [
            HumanMessage(content="generate report"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sandbox_exec",
                        "args": {"command": "python build.py"},
                        "id": "call_build",
                    }
                ],
                additional_kwargs={"reasoning_content": "I need to build the file first."},
            ),
            ToolMessage(content="ok", tool_call_id="call_build"),
        ],
        None,
    )

    assistant_payload = messages[1]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["tool_calls"][0]["id"] == "call_build"
    assert assistant_payload["reasoning_content"] == "I need to build the file first."

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
