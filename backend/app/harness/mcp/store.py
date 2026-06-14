"""Persistent user MCP configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.harness.mcp.config import SlotFlowMcpConfig, SlotFlowMcpServerConfig


class McpServerNotFoundError(KeyError):
    """Raised when an MCP server is missing."""


class ProtectedMcpServerError(ValueError):
    """Raised when an environment MCP server would be deleted."""


class SlotFlowMcpConfigStore:
    """Merge environment MCP config with user-managed servers and overrides."""

    def __init__(
        self,
        path: str | Path,
        *,
        base_config: SlotFlowMcpConfig | None = None,
    ) -> None:
        self.path = Path(path)
        self.base_config = base_config or SlotFlowMcpConfig()

    def load_config(self) -> SlotFlowMcpConfig:
        user_servers = self.load_user_servers()
        overrides = self.load_overrides()
        servers_by_name = {
            server.name: apply_server_override(server, overrides.get(server.name))
            for server in self.base_config.servers
        }
        for server in user_servers:
            servers_by_name[server.name] = server

        servers = tuple(servers_by_name.values())
        return SlotFlowMcpConfig(
            enabled=self.base_config.enabled or bool(user_servers),
            servers=servers,
        )

    def load_user_servers(self) -> list[SlotFlowMcpServerConfig]:
        data = self._read_data()
        raw_servers = data.get("servers", {})
        if not isinstance(raw_servers, dict):
            return []

        servers: list[SlotFlowMcpServerConfig] = []
        for name, raw_config in raw_servers.items():
            if not isinstance(name, str) or not isinstance(raw_config, dict):
                continue
            server = server_from_mapping(name, raw_config)
            if server is not None:
                servers.append(server)
        return sorted(servers, key=lambda server: server.name)

    def load_overrides(self) -> dict[str, dict[str, Any]]:
        data = self._read_data()
        raw_overrides = data.get("overrides", {})
        if not isinstance(raw_overrides, dict):
            return {}
        return {
            name: dict(raw_config)
            for name, raw_config in raw_overrides.items()
            if isinstance(name, str) and isinstance(raw_config, dict)
        }

    def upsert_http_server(
        self,
        *,
        name: str,
        url: str,
        enabled: bool = True,
    ) -> SlotFlowMcpServerConfig:
        validate_http_mcp_server(name=name, url=url)
        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        user_servers[name] = SlotFlowMcpServerConfig(
            name=name,
            enabled=enabled,
            config={
                "transport": "streamable_http",
                "url": url,
            },
        )
        self._write_user_servers(user_servers)
        return user_servers[name]

    def set_server_enabled(self, name: str, enabled: bool) -> SlotFlowMcpServerConfig:
        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        if name in user_servers:
            current = user_servers[name]
            next_server = SlotFlowMcpServerConfig(
                name=current.name,
                enabled=enabled,
                config=current.config,
            )
            user_servers[name] = next_server
            self._write_user_servers(user_servers)
            return next_server

        base_servers = {
            server.name: server
            for server in self.base_config.servers
        }
        if name not in base_servers:
            raise McpServerNotFoundError(name)

        data = self._read_data()
        raw_overrides = data.get("overrides", {})
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
        current_override = overrides.get(name, {})
        if not isinstance(current_override, dict):
            current_override = {}
        overrides[name] = {**current_override, "enabled": enabled}
        data["overrides"] = overrides
        self._write_data(data)
        return apply_server_override(base_servers[name], overrides[name])

    def delete_user_server(self, name: str) -> None:
        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        if name not in user_servers:
            if any(server.name == name for server in self.base_config.servers):
                raise ProtectedMcpServerError(name)
            raise McpServerNotFoundError(name)
        del user_servers[name]
        self._write_user_servers(user_servers)

    def _read_data(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"servers": {}, "overrides": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"servers": {}, "overrides": {}}
        return data if isinstance(data, dict) else {"servers": {}, "overrides": {}}

    def _write_user_servers(
        self,
        user_servers: dict[str, SlotFlowMcpServerConfig],
    ) -> None:
        data = self._read_data()
        data["servers"] = {
            name: server_to_mapping(server)
            for name, server in sorted(user_servers.items())
        }
        self._write_data(data)

    def _write_data(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def server_from_mapping(
    name: str,
    raw_config: dict[str, Any],
) -> SlotFlowMcpServerConfig | None:
    config = dict(raw_config)
    enabled = config.pop("enabled", True)
    if not isinstance(enabled, bool):
        return None
    return SlotFlowMcpServerConfig(
        name=name.strip(),
        enabled=enabled,
        config=config,
    )


def server_to_mapping(server: SlotFlowMcpServerConfig) -> dict[str, Any]:
    return {
        "enabled": server.enabled,
        **dict(server.config or {}),
    }


def apply_server_override(
    server: SlotFlowMcpServerConfig,
    override: dict[str, Any] | None,
) -> SlotFlowMcpServerConfig:
    if not override:
        return server
    enabled = override.get("enabled", server.enabled)
    return SlotFlowMcpServerConfig(
        name=server.name,
        enabled=enabled if isinstance(enabled, bool) else server.enabled,
        config=server.config,
    )


def validate_http_mcp_server(*, name: str, url: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise ValueError("name must use letters, numbers, dots, underscores, or hyphens")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
