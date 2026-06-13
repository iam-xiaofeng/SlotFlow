"""Workspace path boundary for future SlotFlow file tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.harness.sandbox.config import SlotFlowSandboxConfig
from app.harness.sandbox.readers import WorkspaceReadResult, read_workspace_file


class WorkspacePathError(ValueError):
    """Raised when a requested workspace path is outside the safe boundary."""


class WorkspaceWriteDisabledError(PermissionError):
    """Raised when code tries to write while workspace writes are disabled."""


class WorkspaceFileTooLargeError(ValueError):
    """Raised when a file operation exceeds configured byte limits."""


WorkspaceEntryKind = Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """A compact directory entry for future workspace list tools."""

    path: str
    kind: WorkspaceEntryKind
    size_bytes: int | None = None


class SlotFlowWorkspace:
    """Resolve and guard paths inside a single SlotFlow workspace root."""

    def __init__(self, config: SlotFlowSandboxConfig | None = None) -> None:
        self.config = config or SlotFlowSandboxConfig()
        self.root = self.config.resolved_workspace_root()

    def resolve_path(self, relative_path: str | Path = ".") -> Path:
        """Resolve a user supplied workspace path and keep it under `root`."""

        clean_path = validate_relative_workspace_path(relative_path)
        candidate = (self.root / clean_path).resolve(strict=False)
        if not is_relative_to(candidate, self.root):
            raise WorkspacePathError(
                f"workspace path escapes root: {relative_path!r}",
            )
        return candidate

    def list_entries(self, relative_path: str | Path = ".") -> list[WorkspaceEntry]:
        """List immediate children under a workspace directory."""

        target = self.resolve_path(relative_path)
        if not target.exists():
            raise WorkspacePathError(f"workspace directory does not exist: {relative_path!r}")
        if not target.is_dir():
            raise WorkspacePathError(f"workspace path is not a directory: {relative_path!r}")

        entries: list[WorkspaceEntry] = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            relative_child = child.relative_to(self.root).as_posix()
            if child.is_dir():
                entries.append(WorkspaceEntry(path=relative_child, kind="directory"))
            elif child.is_file():
                entries.append(
                    WorkspaceEntry(
                        path=relative_child,
                        kind="file",
                        size_bytes=child.stat().st_size,
                    )
                )
        return entries

    def read_text(self, relative_path: str | Path, *, encoding: str = "utf-8") -> str:
        """Read a text file after enforcing path and byte limits."""

        target = self.resolve_path(relative_path)
        self._assert_readable_file(target, relative_path)
        return target.read_text(encoding=encoding)

    def read_file(self, relative_path: str | Path) -> WorkspaceReadResult:
        """Read a workspace file into a model-readable structured payload."""

        target = self.resolve_path(relative_path)
        self._assert_readable_file(target, relative_path)
        return read_workspace_file(
            target,
            relative_path=target.relative_to(self.root).as_posix(),
        )

    def write_text(
        self,
        relative_path: str | Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> Path:
        """Write a text file only when workspace writes are explicitly enabled."""

        if not self.config.writes_enabled:
            raise WorkspaceWriteDisabledError("workspace writes are disabled")

        encoded = content.encode(encoding)
        if len(encoded) > self.config.max_write_bytes:
            raise WorkspaceFileTooLargeError(
                "workspace write exceeds max_write_bytes: "
                f"{len(encoded)} > {self.config.max_write_bytes}",
            )

        target = self.resolve_path(relative_path)
        if target.exists() and target.is_dir():
            raise WorkspacePathError(f"workspace path is a directory: {relative_path!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return target

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        """Write bytes only when workspace writes are explicitly enabled."""

        if not self.config.writes_enabled:
            raise WorkspaceWriteDisabledError("workspace writes are disabled")

        if len(data) > self.config.max_write_bytes:
            raise WorkspaceFileTooLargeError(
                "workspace write exceeds max_write_bytes: "
                f"{len(data)} > {self.config.max_write_bytes}",
            )

        target = self.resolve_path(relative_path)
        if target.exists() and target.is_dir():
            raise WorkspacePathError(f"workspace path is a directory: {relative_path!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _assert_readable_file(self, target: Path, relative_path: str | Path) -> None:
        if not target.is_file():
            raise WorkspacePathError(f"workspace path is not a file: {relative_path!r}")

        size = target.stat().st_size
        if size > self.config.max_read_bytes:
            raise WorkspaceFileTooLargeError(
                f"workspace read exceeds max_read_bytes: {size} > {self.config.max_read_bytes}",
            )


def validate_relative_workspace_path(relative_path: str | Path) -> Path:
    """Convert a user path into a safe relative `Path`."""

    raw = str(relative_path)
    if "\x00" in raw:
        raise WorkspacePathError("workspace path contains a null byte")

    stripped = raw.strip()
    if not stripped:
        raise WorkspacePathError("workspace path is empty")
    if "\\" in stripped:
        raise WorkspacePathError("workspace path must use forward slashes")
    if ":" in stripped:
        raise WorkspacePathError("workspace path must not contain drive prefixes")

    posix_path = PurePosixPath(stripped)
    if posix_path.is_absolute():
        raise WorkspacePathError("workspace path must be relative")

    safe_parts: list[str] = []
    for part in posix_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise WorkspacePathError("workspace path must not contain '..'")
        safe_parts.append(part)

    return Path(*safe_parts) if safe_parts else Path(".")


def is_relative_to(path: Path, root: Path) -> bool:
    """Return whether `path` stays within `root`."""

    return path == root or path.is_relative_to(root)


def build_slotflow_workspace(
    config: SlotFlowSandboxConfig | None = None,
) -> SlotFlowWorkspace:
    """Create a workspace boundary object from explicit sandbox config."""

    return SlotFlowWorkspace(config=config)
