"""SlotFlow 本地 agent runtime 装配层。

这个包把“真实 agent 怎么创建、checkpointer 怎么挂进去、模型怎么按 provider 选择”
收拢成一个小边界，按职责拆成子模块：

- `env`：环境变量解析小工具；
- `config`：`SlotFlowRuntimeConfig` 与从环境装配它的入口；
- `models`：通过 ChatLiteLLM 为所有 provider 创建统一 chat model；
- `checkpointer`：LangGraph checkpointer 创建与关闭；
- `adapter`：`RuntimeBackedAgentAdapter` 与 graph 组装入口。

为保持既有导入路径不变，这里重新导出全部公开符号，调用方仍可 `from app.chat.runtime
import ...`。
"""

from __future__ import annotations

from app.chat.runtime import adapter, checkpointer, config, env, models
from app.chat.runtime.adapter import (
    RuntimeBackedAgentAdapter,
    build_agent_adapter,
    create_langgraph_agent_graph,
)
from app.chat.runtime.checkpointer import (
    aclose_checkpointer,
    close_checkpointer,
    create_async_checkpointer,
    create_checkpointer,
    create_postgres_checkpointer,
    create_sqlite_checkpointer,
)
from app.chat.runtime.config import (
    DEFAULT_CHECKPOINTER_SQLITE_PATH,
    DEFAULT_DEEPSEEK_SYSTEM_PROMPT,
    DEFAULT_MCP_CONFIG_PATH,
    DEFAULT_MEMORY_SQLITE_PATH,
    DEFAULT_SKILLS_CONFIG_PATH,
    DEFAULT_SKILLS_ROOT,
    CheckpointerBackend,
    SlotFlowRuntimeConfig,
    build_mcp_config_store_from_env,
    build_mcp_tool_provider,
    build_memory_store_from_env,
    build_skills_config_store_from_env,
    load_mcp_config_from_env,
    load_mcp_servers_from_json,
    load_middleware_config_from_env,
    load_runtime_config_from_env,
    load_sandbox_config_from_env,
    refresh_runtime_mcp_config,
    refresh_runtime_skills_config,
)
from app.chat.runtime.env import (
    load_bool_from_env,
    load_optional_csv_list_from_env,
    load_optional_csv_set_from_env,
    load_optional_path_from_env,
    load_optional_text_from_env,
    load_path_from_env,
    load_positive_int_from_env,
)
from app.chat.runtime.models import (
    build_litellm_model_kwargs,
    create_chat_model,
    create_model_for_context,
    infer_model_provider,
)

__all__ = [
    "adapter",
    "checkpointer",
    "config",
    "env",
    "models",
    "RuntimeBackedAgentAdapter",
    "build_agent_adapter",
    "create_langgraph_agent_graph",
    "aclose_checkpointer",
    "close_checkpointer",
    "create_async_checkpointer",
    "create_checkpointer",
    "create_postgres_checkpointer",
    "create_sqlite_checkpointer",
    "CheckpointerBackend",
    "DEFAULT_CHECKPOINTER_SQLITE_PATH",
    "DEFAULT_DEEPSEEK_SYSTEM_PROMPT",
    "DEFAULT_MCP_CONFIG_PATH",
    "DEFAULT_MEMORY_SQLITE_PATH",
    "DEFAULT_SKILLS_CONFIG_PATH",
    "DEFAULT_SKILLS_ROOT",
    "SlotFlowRuntimeConfig",
    "build_mcp_config_store_from_env",
    "build_mcp_tool_provider",
    "build_memory_store_from_env",
    "build_skills_config_store_from_env",
    "load_mcp_config_from_env",
    "load_mcp_servers_from_json",
    "load_middleware_config_from_env",
    "load_runtime_config_from_env",
    "load_sandbox_config_from_env",
    "refresh_runtime_mcp_config",
    "refresh_runtime_skills_config",
    "load_bool_from_env",
    "load_optional_csv_list_from_env",
    "load_optional_csv_set_from_env",
    "load_optional_path_from_env",
    "load_optional_text_from_env",
    "load_path_from_env",
    "load_positive_int_from_env",
    "build_litellm_model_kwargs",
    "create_chat_model",
    "create_model_for_context",
    "infer_model_provider",
]
