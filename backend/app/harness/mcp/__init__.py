"""SlotFlow MCP tool source abstraction."""

from app.harness.mcp.config import SlotFlowMcpConfig, SlotFlowMcpServerConfig
from app.harness.mcp.loader import (
    MCP_SERVER_METADATA_KEY,
    EmptyMcpToolProvider,
    McpToolProvider,
    MultiServerMcpToolProvider,
    build_multi_server_mcp_connections,
    ensure_mcp_tools_loaded,
    load_mcp_tools,
)
from app.harness.mcp.proxy import build_mcp_manual, build_mcp_proxy_tools, mcp_server_name
from app.harness.mcp.store import (
    McpServerNotFoundError,
    ProtectedMcpServerError,
    SlotFlowMcpConfigStore,
    is_removed_default_mcp_server,
)

__all__ = [
    "EmptyMcpToolProvider",
    "MCP_SERVER_METADATA_KEY",
    "McpServerNotFoundError",
    "McpToolProvider",
    "MultiServerMcpToolProvider",
    "ProtectedMcpServerError",
    "SlotFlowMcpConfigStore",
    "SlotFlowMcpConfig",
    "SlotFlowMcpServerConfig",
    "build_mcp_manual",
    "build_mcp_proxy_tools",
    "build_multi_server_mcp_connections",
    "ensure_mcp_tools_loaded",
    "is_removed_default_mcp_server",
    "load_mcp_tools",
    "mcp_server_name",
]
