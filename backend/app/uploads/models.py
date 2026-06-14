"""SlotFlow upload API models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.chat.models import utc_now


class UploadedFileRecord(BaseModel):
    """Metadata returned after a user file is stored in the SlotFlow workspace."""

    id: str
    filename: str
    original_filename: str | None = None
    content_type: str | None = None
    size_bytes: int
    workspace_path: str
    created_at: datetime = Field(default_factory=utc_now)
