"""FastAPI routes for safe workspace views."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.dependencies import get_upload_store
from app.harness.sandbox import WorkspacePathError
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


@router.get("/artifacts/raw")
async def raw_artifact(
    request: Request,
    path: str = Query(min_length=1),
    download: bool = False,
) -> FileResponse:
    """Serve one generated artifact for browser preview."""

    if path == "artifacts" or not path.startswith("artifacts/"):
        raise HTTPException(status_code=400, detail="artifact path must be under artifacts/")

    workspace = get_upload_store(request).workspace
    try:
        target = workspace.resolve_path(path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if download:
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="attachment",
        )

    return FileResponse(
        target,
        media_type=media_type,
        headers={"Content-Disposition": "inline"},
    )


@router.delete("/artifacts", status_code=204)
async def delete_artifact(
    request: Request,
    path: str = Query(min_length=1),
) -> Response:
    """Delete one generated artifact file."""

    if path == "artifacts" or not path.startswith("artifacts/"):
        raise HTTPException(status_code=400, detail="artifact path must be under artifacts/")

    workspace = get_upload_store(request).workspace
    try:
        target = workspace.resolve_path(path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")

    target.unlink()
    return Response(status_code=204)
