"""SlotFlow 本地 runtime 的配置与环境装配。

把 “这次运行用什么模型、checkpointer、skills、MCP、middleware、sandbox” 收拢成一个
最小配置 `SlotFlowRuntimeConfig`，并提供从环境变量装配它的入口。这里只读配置，不创建
模型，也不组装 graph。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.chat.model_catalog import DEFAULT_CHAT_MODEL
from app.chat.runtime.env import (
    load_bool_from_env,
    load_optional_csv_list_from_env,
    load_optional_csv_set_from_env,
    load_optional_path_from_env,
    load_optional_text_from_env,
    load_path_from_env,
    load_positive_int_from_env,
)
from app.harness.mcp import (
    McpToolProvider,
    MultiServerMcpToolProvider,
    SlotFlowMcpConfig,
    SlotFlowMcpConfigStore,
    SlotFlowMcpServerConfig,
    is_removed_default_mcp_server,
)
from app.harness.memory import SlotFlowMemoryStore
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.skills import SlotFlowSkillsConfigStore, load_enabled_skills
from app.harness.subagents import SlotFlowSubagentConfig
from app.harness.tools.agent_reach import SlotFlowAgentReachConfig


CheckpointerBackend = Literal["none", "memory", "sqlite", "postgres"]

DEFAULT_DEEPSEEK_SYSTEM_PROMPT = (
    "你是 SlotFlow，一个本地优先、可扩展的 AI 助手，运行在带工具、技能(Skill)、MCP、"
    "长期记忆与工作区(workspace)的 agent runtime 中。\n\n"
    "基本原则：\n"
    "- 回答简洁、具体、可执行：先给结论，再按需展开；用与用户相同的语言作答（默认中文）。\n"
    "- 诚实可靠：只在有把握时下断言；不确定就说明，或先用 ask_clarification 澄清，"
    "绝不编造事实、链接或工具结果；工具失败就如实说明并给出当前能做到的最佳回答。\n"
    "- 时效性与事实性问题（行情、新闻、版本、数据等）用 web_search/web_fetch 核实，"
    "并在相应结论旁附上来源链接。\n\n"
    "能力使用（具体调用规则由运行时附加说明给出，这里只是高层约定）：\n"
    "- 专业领域或专业工作流：先用 skill_match 查已安装 Skill；没有再用 find-skills 检索可"
    "安装的 Skill，确认 package_url 与 skill_name 后再 skill_install。\n"
    "- 面向用户的报告、图表、可视化、流程图、对比表、交互式演示或代码预览：默认用 artifact "
    "工具生成 HTML/Markdown 产物展示，而不是把长内容堆进对话。\n"
    "- 需要读写本地文件时使用 workspace 工具；上传文件的路径会注入到当前用户消息，回答其"
    "内容前先 workspace_read。\n"
    "- 值得跨会话记住的用户事实或偏好，主动用长期记忆保存，避免重复询问已知信息。\n\n"
    "思考与输出：思考过程只属于 reasoning 通道；最终答案正文只放面向用户的结果，"
    "不要混入内部推理、待办、工具调用记录或冗长自述。"
)
DEFAULT_CHECKPOINTER_SQLITE_PATH = Path(".slotflow/checkpoints.sqlite3")
DEFAULT_SKILLS_ROOT = Path(".slotflow/skills")
DEFAULT_SKILLS_CONFIG_PATH = Path(".slotflow/skills.json")
DEFAULT_MEMORY_SQLITE_PATH = Path(".slotflow/memory.sqlite3")
DEFAULT_MCP_CONFIG_PATH = Path(".slotflow/mcp.json")
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PLAYWRIGHT_MCP_NAME = "playwright"
PLAYWRIGHT_PRIVATE_ORIGIN_GLOBS = (
    "localhost",
    "127.*",
    "[::1]",
    "[fe80::*]",
    "[fc00::*]",
    "[fd00::*]",
    "0.*",
    "10.*",
    "100.64.*",
    "169.254.*",
    "192.168.*",
    "*.local",
    "*.localhost",
    "*.internal",
    "metadata.google.internal",
    *(f"172.{second}.*" for second in range(16, 32)),
)


@dataclass(slots=True)
class SlotFlowRuntimeConfig:
    """SlotFlow 本地 runtime 的最小配置。

    只保留运行链路直接需要的字段，避免引入不相关的大配置树。
    """

    model_name: str = DEFAULT_CHAT_MODEL
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
    agent_reach_config: SlotFlowAgentReachConfig = field(default_factory=SlotFlowAgentReachConfig)
    subagent_config: SlotFlowSubagentConfig = field(default_factory=SlotFlowSubagentConfig)


def load_runtime_config_from_env() -> SlotFlowRuntimeConfig:
    """从环境变量读取一个很小的 runtime 配置。

    默认使用 ChatLiteLLM-backed 真实 graph。日常测试通过 `model_factory` 注入 fake
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
    sandbox_config = load_sandbox_config_from_env()
    env_mcp_config = load_mcp_config_from_env(sandbox_config=sandbox_config)
    mcp_config_store = build_mcp_config_store_from_env(env_mcp_config)
    mcp_config = mcp_config_store.load_config()
    skills_root = load_path_from_env("SLOTFLOW_SKILLS_ROOT", default=DEFAULT_SKILLS_ROOT)
    skills_config_store = build_skills_config_store_from_env(skills_root)
    skills_config_store.ensure_default_find_skills()

    return SlotFlowRuntimeConfig(
        model_name=DEFAULT_CHAT_MODEL,
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
        sandbox_config=sandbox_config,
        agent_reach_config=load_agent_reach_config_from_env(),
        subagent_config=SlotFlowSubagentConfig(
            recursion_limit=load_positive_int_from_env(
                "SLOTFLOW_SUBAGENT_RECURSION_LIMIT",
                default=SlotFlowSubagentConfig().recursion_limit,
            )
        ),
    )


def load_middleware_config_from_env() -> SlotFlowMiddlewareConfig:
    """Read SlotFlow-owned middleware switches from environment variables."""

    defaults = SlotFlowMiddlewareConfig()
    return SlotFlowMiddlewareConfig(
        runtime_summary_enabled=load_bool_from_env(
            "SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE",
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
        proactive_memory_extraction_enabled=load_bool_from_env(
            "SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION",
            default=True,
        ),
        skills_preflight_enabled=load_bool_from_env(
            "SLOTFLOW_SKILLS_PREFLIGHT_MIDDLEWARE",
            default=True,
        ),
        clarify_gate_enabled=load_bool_from_env(
            "SLOTFLOW_CLARIFY_GATE",
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
        subagent_limit_enabled=load_bool_from_env(
            "SLOTFLOW_SUBAGENT_LIMIT",
            default=True,
        ),
        subagent_max_concurrent=load_positive_int_from_env(
            "SLOTFLOW_SUBAGENT_MAX_CONCURRENT",
            default=defaults.subagent_max_concurrent,
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
            default=True,
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
        code_execution_enabled=load_bool_from_env(
            "SLOTFLOW_CODE_EXECUTION_ENABLED",
            default=True,
        ),
        docker_image=(
            os.environ.get("SLOTFLOW_DOCKER_SANDBOX_IMAGE", "").strip()
            or SlotFlowSandboxConfig().docker_image
        ),
        docker_timeout_seconds=load_positive_int_from_env(
            "SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS",
            default=SlotFlowSandboxConfig().docker_timeout_seconds,
        ),
        docker_network_enabled=load_bool_from_env(
            "SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED",
            default=SlotFlowSandboxConfig().docker_network_enabled,
        ),
        docker_idle_timeout_seconds=load_positive_int_from_env(
            "SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS",
            default=SlotFlowSandboxConfig().docker_idle_timeout_seconds,
        ),
        allow_host_docker_install=load_bool_from_env(
            "SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL",
            default=SlotFlowSandboxConfig().allow_host_docker_install,
        ),
    )


def load_agent_reach_config_from_env() -> SlotFlowAgentReachConfig:
    """Read the fixed Agent Reach host-bridge switch and resource limits."""

    defaults = SlotFlowAgentReachConfig()
    return SlotFlowAgentReachConfig(
        enabled=load_bool_from_env("SLOTFLOW_AGENT_REACH_ENABLED", default=True),
        home=load_path_from_env("SLOTFLOW_AGENT_REACH_HOME", default=defaults.home),
        timeout_seconds=load_positive_int_from_env(
            "SLOTFLOW_AGENT_REACH_TIMEOUT_SECONDS",
            default=defaults.timeout_seconds,
        ),
        max_output_bytes=load_positive_int_from_env(
            "SLOTFLOW_AGENT_REACH_MAX_OUTPUT_BYTES",
            default=defaults.max_output_bytes,
        ),
    )


def load_mcp_config_from_env(
    *,
    sandbox_config: SlotFlowSandboxConfig | None = None,
) -> SlotFlowMcpConfig:
    """Read environment MCP servers and append SlotFlow's protected Playwright preset."""

    resolved_sandbox = sandbox_config or load_sandbox_config_from_env()
    raw_config = load_optional_text_from_env("SLOTFLOW_MCP_CONFIG_JSON")
    if raw_config is not None:
        servers = load_mcp_servers_from_json(raw_config)
    else:
        servers = [
            SlotFlowMcpServerConfig(name=name)
            for name in load_optional_csv_list_from_env("SLOTFLOW_MCP_SERVERS") or []
            if not is_removed_default_mcp_server(name)
        ]

    servers = [server for server in servers if server.name != DEFAULT_PLAYWRIGHT_MCP_NAME]
    playwright = None
    if load_bool_from_env("SLOTFLOW_PLAYWRIGHT_MCP_ENABLED", default=True):
        playwright = build_playwright_mcp_server(sandbox_config=resolved_sandbox)
        servers.append(playwright)
    enabled = load_bool_from_env(
        "SLOTFLOW_MCP_ENABLED",
        default=raw_config is not None or bool(playwright and playwright.enabled),
    )
    return SlotFlowMcpConfig(enabled=enabled, servers=tuple(servers))


def build_playwright_mcp_server(
    *,
    sandbox_config: SlotFlowSandboxConfig,
) -> SlotFlowMcpServerConfig:
    """Build the fixed stdio preset for the pnpm-locked Playwright MCP package."""

    executable = REPO_ROOT / "frontend" / "scripts" / "playwright-mcp.mjs"
    enabled = sandbox_config.network_enabled
    args = [
        "--headless",
        "--isolated",
        "--block-service-workers",
        "--image-responses",
        "omit",
        "--codegen",
        "none",
        "--output-mode",
        "stdout",
        "--timeout-action",
        str(
            load_positive_int_from_env(
                "SLOTFLOW_PLAYWRIGHT_MCP_ACTION_TIMEOUT_MS",
                default=10_000,
            )
        ),
        "--timeout-navigation",
        str(
            load_positive_int_from_env(
                "SLOTFLOW_PLAYWRIGHT_MCP_NAVIGATION_TIMEOUT_MS",
                default=60_000,
            )
        ),
    ]
    if not sandbox_config.allow_private_network:
        args.extend(["--blocked-origins", ";".join(PLAYWRIGHT_PRIVATE_ORIGIN_GLOBS)])

    workspace_root = sandbox_config.resolved_workspace_root()

    host_path = os.pathsep.join(
        [
            str(Path.home() / ".volta" / "bin"),
            str(Path.home() / ".local" / "bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
    )
    return SlotFlowMcpServerConfig(
        name=DEFAULT_PLAYWRIGHT_MCP_NAME,
        enabled=enabled,
        config={
            "transport": "stdio",
            "command": str(executable),
            "args": args,
            "cwd": str(workspace_root),
            "env": {"HOME": str(Path.home()), "PATH": host_path},
        },
        order=-100,
        pinned=True,
        stateful=True,
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
        stateful = server_config.pop("stateful", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"MCP server {name!r} enabled must be a boolean")
        if not isinstance(stateful, bool):
            raise ValueError(f"MCP server {name!r} stateful must be a boolean")

        servers.append(
            SlotFlowMcpServerConfig(
                name=name.strip(),
                enabled=enabled,
                config=server_config,
                stateful=stateful,
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
