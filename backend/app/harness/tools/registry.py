"""SlotFlow harness tool registry。"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import McpToolProvider, SlotFlowMcpConfig, load_mcp_tools
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools.builtins import slotflow_context_tool
from app.harness.tools.workspace import build_workspace_tools


def build_harness_tools(
    *,
    features: SlotFlowHarnessFeatures,
    extra_tools: list[BaseTool] | None = None,
    mcp_config: SlotFlowMcpConfig | None = None,
    mcp_tool_provider: McpToolProvider | None = None,
    sandbox_config: SlotFlowSandboxConfig | None = None,
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
    return dedupe_tools_by_name(
        [
            *(extra_tools or []),
            slotflow_context_tool,
            *workspace_tools,
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
