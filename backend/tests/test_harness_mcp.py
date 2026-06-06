"""Module 13 tests: SlotFlow harness MCP tools boundary."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import (
    SlotFlowMcpConfig,
    SlotFlowMcpServerConfig,
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


def test_build_harness_tools_includes_mcp_tools_after_safe_builtin() -> None:
    provider = CapturingMcpToolProvider()

    tools = build_harness_tools(
        features=_features(),
        mcp_config=SlotFlowMcpConfig(
            enabled=True,
            servers=(SlotFlowMcpServerConfig(name="filesystem"),),
        ),
        mcp_tool_provider=provider,
    )

    assert [tool.name for tool in tools] == ["slotflow_context", "mcp_echo"]
    assert provider.calls[0].servers[0].name == "filesystem"
