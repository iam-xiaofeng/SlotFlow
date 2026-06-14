"""Persistent user MCP configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.harness.mcp.config import SlotFlowMcpConfig, SlotFlowMcpServerConfig


REMOVED_DEFAULT_MCP_SERVER_NAMES = {"filesystem"}


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
            if not is_removed_default_mcp_server(server.name)
        }
        for server in user_servers:
            servers_by_name[server.name] = server

        servers = tuple(sort_mcp_servers(servers_by_name.values()))
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
            if is_removed_default_mcp_server(name):
                continue
            server = server_from_mapping(name, raw_config)
            if server is not None:
                servers.append(server)
        return sort_mcp_servers(servers)

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
            order=next_mcp_order(user_servers.values()),
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
                order=current.order,
                pinned=current.pinned,
            )
            user_servers[name] = next_server
            self._write_user_servers(user_servers)
            return next_server

        base_servers = {
            server.name: server
            for server in self.base_config.servers
            if not is_removed_default_mcp_server(server.name)
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

    def set_server_pinned(self, name: str, pinned: bool) -> SlotFlowMcpServerConfig:
        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        if name in user_servers:
            current = user_servers[name]
            next_server = SlotFlowMcpServerConfig(
                name=current.name,
                enabled=current.enabled,
                config=current.config,
                order=current.order,
                pinned=pinned,
            )
            user_servers[name] = next_server
            self._write_user_servers(user_servers)
            return next_server

        base_servers = {
            server.name: server
            for server in self.base_config.servers
            if not is_removed_default_mcp_server(server.name)
        }
        if name not in base_servers:
            raise McpServerNotFoundError(name)

        data = self._read_data()
        raw_overrides = data.get("overrides", {})
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
        current_override = overrides.get(name, {})
        if not isinstance(current_override, dict):
            current_override = {}
        overrides[name] = {**current_override, "pinned": pinned}
        data["overrides"] = overrides
        self._write_data(data)
        return apply_server_override(base_servers[name], overrides[name])

    def reorder_servers(self, ordered_names: list[str]) -> SlotFlowMcpConfig:
        deduped_names = list(dict.fromkeys(ordered_names))
        current_servers = {server.name: server for server in self.load_config().servers}
        unknown_names = [name for name in deduped_names if name not in current_servers]
        if unknown_names:
            raise McpServerNotFoundError(unknown_names[0])

        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        data = self._read_data()
        raw_overrides = data.get("overrides", {})
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}

        for index, name in enumerate(deduped_names):
            if name in user_servers:
                current = user_servers[name]
                user_servers[name] = SlotFlowMcpServerConfig(
                    name=current.name,
                    enabled=current.enabled,
                    config=current.config,
                    order=index,
                    pinned=current.pinned,
                )
                continue
            current_override = overrides.get(name, {})
            if not isinstance(current_override, dict):
                current_override = {}
            overrides[name] = {**current_override, "order": index}

        data["servers"] = {
            name: server_to_mapping(server)
            for name, server in sorted(user_servers.items(), key=lambda item: mcp_server_sort_key(item[1]))
            if not is_removed_default_mcp_server(name)
        }
        data["overrides"] = overrides
        self._write_data(data)
        return self.load_config()

    def delete_user_server(self, name: str) -> None:
        user_servers = {
            server.name: server
            for server in self.load_user_servers()
        }
        if name not in user_servers:
            if any(
                server.name == name and not is_removed_default_mcp_server(server.name)
                for server in self.base_config.servers
            ):
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
            for name, server in sorted(user_servers.items(), key=lambda item: mcp_server_sort_key(item[1]))
            if not is_removed_default_mcp_server(name)
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
    order = config.pop("order", 0)
    pinned = config.pop("pinned", False)
    if not isinstance(enabled, bool):
        return None
    return SlotFlowMcpServerConfig(
        name=name.strip(),
        enabled=enabled,
        config=config,
        order=order if isinstance(order, int) else 0,
        pinned=pinned if isinstance(pinned, bool) else False,
    )


def server_to_mapping(server: SlotFlowMcpServerConfig) -> dict[str, Any]:
    return {
        "enabled": server.enabled,
        "order": server.order,
        "pinned": server.pinned,
        **dict(server.config or {}),
    }


def apply_server_override(
    server: SlotFlowMcpServerConfig,
    override: dict[str, Any] | None,
) -> SlotFlowMcpServerConfig:
    if not override:
        return server
    enabled = override.get("enabled", server.enabled)
    order = override.get("order", server.order)
    pinned = override.get("pinned", server.pinned)
    return SlotFlowMcpServerConfig(
        name=server.name,
        enabled=enabled if isinstance(enabled, bool) else server.enabled,
        config=server.config,
        order=order if isinstance(order, int) else server.order,
        pinned=pinned if isinstance(pinned, bool) else server.pinned,
    )


def validate_http_mcp_server(*, name: str, url: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise ValueError("name must use letters, numbers, dots, underscores, or hyphens")
    if is_removed_default_mcp_server(name):
        raise ValueError("filesystem MCP server is reserved and no longer supported")
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")


def is_removed_default_mcp_server(name: str) -> bool:
    return name.strip().lower() in REMOVED_DEFAULT_MCP_SERVER_NAMES


def mcp_server_sort_key(server: SlotFlowMcpServerConfig) -> tuple[bool, int, str]:
    return (not server.pinned, server.order, server.name)


def sort_mcp_servers(servers) -> list[SlotFlowMcpServerConfig]:
    return [
        server
        for _, server in sorted(
            enumerate(servers),
            key=lambda item: (not item[1].pinned, item[1].order, item[0]),
        )
    ]


def next_mcp_order(servers) -> int:
    server_list = list(servers)
    if not server_list:
        return 0
    return max(server.order for server in server_list) + 1
