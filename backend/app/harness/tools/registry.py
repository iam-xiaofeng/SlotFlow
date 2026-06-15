"""SlotFlow harness tool registry。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import McpToolProvider, SlotFlowMcpConfig, load_mcp_tools
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.skills import SlotFlowSkillsConfigStore
from app.harness.subagents import SlotFlowSubagentConfig, build_subagent_tools
from app.harness.tools.builtins import slotflow_context_tool
from app.harness.tools.customization import build_customization_tools
from app.harness.tools.network import build_network_tools
from app.harness.tools.workspace import build_workspace_tools

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
    subagent_config: SlotFlowSubagentConfig | None = None,
) -> list[BaseTool]:
    """组装本次 graph 要绑定的工具列表。

    `features` 参数暂时没有分支逻辑，但保留在函数签名里，是为了后续把
    `subagent_enabled`、MCP 开关等能力都收敛到同一入口。
    """

    _ = features
    mcp_tools = load_mcp_tools(
        config=mcp_config or SlotFlowMcpConfig(),
        provider=mcp_tool_provider,
    )
    workspace_tools = build_workspace_tools(sandbox_config)
    network_tools = build_network_tools(sandbox_config)
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
            slotflow_context_tool,
            *workspace_tools,
            *network_tools,
            *customization_tools,
            *mcp_tools,
        ],
    )
    return dedupe_tools_by_name(
        [
            *(extra_tools or []),
            slotflow_context_tool,
            *workspace_tools,
            *network_tools,
            *customization_tools,
            *subagent_tools,
            *mcp_tools,
        ]
    )


def dedupe_tools_by_name(tools: list[BaseTool]) -> list[BaseTool]:
    """按 tool.name 去重，保留更早出现的工具。"""

    seen_names: set[str] = set()
    unique_tools: list[BaseTool] = []
    for tool in tools:
        if tool.name in seen_names:
            continue
        unique_tools.append(tool)
        seen_names.add(tool.name)
    return unique_tools
