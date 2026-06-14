"""SlotFlow sandbox minimum configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_WORKSPACE_ROOT = Path(".slotflow/workspace")
DEFAULT_MAX_READ_BYTES = 1024 * 1024
DEFAULT_MAX_WRITE_BYTES = 1024 * 1024
DEFAULT_MAX_FETCH_BYTES = 512 * 1024
DEFAULT_NETWORK_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class SlotFlowSandboxConfig:
    """Safety limits for the local SlotFlow workspace boundary."""

    workspace_root: Path | None = None
    writes_enabled: bool = False
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    network_enabled: bool = True
    allow_private_network: bool = False
    max_fetch_bytes: int = DEFAULT_MAX_FETCH_BYTES
    network_timeout_seconds: int = DEFAULT_NETWORK_TIMEOUT_SECONDS

    def resolved_workspace_root(self) -> Path:
        """Return the absolute workspace root without creating it."""

        root = self.workspace_root or DEFAULT_WORKSPACE_ROOT
        return root.expanduser().resolve(strict=False)
