"""Module 13 tests: SlotFlow harness MCP tools boundary."""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool, tool

from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import (
    MultiServerMcpToolProvider,
    SlotFlowMcpConfig,
    SlotFlowMcpServerConfig,
    build_multi_server_mcp_connections,
    ensure_mcp_tools_loaded,
    load_mcp_tools,
)
from app.harness.tools.registry import build_harness_tools


@tool("mcp_echo")
def mcp_echo_tool(value: str) -> str:
    """Echo a value from a fake MCP provider."""

    return value


class CapturingMcpToolProvider:
    """Test provider that records the config it receives."""

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self.calls: list[SlotFlowMcpConfig] = []
        self._tools = tools or [mcp_echo_tool]

    def load_tools(self, config: SlotFlowMcpConfig) -> list[BaseTool]:
        self.calls.append(config)
        return list(self._tools)


class FakeMultiServerMcpClient:
    """Fake LangChain MCP client used to avoid starting a real MCP server in tests."""

    def __init__(
        self,
        connections: dict[str, dict],
        *,
        captured_connections: list[dict[str, dict]],
    ) -> None:
        self._connections = connections
        self._captured_connections = captured_connections

    async def get_tools(self) -> list[BaseTool]:
        self._captured_connections.append(self._connections)
        return [mcp_echo_tool]


def _features() -> SlotFlowHarnessFeatures:
    return SlotFlowHarnessFeatures(
        thinking_enabled=True,
        plan_enabled=True,
        subagent_enabled=False,
    )


def test_load_mcp_tools_does_not_call_provider_when_disabled() -> None:
    provider = CapturingMcpToolProvider()

    tools = load_mcp_tools(
        config=SlotFlowMcpConfig(
            enabled=False,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        provider=provider,
    )

    assert tools == []
    assert provider.calls == []


def test_load_mcp_tools_filters_disabled_servers_before_provider_call() -> None:
    provider = CapturingMcpToolProvider()

    tools = load_mcp_tools(
        config=SlotFlowMcpConfig(
            enabled=True,
            servers=(
                SlotFlowMcpServerConfig(name="filesystem"),
                SlotFlowMcpServerConfig(name="disabled", enabled=False),
            ),
        ),
        provider=provider,
    )

    assert tools == [mcp_echo_tool]
    assert len(provider.calls) == 1
    assert provider.calls[0] == SlotFlowMcpConfig(
        enabled=True,
        servers=(SlotFlowMcpServerConfig(name="filesystem"),),
    )


def test_build_harness_tools_includes_mcp_tools_after_workspace_tools() -> None:
    provider = CapturingMcpToolProvider()

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )

    assert [tool.name for tool in tools] == [
        "ask_clarification",
        "slotflow_context",
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
        "web_fetch",
        "web_search",
        "find-skills",
        "skill_list",
        "skill_install",
        "mcp_add_http",
        "mcp_echo",
    ]
    assert provider.calls[0].servers[0].name == "filesystem"


def test_build_multi_server_mcp_connections_requires_real_server_config() -> None:
    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(SlotFlowMcpServerConfig(name="filesystem"),),
    )

    try:
        build_multi_server_mcp_connections(config)
    except ValueError as exc:
        assert "missing connection config" in str(exc)
    else:
        raise AssertionError("expected missing MCP config to fail")


@pytest.mark.asyncio
async def test_multi_server_mcp_provider_loads_and_caches_tools() -> None:
    captured_connections: list[dict[str, dict]] = []

    def client_factory(connections: dict[str, dict]) -> FakeMultiServerMcpClient:
        return FakeMultiServerMcpClient(
            connections,
            captured_connections=captured_connections,
        )

    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="filesystem",
                config={
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-m", "fake_mcp_server"],
                },
            ),
        ),
    )
    provider = MultiServerMcpToolProvider(client_factory=client_factory)

    loaded_tools = await provider.aload_tools(config)
    cached_tools = provider.load_tools(config)

    assert loaded_tools == [mcp_echo_tool]
    assert cached_tools == [mcp_echo_tool]
    assert captured_connections == [
        {
            "filesystem": {
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "fake_mcp_server"],
            }
        }
    ]


def test_multi_server_mcp_provider_requires_async_preload() -> None:
    provider = MultiServerMcpToolProvider(client_factory=lambda connections: None)
    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="filesystem",
                config={"transport": "stdio", "command": "python", "args": []},
            ),
        ),
    )

    try:
        provider.load_tools(config)
    except RuntimeError as exc:
        assert "loaded asynchronously" in str(exc)
    else:
        raise AssertionError("expected sync load before preload to fail")


@pytest.mark.asyncio
async def test_ensure_mcp_tools_loaded_calls_async_provider_before_registry() -> None:
    captured_connections: list[dict[str, dict]] = []
    provider = MultiServerMcpToolProvider(
        client_factory=lambda connections: FakeMultiServerMcpClient(
            connections,
            captured_connections=captured_connections,
        )
    )
    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="search",
                config={"transport": "streamable_http", "url": "http://localhost:8000/mcp"},
            ),
        ),
    )

    await ensure_mcp_tools_loaded(config=config, provider=provider)

    assert [tool.name for tool in provider.load_tools(config)] == ["mcp_echo"]
    assert captured_connections == [
        {
            "search": {
                "transport": "streamable_http",
                "url": "http://localhost:8000/mcp",
            }
        }
    ]
