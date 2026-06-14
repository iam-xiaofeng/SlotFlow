"""FastAPI routes for safe workspace views."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.harness.sandbox import WorkspacePathError
from app.uploads.storage import SlotFlowUploadStore
from app.workspace.models import WorkspaceEntryRecord, WorkspaceReadRecord


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


@router.get("/artifacts/read", response_model=WorkspaceReadRecord)
async def read_artifact(
    request: Request,
    path: str = Query(min_length=1),
) -> WorkspaceReadRecord:
    """Read one generated artifact under `workspace/artifacts`."""

    if path == "artifacts" or not path.startswith("artifacts/"):
        raise HTTPException(status_code=400, detail="artifact path must be under artifacts/")

    workspace = get_upload_store(request).workspace
    try:
        result = workspace.read_file(path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc

    return WorkspaceReadRecord.model_validate(result.model_dump())


def get_upload_store(request: Request) -> SlotFlowUploadStore:
    """Read upload store from app.state."""

    return request.app.state.upload_store
