"""SlotFlow harness tool registry。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import (
    McpToolProvider,
    SlotFlowMcpConfig,
    build_mcp_proxy_tools,
    load_mcp_tools,
)
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.skills import SlotFlowSkillsConfigStore
from app.harness.subagents import SlotFlowSubagentConfig, build_subagent_tools
from app.harness.tool_spaces import tool_space_for_tool
from app.harness.tools.agent_reach import SlotFlowAgentReachConfig, build_agent_reach_tools
from app.harness.tools.builtins import ask_clarification_tool
from app.harness.tools.context_archive import build_context_archive_tools
from app.harness.tools.todo import write_todos_tool
from app.harness.tools.customization import build_customization_tools
from app.harness.tools.host_execution import is_unsafe_host_execution_tool_name
from app.harness.tools.markitdown import SlotFlowMarkItDownConfig, build_markitdown_tools
from app.harness.tools.network import build_network_tools
from app.harness.tools.sandbox import build_sandbox_tools
from app.harness.tools.workspace import build_workspace_tools
from app.harness.utils import dedupe_by_name

if TYPE_CHECKING:
    from app.harness.mcp import SlotFlowMcpConfigStore


def build_harness_tools(
    *,
    features: SlotFlowHarnessFeatures,
    model: str | BaseChatModel | None = None,
    run_context: RunContext | None = None,
    extra_tools: list[BaseTool] | None = None,
    mcp_config: SlotFlowMcpConfig | None = None,
    mcp_tool_provider: McpToolProvider | None = None,
    mcp_config_store: SlotFlowMcpConfigStore | None = None,
    skills_root: Path | None = None,
    skills_config_store: SlotFlowSkillsConfigStore | None = None,
    sandbox_config: SlotFlowSandboxConfig | None = None,
    agent_reach_config: SlotFlowAgentReachConfig | None = None,
    markitdown_config: SlotFlowMarkItDownConfig | None = None,
    subagent_config: SlotFlowSubagentConfig | None = None,
) -> list[BaseTool]:
    """组装本次 graph 要绑定的工具列表。

    `features` 会透传给 `build_subagent_tools`，用于决定是否启用子 agent 工具等能力。

    这里有两条**刻意不进主 agent** 的边界（工具集恒定，见 `harness/tool_spaces.py`）：

    - **MCP 原生 schema**：不论接了多少 server，主 agent 只拿到 `mcp_docs` / `mcp_call`
      两个固定代理；真实 MCP 工具被代理闭包持有，宿主侧直调。
    - **浏览器自动化**：playwright MCP 的 `browser_*` 工具整组交给 `browser` 垂类子代理，
      主 agent 通过 `task_tool(agent_name='browser', ...)` 委派。它们 schema 多、交互轮数长、
      每轮回大段页面快照——正是子代理上下文隔离最划算的场景。子代理未启用时（flash 模式）
      它们回落进上面的 MCP 代理，而不是整块消失。
    """

    mcp_tools = filter_unsafe_host_execution_tools(
        load_mcp_tools(
            config=mcp_config or SlotFlowMcpConfig(),
            provider=mcp_tool_provider,
        )
    )
    resolved_subagent_config = subagent_config or SlotFlowSubagentConfig()
    # 和 `build_subagent_tools` 的门槛保持一致：只有子代理真的会被建出来时，才把
    # browser_* 交给它。否则（flash 模式、无模型上下文）没有承载者，能力不能凭空消失——
    # 回落到通用 MCP 代理：多 2 个 schema，总好过用户在 flash 模式下打不开浏览器。
    subagents_available = (
        features.subagent_enabled
        and model is not None
        and run_context is not None
        and bool(resolved_subagent_config.enabled_profiles())
    )
    browser_mcp_tools = (
        [tool for tool in mcp_tools if tool_space_for_tool(tool) == "browser"]
        if subagents_available
        else []
    )
    # 用 id() 而不是 `not in`：BaseTool 是 pydantic 模型，`in` 会走逐字段 __eq__，
    # 既慢也可能把两个不同来源的同名工具判成同一个。
    browser_tool_ids = {id(tool) for tool in browser_mcp_tools}
    proxied_mcp_tools = [tool for tool in mcp_tools if id(tool) not in browser_tool_ids]
    mcp_proxy_tools = build_mcp_proxy_tools(proxied_mcp_tools)
    workspace_tools = build_workspace_tools(
        sandbox_config,
        thread_id=run_context.thread_id if run_context is not None else None,
    )
    sandbox_tools = build_sandbox_tools(
        sandbox_config,
        thread_id=run_context.thread_id if run_context is not None else None,
        skills_root=skills_root,
    )
    network_tools = build_network_tools(sandbox_config)
    resolved_sandbox_config = sandbox_config or SlotFlowSandboxConfig()
    agent_reach_tools = build_agent_reach_tools(
        agent_reach_config or SlotFlowAgentReachConfig(),
        sandbox_config=resolved_sandbox_config,
    )
    markitdown_tools = build_markitdown_tools(
        markitdown_config or SlotFlowMarkItDownConfig(),
        sandbox_config=resolved_sandbox_config,
        model=model,
        thread_id=run_context.thread_id if run_context is not None else None,
    )
    context_archive_tools = build_context_archive_tools()
    customization_tools = build_customization_tools(
        skills_root=skills_root,
        skills_config_store=skills_config_store,
        mcp_config_store=mcp_config_store,
        sandbox_config=sandbox_config,
    )
    subagent_tools = build_subagent_tools(
        features=features,
        config=subagent_config,
        model=model,
        run_context=run_context,
        environment_tools=[
            *workspace_tools,
            *sandbox_tools,
            *network_tools,
            *agent_reach_tools,
            *markitdown_tools,
            *context_archive_tools,
            *customization_tools,
            *mcp_proxy_tools,
            # 真实 browser_* 工具只在这里出现：子代理按 tool_spaces 过滤后拿到它们。
            *browser_mcp_tools,
        ],
    )
    # Expose write_todos in every mode. Pro/Ultra still get the proactive planning
    # prompt; Flash keeps the tool available for explicit requests like "测试 todo 功能"
    # instead of forcing the model to simulate a todo list in prose.
    todo_tools = [write_todos_tool]
    return dedupe_by_name(
        filter_unsafe_host_execution_tools(
            [
                *(extra_tools or []),
                ask_clarification_tool,
                *todo_tools,
                *context_archive_tools,
                *workspace_tools,
                *sandbox_tools,
                *network_tools,
                *agent_reach_tools,
                *markitdown_tools,
                *customization_tools,
                *subagent_tools,
                *mcp_proxy_tools,
            ]
        )
    )


def filter_unsafe_host_execution_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Remove host shell/code execution tools; code execution must use sandbox_exec."""

    return [tool for tool in tools if not is_unsafe_host_execution_tool(tool)]


def is_unsafe_host_execution_tool(tool: BaseTool) -> bool:
    return is_unsafe_host_execution_tool_name(tool.name)
