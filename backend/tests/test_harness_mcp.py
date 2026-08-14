"""Module 13 tests: SlotFlow harness MCP tools boundary."""

from __future__ import annotations

import json

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
    mcp_server_name,
)
from app.harness.tools.registry import build_harness_tools


@tool("mcp_echo")
def mcp_echo_tool(value: str) -> str:
    """Echo a value from a fake MCP provider."""

    return value


@tool("browser_navigate")
def browser_navigate_tool(url: str) -> str:
    """Fake playwright-style MCP browser tool used to prove the vertical-subagent boundary."""

    return url


@tool("bash")
def mcp_bash_tool(command: str) -> str:
    """Unsafe fake MCP bash tool used to prove registry filtering."""

    return command


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


def test_mcp_tools_are_proxied_instead_of_bound_as_individual_schemas() -> None:
    """MCP 收敛边界:不管接多少 server,主 agent 只多出 mcp_docs / mcp_call 两个工具。"""

    provider = CapturingMcpToolProvider()

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )

    names = [tool.name for tool in tools]
    assert "mcp_docs" in names
    assert "mcp_call" in names
    # 真实 MCP 工具的 schema 绝不进模型工具面。
    assert "mcp_echo" not in names
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    assert provider.calls[0].servers[0].name == "filesystem"


@pytest.mark.asyncio
async def test_mcp_docs_lists_the_real_tool_and_mcp_call_reaches_it() -> None:
    provider = CapturingMcpToolProvider()

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )
    by_name = {tool.name: tool for tool in tools}

    docs = json.loads(by_name["mcp_docs"].invoke({"query": "echo"}))
    assert [entry["tool"] for entry in docs["tools"]] == ["mcp_echo"]
    assert "value" in docs["tools"][0]["arguments"]

    called = json.loads(
        await by_name["mcp_call"].ainvoke(
            {"server": docs["tools"][0]["server"], "tool": "mcp_echo", "arguments": {"value": "hi"}}
        )
    )
    assert called["result"] == "hi"


@pytest.mark.asyncio
async def test_mcp_call_rejects_a_tool_name_the_model_invented() -> None:
    provider = CapturingMcpToolProvider()

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )
    by_name = {tool.name: tool for tool in tools}

    result = json.loads(
        await by_name["mcp_call"].ainvoke(
            {"server": "filesystem", "tool": "read_everything", "arguments": {}}
        )
    )
    assert result["error"] == "unknown_mcp_tool"
    assert "mcp_docs" in result["hint"]


def test_build_harness_tools_filters_unsafe_mcp_execution_tools() -> None:
    provider = CapturingMcpToolProvider(tools=[mcp_bash_tool, mcp_echo_tool])

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )
    by_name = {tool.name: tool for tool in tools}

    assert "sandbox_exec" in by_name
    assert "bash" not in by_name
    # 被过滤掉的宿主执行工具也不能从 mcp_docs 手册的后门重新暴露出来。
    docs = json.loads(by_name["mcp_docs"].invoke({"query": "bash"}))
    assert [entry["tool"] for entry in docs["tools"]] == ["mcp_echo"]


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
    # 来源 server 标签由 loader 打上:mcp_docs/mcp_call 靠它把工具归到对应 server,
    # 否则同名工具跨 server 撞车时无法区分(adapter 自己不写这个字段)。
    assert mcp_server_name(loaded_tools[0]) == "filesystem"
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


def test_browser_mcp_tools_fall_back_to_the_proxy_when_no_subagent_can_own_them() -> None:
    """flash 模式没有子代理承载 browser_*,能力不能凭空消失——回落到通用 MCP 代理。"""

    provider = CapturingMcpToolProvider(tools=[browser_navigate_tool, mcp_echo_tool])

    tools = build_harness_tools(
        features=_features(),  # subagent_enabled=False
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="playwright"),),
        ),
        mcp_tool_provider=provider,
    )
    by_name = {tool.name: tool for tool in tools}

    assert "browser_navigate" not in by_name  # 仍然不直绑 schema
    docs = json.loads(by_name["mcp_docs"].invoke({"query": "browser navigate"}))
    assert "browser_navigate" in [entry["tool"] for entry in docs["tools"]]
