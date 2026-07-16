"""SlotFlow MCP tools loader boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any, Protocol

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


class MultiServerMcpToolProvider:
    """Real MCP provider backed by LangChain's `MultiServerMCPClient`."""

    def __init__(
        self,
        *,
        client_factory: Callable[[dict[str, dict[str, Any]]], Any] | None = None,
        persistent_tool_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._client_factory = client_factory or self._default_client_factory
        self._persistent_tool_loader = persistent_tool_loader or self._default_persistent_tool_loader
        self._loaded_config: SlotFlowMcpConfig | None = None
        self._tools: list[BaseTool] | None = None
        self._session_stack: AsyncExitStack | None = None
        self._load_lock = asyncio.Lock()

    async def aload_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        """Load tools, retaining sessions only for explicitly stateful servers."""

        active_config = active_mcp_config(config)
        async with self._load_lock:
            if active_config is None:
                await self._close_sessions()
                self._loaded_config = None
                self._tools = []
                return []

            if self._loaded_config == active_config and self._tools is not None:
                return list(self._tools)

            await self._close_sessions()
            connections = build_multi_server_mcp_connections(active_config)
            client = self._client_factory(connections)
            stateful_servers = [server for server in active_config.servers if server.stateful]

            if not stateful_servers:
                tools_result = client.get_tools()
                tools = await tools_result if inspect.isawaitable(tools_result) else tools_result
                loaded_tools = list(tools)
            else:
                stack = AsyncExitStack()
                loaded_tools = []
                try:
                    for server in active_config.servers:
                        if server.stateful:
                            session = await stack.enter_async_context(client.session(server.name))
                            result = self._persistent_tool_loader(
                                session,
                                server_name=server.name,
                            )
                        else:
                            result = client.get_tools(server_name=server.name)
                        server_tools = await result if inspect.isawaitable(result) else result
                        loaded_tools.extend(server_tools)
                except BaseException:
                    await stack.aclose()
                    raise
                self._session_stack = stack

            self._loaded_config = active_config
            self._tools = loaded_tools
            return list(self._tools)

    def load_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        """Return cached tools after `aload_tools()` has prepared them."""

        active_config = active_mcp_config(config)
        if active_config is None:
            return []
        if self._loaded_config != active_config or self._tools is None:
            raise RuntimeError(
                "real MCP tools must be loaded asynchronously before building the graph",
            )
        return list(self._tools)

    async def aclose(self) -> None:
        """Close retained stateful MCP sessions and clear the cached tools."""

        async with self._load_lock:
            await self._close_sessions()
            self._loaded_config = None
            self._tools = None

    async def _close_sessions(self) -> None:
        stack = self._session_stack
        self._session_stack = None
        if stack is not None:
            await stack.aclose()

    @staticmethod
    def _default_persistent_tool_loader(session: Any, *, server_name: str) -> Any:
        from langchain_mcp_adapters.tools import load_mcp_tools

        return load_mcp_tools(session, server_name=server_name)

    @staticmethod
    def _default_client_factory(connections: dict[str, dict[str, Any]]) -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        return MultiServerMCPClient(connections)


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

    active_config = active_mcp_config(config)
    if active_config is None:
        return []

    return (provider or EmptyMcpToolProvider()).load_tools(active_config)


async def ensure_mcp_tools_loaded(
    *,
    config: SlotFlowMcpConfig,
    provider: McpToolProvider | None = None,
) -> list[BaseTool]:
    """Run async provider setup before the synchronous harness builder asks for tools."""

    if provider is None:
        return []

    aload_tools = getattr(provider, "aload_tools", None)
    if callable(aload_tools):
        return list(await aload_tools(config))
    return provider.load_tools(config)


def active_mcp_config(config: SlotFlowMcpConfig) -> SlotFlowMcpConfig | None:
    """Return enabled MCP config with disabled servers filtered out."""

    if not config.enabled:
        return None

    active_servers = config.active_servers()
    if not active_servers:
        return None

    return SlotFlowMcpConfig(
        enabled=True,
        servers=active_servers,
    )


def build_multi_server_mcp_connections(config: SlotFlowMcpConfig) -> dict[str, dict[str, Any]]:
    """Convert SlotFlow MCP config into MultiServerMCPClient connections."""

    connections: dict[str, dict[str, Any]] = {}
    for server in config.active_servers():
        if server.config is None:
            raise ValueError(f"MCP server {server.name!r} is missing connection config")
        connections[server.name] = dict(server.config)
    return connections
