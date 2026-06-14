"""API models for long-term memory management."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.harness.memory import MemoryKind


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    kind: MemoryKind = "manual"
    thread_id: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    kind: MemoryKind | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
