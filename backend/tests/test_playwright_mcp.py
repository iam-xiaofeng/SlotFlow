"""Contracts for the protected stateful Playwright MCP preset."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import tool

from app.chat.runtime import SlotFlowRuntimeConfig
from app.chat.runtime.config import build_playwright_mcp_server, load_mcp_config_from_env
from app.harness.mcp import (
    MultiServerMcpToolProvider,
    ProtectedMcpServerError,
    SlotFlowMcpConfig,
    SlotFlowMcpConfigStore,
    SlotFlowMcpServerConfig,
    ensure_mcp_tools_loaded,
)
from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app


class FakeSession:
    def __init__(self) -> None:
        self.open = False


class FakeStatefulClient:
    def __init__(self, connections: dict[str, dict], events: list[str]) -> None:
        self.connections = connections
        self.events = events
        self.session_value = FakeSession()

    @asynccontextmanager
    async def session(self, server_name: str):
        self.events.append(f"open:{server_name}")
        self.session_value.open = True
        try:
            yield self.session_value
        finally:
            self.session_value.open = False
            self.events.append(f"close:{server_name}")


class FakeFailingClient:
    async def get_tools(self) -> None:
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [TimeoutError()],
        )


def persistent_tool_loader(session: FakeSession, *, server_name: str):
    @tool("stateful_ping")
    def stateful_ping() -> str:
        """Prove that the MCP session remains open between tool calls."""

        if not session.open:
            raise RuntimeError("session closed")
        return server_name

    return [stateful_ping]


def test_builtin_playwright_preset_is_fixed_and_workspace_scoped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SLOTFLOW_PLAYWRIGHT_MCP_ACTION_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("SLOTFLOW_PLAYWRIGHT_MCP_NAVIGATION_TIMEOUT_MS", raising=False)
    workspace = tmp_path / "workspace"

    server = build_playwright_mcp_server(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=workspace)
    )

    assert server.name == "playwright"
    assert server.enabled is True
    assert server.pinned is True
    assert server.stateful is True
    assert server.order == -100
    assert not workspace.exists(), "config construction must stay side-effect free"
    assert server.config is not None
    assert server.config["transport"] == "stdio"
    assert server.config["command"].endswith("frontend/scripts/playwright-mcp.mjs")
    assert server.config["cwd"] == str(workspace.resolve())
    args = server.config["args"]
    assert {"--headless", "--isolated", "--block-service-workers"} <= set(args)
    assert "--allow-unrestricted-file-access" not in args
    assert "--caps" not in args
    blocked = args[args.index("--blocked-origins") + 1]
    assert "localhost" in blocked
    assert "127.*" in blocked
    assert "169.254.*" in blocked
    assert "[fe80::*]" in blocked


def test_private_network_override_removes_best_effort_origin_block(tmp_path: Path) -> None:
    server = build_playwright_mcp_server(
        sandbox_config=SlotFlowSandboxConfig(
            workspace_root=tmp_path,
            allow_private_network=True,
        )
    )
    assert server.config is not None
    assert "--blocked-origins" not in server.config["args"]


def test_runtime_mcp_config_includes_protected_playwright_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SLOTFLOW_PLAYWRIGHT_MCP_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_CONFIG_JSON", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_SERVERS", raising=False)

    config = load_mcp_config_from_env(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path)
    )

    assert config.enabled is True
    assert [server.name for server in config.servers] == ["playwright"]
    assert config.servers[0].stateful is True


def test_explicit_playwright_disable_omits_preset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLOTFLOW_PLAYWRIGHT_MCP_ENABLED", "false")
    monkeypatch.delenv("SLOTFLOW_MCP_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_CONFIG_JSON", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_SERVERS", raising=False)

    config = load_mcp_config_from_env(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path)
    )
    assert config == SlotFlowMcpConfig()


def test_builtin_preset_cannot_be_shadowed_or_deleted(tmp_path: Path) -> None:
    base = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="playwright",
                config={"transport": "stdio", "command": "/fixed/playwright-mcp", "args": []},
                stateful=True,
            ),
        ),
    )
    store = SlotFlowMcpConfigStore(tmp_path / "mcp.json", base_config=base)

    with pytest.raises(ProtectedMcpServerError):
        store.upsert_http_server(name="playwright", url="http://localhost:9999/mcp")
    with pytest.raises(ProtectedMcpServerError):
        store.delete_user_server("playwright")

    disabled = store.set_server_enabled("playwright", False)
    assert disabled.enabled is False
    assert disabled.stateful is True
    assert store.load_config().servers[0].enabled is False


@pytest.mark.asyncio
async def test_stateful_provider_keeps_session_until_closed() -> None:
    events: list[str] = []
    clients: list[FakeStatefulClient] = []

    def client_factory(connections: dict[str, dict]) -> FakeStatefulClient:
        client = FakeStatefulClient(connections, events)
        clients.append(client)
        return client

    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="playwright",
                config={"transport": "stdio", "command": "playwright-mcp", "args": []},
                stateful=True,
            ),
        ),
    )
    provider = MultiServerMcpToolProvider(
        client_factory=client_factory,
        persistent_tool_loader=persistent_tool_loader,
    )

    tools = await provider.aload_tools(config)
    assert events == ["open:playwright"]
    assert clients[0].connections == {
        "playwright": {"transport": "stdio", "command": "playwright-mcp", "args": []}
    }
    assert tools[0].invoke({}) == "playwright"
    assert provider.load_tools(config)[0].invoke({}) == "playwright"

    await provider.aclose()
    assert events == ["open:playwright", "close:playwright"]
    with pytest.raises(RuntimeError, match="session closed"):
        tools[0].invoke({})


@pytest.mark.asyncio
async def test_failed_optional_server_does_not_close_healthy_stateful_session() -> None:
    events: list[str] = []

    def client_factory(
        connections: dict[str, dict],
    ) -> FakeStatefulClient | FakeFailingClient:
        server_name = next(iter(connections))
        if server_name == "playwright":
            return FakeStatefulClient(connections, events)
        return FakeFailingClient()

    config = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="playwright",
                config={"transport": "stdio", "command": "playwright-mcp", "args": []},
                stateful=True,
            ),
            SlotFlowMcpServerConfig(
                name="offline",
                config={"transport": "streamable_http", "url": "http://localhost:9999/mcp"},
            ),
        ),
    )
    provider = MultiServerMcpToolProvider(
        client_factory=client_factory,
        persistent_tool_loader=persistent_tool_loader,
    )

    tools = await provider.aload_tools(config)

    assert [tool.name for tool in tools] == ["stateful_ping"]
    assert tools[0].invoke({}) == "playwright"
    assert provider.load_errors == {"offline": "TimeoutError"}
    assert events == ["open:playwright"]

    await provider.aclose()
    assert events == ["open:playwright", "close:playwright"]


@pytest.mark.asyncio
async def test_disabling_config_closes_retained_stateful_session() -> None:
    events: list[str] = []
    provider = MultiServerMcpToolProvider(
        client_factory=lambda connections: FakeStatefulClient(connections, events),
        persistent_tool_loader=persistent_tool_loader,
    )
    enabled = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="playwright",
                config={"transport": "stdio", "command": "playwright-mcp", "args": []},
                stateful=True,
            ),
        ),
    )

    await ensure_mcp_tools_loaded(config=enabled, provider=provider)
    await ensure_mcp_tools_loaded(config=SlotFlowMcpConfig(), provider=provider)
    assert events == ["open:playwright", "close:playwright"]


def test_playwright_preset_api_is_toggleable_but_protected(tmp_path: Path) -> None:
    server = build_playwright_mcp_server(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )
    base = SlotFlowMcpConfig(enabled=True, servers=(server,))
    runtime = SlotFlowRuntimeConfig(
        mcp_config=base,
        mcp_config_store=SlotFlowMcpConfigStore(tmp_path / "mcp.json", base_config=base),
        sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace"),
    )
    client = TestClient(create_app(runtime_config=runtime))

    listed = client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "name": "playwright",
            "enabled": True,
            "transport": "stdio",
            "url": None,
            "source": "environment",
            "protected": True,
            "order": -100,
            "pinned": True,
            "stateful": True,
        }
    ]

    toggled = client.patch("/api/mcp/servers/playwright", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
    assert toggled.json()["stateful"] is True
    assert client.delete("/api/mcp/servers/playwright").status_code == 403
    conflict = client.post(
        "/api/mcp/servers",
        json={"name": "playwright", "url": "http://localhost:9999/mcp"},
    )
    assert conflict.status_code == 400
