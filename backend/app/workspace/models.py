"""Workspace API response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class ThreadWorkspaceRecord(BaseModel):
    """Per-thread grouping for the unified workspace panel.

    `generated` = model-written artifacts under `artifacts/<thread_id>/`;
    `uploads` = files the user attached in this thread, virtually grouped from the
    thread's chat messages (no storage migration).
    """

    thread_id: str
    title: str
    generated: list[WorkspaceEntryRecord] = Field(default_factory=list)
    uploads: list[WorkspaceEntryRecord] = Field(default_factory=list)
