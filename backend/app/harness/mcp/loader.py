"""SlotFlow MCP tools loader boundary."""

from __future__ import annotations

from typing import Protocol

from langchain_core.tools import BaseTool

from app.harness.mcp.config import SlotFlowMcpConfig


class McpToolProvider(Protocol):
    """Source that converts MCP server config into LangChain tools."""

    def load_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        """Return tools ready to bind to a LangGraph agent."""


class EmptyMcpToolProvider:
    """Default provider: do not connect to any external MCP server."""

    def load_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        return []


def load_mcp_tools(
    *,
    config: SlotFlowMcpConfig,
    provider: McpToolProvider | None = None,
) -> list[BaseTool]:
    """Load MCP tools according to explicit SlotFlow config.

    Module 13 deliberately avoids network/process work. A later real
    MultiServerMCPClient adapter only needs to implement McpToolProvider and pass
    it into the tools registry.
    """

    if not config.enabled:
        return []

    active_servers = config.active_servers()
    if not active_servers:
        return []

    active_config = SlotFlowMcpConfig(
        enabled=True,
        servers=active_servers,
    )
    return (provider or EmptyMcpToolProvider()).load_tools(active_config)
