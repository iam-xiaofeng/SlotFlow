"""Workspace API response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


WorkspaceEntryKind = Literal["file", "directory"]


class WorkspaceEntryRecord(BaseModel):
    """A safe workspace entry exposed to the frontend."""

    path: str
    kind: WorkspaceEntryKind
    size_bytes: int | None = None


class WorkspaceReadRecord(BaseModel):
    """A model-readable workspace file payload exposed to the frontend."""

    path: str
    kind: Literal["text", "document", "pdf", "image", "binary"]
    media_type: str
    size_bytes: int
    source: str
    metadata: dict[str, Any]
    content: str | None = None
    warning: str | None = None
