"""FastAPI routes for safe workspace views."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.harness.sandbox import WorkspacePathError
from app.uploads.storage import SlotFlowUploadStore
from app.workspace.models import WorkspaceEntryRecord


router = APIRouter(prefix="/api/workspace", tags=["Workspace"])


@router.get("/artifacts", response_model=list[WorkspaceEntryRecord])
async def list_artifacts(request: Request) -> list[WorkspaceEntryRecord]:
    """List generated artifacts under `workspace/artifacts`."""

    workspace = get_upload_store(request).workspace
    try:
        entries = workspace.list_entries("artifacts")
    except WorkspacePathError:
        return []

    return [
        WorkspaceEntryRecord(
            path=entry.path,
            kind=entry.kind,
            size_bytes=entry.size_bytes,
        )
        for entry in entries
    ]


def get_upload_store(request: Request) -> SlotFlowUploadStore:
    """Read upload store from app.state."""

    return request.app.state.upload_store

