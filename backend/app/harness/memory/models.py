"""Long-term memory data models for SlotFlow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, Field

MemoryKind = Literal["manual", "preference", "profile", "topic", "fact"]
MEMORY_KINDS: set[str] = {"manual", "preference", "profile", "topic", "fact"}


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryRecord(BaseModel):
    """A persisted memory item available to future agent runs."""

    id: str
    thread_id: str | None = None
    kind: MemoryKind = "manual"
    content: str
    source_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
