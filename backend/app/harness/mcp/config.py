"""SlotFlow MCP minimum configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SlotFlowMcpServerConfig:
    """A configured MCP server descriptor.

    Module 13 does not start external MCP server processes. It only keeps the
    server name and raw config available for a later real provider.
    """

    name: str
    enabled: bool = True
    config: dict[str, Any] | None = None
    order: int = 0
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class SlotFlowMcpConfig:
    """Whether this harness run should load MCP tools."""

    enabled: bool = False
    servers: tuple[SlotFlowMcpServerConfig, ...] = field(default_factory=tuple)

    def active_servers(self) -> tuple[SlotFlowMcpServerConfig, ...]:
        """Return only enabled servers, preserving the configured order."""

        return tuple(server for server in self.servers if server.enabled)
