"""Workspace API response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


WorkspaceEntryKind = Literal["file", "directory"]


class WorkspaceEntryRecord(BaseModel):
    """A safe workspace entry exposed to the frontend."""

    path: str
    kind: WorkspaceEntryKind
    size_bytes: int | None = None

