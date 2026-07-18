"""SlotFlow MCP tools loader boundary."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from app.harness.mcp.config import SlotFlowMcpConfig


_logger = logging.getLogger(__name__)


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
        self._load_errors: dict[str, str] = {}
        self._session_stack: AsyncExitStack | None = None
        self._load_lock = asyncio.Lock()

    async def aload_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        """Load each server independently and retain successful stateful sessions."""

        active_config = active_mcp_config(config)
        async with self._load_lock:
            if active_config is None:
                await self._close_sessions()
                self._loaded_config = None
                self._tools = []
                self._load_errors = {}
                return []

            if self._loaded_config == active_config and self._tools is not None:
                return list(self._tools)

            await self._close_sessions()
            connections = build_multi_server_mcp_connections(active_config)
            stack = AsyncExitStack()
            loaded_tools: list[BaseTool] = []
            load_errors: dict[str, str] = {}
            try:
                for server in active_config.servers:
                    client = self._client_factory(
                        {server.name: connections[server.name]},
                    )
                    server_stack = AsyncExitStack()
                    try:
                        if server.stateful:
                            session = await server_stack.enter_async_context(
                                client.session(server.name),
                            )
                            result = self._persistent_tool_loader(
                                session,
                                server_name=server.name,
                            )
                        else:
                            result = client.get_tools()
                        server_tools = (
                            await result if inspect.isawaitable(result) else result
                        )
                    except Exception as exc:  # noqa: BLE001 - optional servers degrade alone
                        await server_stack.aclose()
                        summary = _exception_summary(exc)
                        load_errors[server.name] = summary
                        _logger.warning(
                            "MCP server %s unavailable during tool discovery: %s",
                            server.name,
                            summary,
                        )
                        continue

                    loaded_tools.extend(server_tools)
                    if server.stateful:
                        stack.push_async_callback(server_stack.aclose)
            except BaseException:
                await stack.aclose()
                raise

            self._session_stack = stack
            self._loaded_config = active_config
            self._tools = loaded_tools
            self._load_errors = load_errors
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

    @property
    def load_errors(self) -> dict[str, str]:
        """Return sanitized per-server discovery failures from the latest load."""

        return dict(self._load_errors)

    async def aclose(self) -> None:
        """Close retained stateful MCP sessions and clear the cached tools."""

        async with self._load_lock:
            await self._close_sessions()
            self._loaded_config = None
            self._tools = None
            self._load_errors = {}

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


def _first_exception_leaf(error: BaseException) -> BaseException:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return _first_exception_leaf(error.exceptions[0])
    return error


def _exception_summary(error: BaseException) -> str:
    return type(_first_exception_leaf(error)).__name__


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
