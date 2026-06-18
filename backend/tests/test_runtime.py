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
    DeepSeekChatOpenAI,
    RuntimeBackedAgentAdapter,
    SlotFlowRuntimeConfig,
    aclose_checkpointer,
    create_async_checkpointer,
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
    monkeypatch.delenv("SLOTFLOW_TOOL_SAFETY_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_LONG_TERM_MEMORY_ENABLED", raising=False)
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
    assert "extra_body" not in no_thinking_kwargs
    assert "reasoning_effort" not in flash_kwargs
    assert "extra_body" not in flash_kwargs


def test_deepseek_chat_openai_preserves_reasoning_stream_delta() -> None:
    model = DeepSeekChatOpenAI(
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
    monkeypatch.setenv("SLOTFLOW_TOOL_SAFETY_MIDDLEWARE", "false")

    config = load_runtime_config_from_env()

    assert config.middleware_config == SlotFlowMiddlewareConfig(
        runtime_summary_enabled=False,
        tool_safety_enabled=False,
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
