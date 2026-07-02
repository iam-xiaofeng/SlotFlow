"""SlotFlow sandbox minimum configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_WORKSPACE_ROOT = Path(".slotflow/workspace")
DEFAULT_MAX_READ_BYTES = 1024 * 1024
DEFAULT_MAX_WRITE_BYTES = 1024 * 1024
DEFAULT_MAX_FETCH_BYTES = 512 * 1024
DEFAULT_NETWORK_TIMEOUT_SECONDS = 15
DEFAULT_DOCKER_IMAGE = "python:3.12"
DEFAULT_DOCKER_TIMEOUT_SECONDS = 120
DEFAULT_DOCKER_NETWORK_ENABLED = True
DEFAULT_DOCKER_IDLE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True, slots=True)
class SlotFlowSandboxConfig:
    """Safety limits for the local SlotFlow workspace boundary."""

    workspace_root: Path | None = None
    # Enabled by default: the only model-facing writer is the sandboxed artifact_write
    # (path-validated, size-limited, confined to artifacts/<thread_id>/). Set the env
    # flag false to make the workspace fully read-only.
    writes_enabled: bool = True
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    network_enabled: bool = True
    allow_private_network: bool = False
    max_fetch_bytes: int = DEFAULT_MAX_FETCH_BYTES
    network_timeout_seconds: int = DEFAULT_NETWORK_TIMEOUT_SECONDS
    code_execution_enabled: bool = True
    docker_image: str = DEFAULT_DOCKER_IMAGE
    docker_timeout_seconds: int = DEFAULT_DOCKER_TIMEOUT_SECONDS
    docker_network_enabled: bool = DEFAULT_DOCKER_NETWORK_ENABLED
    docker_idle_timeout_seconds: int = DEFAULT_DOCKER_IDLE_TIMEOUT_SECONDS
    allow_host_docker_install: bool = True

    def resolved_workspace_root(self) -> Path:
        """Return the absolute workspace root without creating it."""

        root = self.workspace_root or DEFAULT_WORKSPACE_ROOT
        return root.expanduser().resolve(strict=False)
