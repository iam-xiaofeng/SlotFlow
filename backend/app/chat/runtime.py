"""SlotFlow 本地 agent runtime 装配层。

这里不直接依赖 DeerFlow 包，而是把“真实 agent 怎么创建、checkpointer 怎么挂进去”
这类运行时装配问题单独收拢成一个更小的本地边界。

当前运行链路只保留真实 LangChain/LangGraph graph。测试如果需要稳定输出，通过
`model_factory` 注入 fake chat model，而不是在生产代码里保留一套静态 adapter。

后面如果要继续吸收 DeerFlow 的有价值能力，也应该优先在这里本地重写，而不是把旧项目
整包 import 进来。
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from langgraph.checkpoint.memory import InMemorySaver

from app.chat.agent_adapter import (
    AgentEvent,
    AgentAdapter,
    LangGraphEventAgentAdapter,
)
from app.chat.models import ChatStreamRequest, RunConfigBundle, RunContext
from app.harness import SlotFlowHarnessConfig, build_slotflow_harness_graph
from app.harness.mcp import (
    McpToolProvider,
    MultiServerMcpToolProvider,
    SlotFlowMcpConfigStore,
    SlotFlowMcpConfig,
    SlotFlowMcpServerConfig,
    ensure_mcp_tools_loaded,
    is_removed_default_mcp_server,
)
from app.harness.memory import SlotFlowMemoryStore
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.skills import SlotFlowSkillsConfigStore, load_enabled_skills

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.types import Checkpointer


CheckpointerBackend = Literal["none", "memory", "sqlite", "postgres"]

DEFAULT_DEEPSEEK_SYSTEM_PROMPT = (
    "你是 SlotFlow 的学习版助手，回答要简洁、具体。"
    "专业领域问题会由后端 skills preflight 先查找可用 Skill；"
    "你需要优先阅读该结果，必要时再用 find-skills/skill_install 补充。"
    "用户可见的报告、图表、可视化、流程图、对比表、交互演示和代码预览"
    "默认写入 artifact。"
)
DEFAULT_CHECKPOINTER_SQLITE_PATH = Path(".slotflow/checkpoints.sqlite3")
DEFAULT_SKILLS_ROOT = Path(".slotflow/skills")
DEFAULT_SKILLS_CONFIG_PATH = Path(".slotflow/skills.json")
DEFAULT_MEMORY_SQLITE_PATH = Path(".slotflow/memory.sqlite3")
DEFAULT_MCP_CONFIG_PATH = Path(".slotflow/mcp.json")


@dataclass(slots=True)
class SlotFlowRuntimeConfig:
    """SlotFlow 本地 runtime 的最小配置。

    先只保留和当前学习链路直接相关的字段，不提前引入 DeerFlow 旧网关里的大而全配置树。
    """

    model_name: str = "deepseek-v4-flash"
    checkpointer_backend: CheckpointerBackend = "memory"
    checkpointer_sqlite_path: Path = DEFAULT_CHECKPOINTER_SQLITE_PATH
    checkpointer_postgres_uri: str | None = None
    checkpointer_setup: bool = True
    system_prompt: str = DEFAULT_DEEPSEEK_SYSTEM_PROMPT
    skills_root: Path | None = None
    enabled_skills: set[str] | None = None
    skills_config_store: SlotFlowSkillsConfigStore | None = field(default=None, compare=False)
    memory_store: SlotFlowMemoryStore | None = field(default=None, compare=False)
    mcp_config: SlotFlowMcpConfig = field(default_factory=SlotFlowMcpConfig)
    mcp_tool_provider: McpToolProvider | None = None
    mcp_config_store: SlotFlowMcpConfigStore | None = field(default=None, compare=False)
    middleware_config: SlotFlowMiddlewareConfig = field(default_factory=SlotFlowMiddlewareConfig)
    sandbox_config: SlotFlowSandboxConfig = field(default_factory=SlotFlowSandboxConfig)


class RuntimeBackedAgentAdapter:
    """按 SlotFlow 本地 runtime 配置在每次 run 时创建真实 adapter。

    这样可以保留路由层当前依赖的 `AgentAdapter` 边界，同时避免把真实模型、graph、
    checkpointer 选择写死在 `create_app()` 启动阶段。
    """

    def __init__(
        self,
        runtime_config: SlotFlowRuntimeConfig,
        *,
        model_factory: Callable[[str], BaseChatModel] | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._model_factory = model_factory or (lambda model_name: create_deepseek_chat_model(model_name=model_name))
        self._checkpointer = (
            create_checkpointer(runtime_config)
            if runtime_config.checkpointer_backend in ("none", "memory")
            else None
        )

    def close(self) -> None:
        """关闭 runtime 持有的持久化 checkpointer 连接。"""

        close_checkpointer(self._checkpointer)

    async def aclose(self) -> None:
        """异步关闭 runtime 持有的持久化 checkpointer 连接。"""

        await aclose_checkpointer(self._checkpointer)

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        model_name = bundle.context.model_name or self._runtime_config.model_name
        checkpointer = await self._ensure_checkpointer()
        refresh_runtime_skills_config(self._runtime_config)
        await self._ensure_mcp_tools_loaded()
        graph = create_langgraph_agent_graph(
            model=self._model_factory(model_name),
            runtime_config=self._runtime_config,
            run_context=bundle.context,
            checkpointer=checkpointer,
        )
        adapter = LangGraphEventAgentAdapter(graph)

        async for event in adapter.stream_events(request=request, bundle=bundle):
            yield event

    async def _ensure_checkpointer(self) -> Checkpointer | None:
        if self._runtime_config.checkpointer_backend in ("none", "memory"):
            return self._checkpointer
        if self._checkpointer is None:
            self._checkpointer = await create_async_checkpointer(self._runtime_config)
        return self._checkpointer

    async def _ensure_mcp_tools_loaded(self) -> None:
        refresh_runtime_mcp_config(self._runtime_config)
        await ensure_mcp_tools_loaded(
            config=self._runtime_config.mcp_config,
            provider=self._runtime_config.mcp_tool_provider,
        )


def load_runtime_config_from_env() -> SlotFlowRuntimeConfig:
    """从环境变量读取一个很小的 runtime 配置。

    默认使用 DeepSeek-compatible 真实 graph。日常测试通过 `model_factory` 注入 fake
    model，不再让生产配置携带测试模式。
    """

    checkpointer_backend = os.environ.get("SLOTFLOW_CHECKPOINTER_BACKEND", "memory").strip().lower()
    if checkpointer_backend not in ("none", "memory", "sqlite", "postgres"):
        raise ValueError(
            "SLOTFLOW_CHECKPOINTER_BACKEND must be 'none', 'memory', 'sqlite', "
            "or 'postgres', "
            f"got {checkpointer_backend!r}",
        )

    middleware_config = load_middleware_config_from_env()
    env_mcp_config = load_mcp_config_from_env()
    mcp_config_store = build_mcp_config_store_from_env(env_mcp_config)
    mcp_config = mcp_config_store.load_config()
    skills_root = load_path_from_env("SLOTFLOW_SKILLS_ROOT", default=DEFAULT_SKILLS_ROOT)
    skills_config_store = build_skills_config_store_from_env(skills_root)
    skills_config_store.ensure_default_find_skills()

    return SlotFlowRuntimeConfig(
        model_name=os.environ.get("SLOTFLOW_DEEPSEEK_MODEL", "deepseek-v4-flash"),
        checkpointer_backend=checkpointer_backend,
        checkpointer_sqlite_path=load_path_from_env(
            "SLOTFLOW_CHECKPOINTER_SQLITE_PATH",
            default=DEFAULT_CHECKPOINTER_SQLITE_PATH,
        ),
        checkpointer_postgres_uri=load_optional_text_from_env(
            "SLOTFLOW_CHECKPOINTER_POSTGRES_URI",
        ),
        checkpointer_setup=load_bool_from_env(
            "SLOTFLOW_CHECKPOINTER_SETUP",
            default=True,
        ),
        system_prompt=os.environ.get("SLOTFLOW_SYSTEM_PROMPT", DEFAULT_DEEPSEEK_SYSTEM_PROMPT),
        skills_root=skills_root,
        enabled_skills=load_optional_csv_set_from_env("SLOTFLOW_ENABLED_SKILLS"),
        skills_config_store=skills_config_store,
        memory_store=build_memory_store_from_env(middleware_config),
        mcp_config=mcp_config,
        mcp_tool_provider=build_mcp_tool_provider(mcp_config),
        mcp_config_store=mcp_config_store,
        middleware_config=middleware_config,
        sandbox_config=load_sandbox_config_from_env(),
    )


def load_middleware_config_from_env() -> SlotFlowMiddlewareConfig:
    """Read SlotFlow-owned middleware switches from environment variables."""

    defaults = SlotFlowMiddlewareConfig()
    return SlotFlowMiddlewareConfig(
        runtime_summary_enabled=load_bool_from_env(
            "SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE",
            default=True,
        ),
        dangling_tool_call_enabled=load_bool_from_env(
            "SLOTFLOW_DANGLING_TOOL_CALL_MIDDLEWARE",
            default=True,
        ),
        tool_safety_enabled=load_bool_from_env(
            "SLOTFLOW_TOOL_SAFETY_MIDDLEWARE",
            default=True,
        ),
        artifact_discovery_enabled=load_bool_from_env(
            "SLOTFLOW_ARTIFACT_DISCOVERY_MIDDLEWARE",
            default=True,
        ),
        summarization_enabled=load_bool_from_env(
            "SLOTFLOW_SUMMARIZATION_MIDDLEWARE",
            default=True,
        ),
        summarization_trigger_tokens=load_positive_int_from_env(
            "SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS",
            default=defaults.summarization_trigger_tokens,
        ),
        summarization_keep_messages=load_positive_int_from_env(
            "SLOTFLOW_SUMMARIZATION_KEEP_MESSAGES",
            default=defaults.summarization_keep_messages,
        ),
        summarization_trim_tokens=load_positive_int_from_env(
            "SLOTFLOW_SUMMARIZATION_TRIM_TOKENS",
            default=defaults.summarization_trim_tokens,
        ),
        long_term_memory_enabled=load_bool_from_env(
            "SLOTFLOW_LONG_TERM_MEMORY_ENABLED",
            default=True,
        ),
        skills_preflight_enabled=load_bool_from_env(
            "SLOTFLOW_SKILLS_PREFLIGHT_MIDDLEWARE",
            default=True,
        ),
        clarification_enabled=load_bool_from_env(
            "SLOTFLOW_CLARIFICATION_MIDDLEWARE",
            default=True,
        ),
        uploads_enabled=load_bool_from_env(
            "SLOTFLOW_UPLOADS_MIDDLEWARE",
            default=True,
        ),
        todo_enabled=load_bool_from_env(
            "SLOTFLOW_TODO_MIDDLEWARE",
            default=True,
        ),
    )


def build_memory_store_from_env(
    middleware_config: SlotFlowMiddlewareConfig,
) -> SlotFlowMemoryStore | None:
    """Create the local long-term memory store when the middleware is enabled."""

    if not middleware_config.long_term_memory_enabled:
        return None
    return SlotFlowMemoryStore(
        load_path_from_env(
            "SLOTFLOW_MEMORY_SQLITE_PATH",
            default=DEFAULT_MEMORY_SQLITE_PATH,
        )
    )


def build_mcp_config_store_from_env(
    base_config: SlotFlowMcpConfig,
) -> SlotFlowMcpConfigStore:
    """Create the persistent user MCP config store."""

    return SlotFlowMcpConfigStore(
        load_path_from_env("SLOTFLOW_MCP_CONFIG_PATH", default=DEFAULT_MCP_CONFIG_PATH),
        base_config=base_config,
    )


def build_skills_config_store_from_env(
    skills_root: Path,
) -> SlotFlowSkillsConfigStore:
    """Create the persistent user skill config store."""

    return SlotFlowSkillsConfigStore(
        load_path_from_env("SLOTFLOW_SKILLS_CONFIG_PATH", default=DEFAULT_SKILLS_CONFIG_PATH),
        skills_root=skills_root,
    )


def refresh_runtime_skills_config(runtime_config: SlotFlowRuntimeConfig) -> set[str] | None:
    """Refresh enabled skills from the persistent config store."""

    if runtime_config.skills_root is None or runtime_config.skills_config_store is None:
        return runtime_config.enabled_skills

    runtime_config.skills_config_store.ensure_default_find_skills()
    all_skills = load_enabled_skills(
        skills_root=runtime_config.skills_root,
        enabled_names=None,
    )
    discovered_names = {skill.name for skill in all_skills}
    runtime_config.enabled_skills = runtime_config.skills_config_store.enabled_skill_names(discovered_names)
    return runtime_config.enabled_skills


def refresh_runtime_mcp_config(runtime_config: SlotFlowRuntimeConfig) -> SlotFlowMcpConfig:
    """Refresh user-managed MCP config without restarting the backend."""

    if runtime_config.mcp_config_store is None:
        return runtime_config.mcp_config

    next_config = runtime_config.mcp_config_store.load_config()
    if next_config != runtime_config.mcp_config:
        runtime_config.mcp_config = next_config
        runtime_config.mcp_tool_provider = build_mcp_tool_provider(next_config)
    return runtime_config.mcp_config


def load_sandbox_config_from_env() -> SlotFlowSandboxConfig:
    """Read SlotFlow workspace/sandbox limits from environment variables."""

    return SlotFlowSandboxConfig(
        workspace_root=load_optional_path_from_env("SLOTFLOW_WORKSPACE_ROOT"),
        writes_enabled=load_bool_from_env(
            "SLOTFLOW_WORKSPACE_WRITES_ENABLED",
            default=False,
        ),
        max_read_bytes=load_positive_int_from_env(
            "SLOTFLOW_WORKSPACE_MAX_READ_BYTES",
            default=SlotFlowSandboxConfig().max_read_bytes,
        ),
        max_write_bytes=load_positive_int_from_env(
            "SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES",
            default=SlotFlowSandboxConfig().max_write_bytes,
        ),
        network_enabled=load_bool_from_env(
            "SLOTFLOW_NETWORK_ENABLED",
            default=True,
        ),
        allow_private_network=load_bool_from_env(
            "SLOTFLOW_NETWORK_ALLOW_PRIVATE",
            default=False,
        ),
        max_fetch_bytes=load_positive_int_from_env(
            "SLOTFLOW_NETWORK_MAX_FETCH_BYTES",
            default=SlotFlowSandboxConfig().max_fetch_bytes,
        ),
        network_timeout_seconds=load_positive_int_from_env(
            "SLOTFLOW_NETWORK_TIMEOUT_SECONDS",
            default=SlotFlowSandboxConfig().network_timeout_seconds,
        ),
    )


def load_mcp_config_from_env() -> SlotFlowMcpConfig:
    """Read the first SlotFlow MCP config shape from environment variables."""

    raw_config = load_optional_text_from_env("SLOTFLOW_MCP_CONFIG_JSON")
    enabled = load_bool_from_env("SLOTFLOW_MCP_ENABLED", default=raw_config is not None)
    if raw_config is not None:
        return SlotFlowMcpConfig(
            enabled=enabled,
            servers=tuple(load_mcp_servers_from_json(raw_config)),
        )

    server_names = [
        name
        for name in load_optional_csv_list_from_env("SLOTFLOW_MCP_SERVERS") or []
        if not is_removed_default_mcp_server(name)
    ]
    return SlotFlowMcpConfig(
        enabled=enabled,
        servers=tuple(SlotFlowMcpServerConfig(name=name) for name in server_names),
    )


def load_mcp_servers_from_json(raw_config: str) -> list[SlotFlowMcpServerConfig]:
    """Parse real MCP server connection config from JSON."""

    try:
        data = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise ValueError("SLOTFLOW_MCP_CONFIG_JSON must be valid JSON") from exc

    if not isinstance(data, dict):
        raise ValueError("SLOTFLOW_MCP_CONFIG_JSON must be an object keyed by server name")

    servers: list[SlotFlowMcpServerConfig] = []
    for name, raw_server_config in data.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("SLOTFLOW_MCP_CONFIG_JSON server names must be non-empty strings")
        if is_removed_default_mcp_server(name):
            continue
        if not isinstance(raw_server_config, dict):
            raise ValueError(f"MCP server {name!r} config must be an object")

        server_config = dict(raw_server_config)
        enabled = server_config.pop("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"MCP server {name!r} enabled must be a boolean")

        servers.append(
            SlotFlowMcpServerConfig(
                name=name.strip(),
                enabled=enabled,
                config=server_config,
            )
        )
    return servers


def build_mcp_tool_provider(config: SlotFlowMcpConfig) -> McpToolProvider | None:
    """Create the real MCP provider only when full connection config exists."""

    if not config.enabled:
        return None
    if any(server.config is not None for server in config.active_servers()):
        return MultiServerMcpToolProvider()
    return None


def load_bool_from_env(name: str, *, default: bool) -> bool:
    """Read a small boolean environment flag."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag, got {value!r}")


def load_optional_path_from_env(name: str) -> Path | None:
    """从环境变量读取可选路径。"""

    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def load_path_from_env(name: str, *, default: Path) -> Path:
    """从环境变量读取路径，不存在时返回默认值。"""

    value = os.environ.get(name)
    if not value or not value.strip():
        return default
    return Path(value.strip())


def load_optional_text_from_env(name: str) -> str | None:
    """从环境变量读取非空字符串。"""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def load_optional_csv_set_from_env(name: str) -> set[str] | None:
    """从环境变量读取逗号分隔名单。"""

    value = os.environ.get(name)
    if not value:
        return None
    names = {item.strip() for item in value.split(",") if item.strip()}
    return names or None


def load_optional_csv_list_from_env(name: str) -> list[str] | None:
    """Read a comma-separated list while preserving item order."""

    value = os.environ.get(name)
    if not value:
        return None
    names = [item.strip() for item in value.split(",") if item.strip()]
    return names or None


def load_positive_int_from_env(name: str, *, default: int) -> int:
    """Read a positive integer environment value."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return parsed


def create_checkpointer(
    runtime_config: SlotFlowRuntimeConfig,
) -> Checkpointer | None:
    """创建同步可用的 LangGraph checkpointer。"""

    if runtime_config.checkpointer_backend == "none":
        return None
    if runtime_config.checkpointer_backend == "memory":
        return InMemorySaver()
    if runtime_config.checkpointer_backend in ("sqlite", "postgres"):
        raise ValueError(
            "persistent checkpointer backends require create_async_checkpointer() "
            "because SlotFlow streams LangGraph with async APIs",
        )
    raise ValueError(f"unknown checkpointer backend: {runtime_config.checkpointer_backend!r}")


async def create_async_checkpointer(
    runtime_config: SlotFlowRuntimeConfig,
) -> Checkpointer | None:
    """创建 async graph stream 可用的 LangGraph checkpointer。"""

    if runtime_config.checkpointer_backend in ("none", "memory"):
        return create_checkpointer(runtime_config)
    if runtime_config.checkpointer_backend == "sqlite":
        return await create_sqlite_checkpointer(
            runtime_config.checkpointer_sqlite_path,
            setup=runtime_config.checkpointer_setup,
        )
    if runtime_config.checkpointer_backend == "postgres":
        if runtime_config.checkpointer_postgres_uri is None:
            raise ValueError(
                "SLOTFLOW_CHECKPOINTER_POSTGRES_URI is required when "
                "SLOTFLOW_CHECKPOINTER_BACKEND=postgres",
            )
        return await create_postgres_checkpointer(
            runtime_config.checkpointer_postgres_uri,
            setup=runtime_config.checkpointer_setup,
        )
    raise ValueError(f"unknown checkpointer backend: {runtime_config.checkpointer_backend!r}")


async def create_sqlite_checkpointer(
    database_path: str | Path,
    *,
    setup: bool = True,
) -> Checkpointer:
    """创建 LangGraph Async SQLite checkpointer。"""

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    database_path_text = str(database_path)
    if database_path_text != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    connection = await aiosqlite.connect(database_path_text)
    checkpointer = AsyncSqliteSaver(connection)
    if setup:
        await checkpointer.setup()
    return checkpointer


async def create_postgres_checkpointer(
    conn_string: str,
    *,
    setup: bool = True,
) -> Checkpointer:
    """创建 LangGraph Async Postgres checkpointer。"""

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    connection = await AsyncConnection.connect(
        conn_string,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    checkpointer = AsyncPostgresSaver(connection)
    if setup:
        await checkpointer.setup()
    return checkpointer


def close_checkpointer(checkpointer: Checkpointer | None) -> None:
    """关闭官方 saver 持有的底层数据库连接。"""

    if checkpointer is None:
        return

    connection = getattr(checkpointer, "conn", None)
    close = getattr(connection, "close", None)
    if callable(close) and not inspect.iscoroutinefunction(close):
        with suppress(Exception):
            close()


async def aclose_checkpointer(checkpointer: Checkpointer | None) -> None:
    """异步关闭官方 saver 持有的底层数据库连接。"""

    if checkpointer is None:
        return

    connection = getattr(checkpointer, "conn", None)
    close = getattr(connection, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result


def create_deepseek_chat_model(*, model_name: str) -> BaseChatModel:
    """创建 DeepSeek OpenAI-compatible chat model。"""

    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek runtime")

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        streaming=True,
        timeout=30,
        max_retries=0,
    )


def create_langgraph_agent_graph(
    *,
    model: str | BaseChatModel,
    runtime_config: SlotFlowRuntimeConfig,
    run_context: RunContext,
    checkpointer: Checkpointer | None = None,
):
    """创建 SlotFlow 本地的真实 LangGraph agent graph。

    `chat.runtime` 只负责选择运行模式和模型。真实 graph 的组装委托给 `app.harness`，
    这样 tools / skills / MCP / middleware 后续都能收敛到 harness 边界里。
    """

    return build_slotflow_harness_graph(
        model=model,
        run_context=run_context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt=runtime_config.system_prompt,
            skills_root=runtime_config.skills_root,
            enabled_skills=runtime_config.enabled_skills,
            skills_config_store=runtime_config.skills_config_store,
            memory_store=runtime_config.memory_store,
            mcp_config=runtime_config.mcp_config,
            mcp_tool_provider=runtime_config.mcp_tool_provider,
            mcp_config_store=runtime_config.mcp_config_store,
            middleware_config=runtime_config.middleware_config,
            sandbox_config=runtime_config.sandbox_config,
        ),
        checkpointer=checkpointer,
    )


def build_agent_adapter(
    runtime_config: SlotFlowRuntimeConfig | None = None,
    *,
    model_factory: Callable[[str], BaseChatModel] | None = None,
) -> AgentAdapter:
    """按 SlotFlow 本地 runtime 配置创建默认 agent adapter。

    返回值仍然满足 `AgentAdapter` 边界，但真实 graph 的创建推迟到每次 run 调用时。
    """

    resolved = runtime_config or load_runtime_config_from_env()
    return RuntimeBackedAgentAdapter(
        resolved,
        model_factory=model_factory,
    )
