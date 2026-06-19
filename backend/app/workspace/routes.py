"""FastAPI routes for safe workspace views."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.dependencies import get_chat_repo, get_upload_store
from app.harness.sandbox import WorkspacePathError
from app.workspace.models import (
    ThreadWorkspaceRecord,
    WorkspaceEntryRecord,
    WorkspaceReadRecord,
)


router = APIRouter(prefix="/api/workspace", tags=["Workspace"])


def _is_viewable_path(path: str) -> bool:
    """Read/preview is allowed only for generated artifacts and user uploads; other
    workspace areas (e.g. skills) stay private. The sandbox is still enforced by
    `resolve_path`."""

    return (
        path == "artifacts"
        or path.startswith("artifacts/")
        or path == "uploads"
        or path.startswith("uploads/")
    )


@router.get("/artifacts", response_model=list[WorkspaceEntryRecord])
async def list_artifacts(
    request: Request,
    path: str = Query("artifacts"),
) -> list[WorkspaceEntryRecord]:
    """List immediate children under `workspace/artifacts` or one of its subdirectories.

    `path` lets the directory browser drill down; it must stay under `artifacts/`
    (the workspace sandbox is additionally enforced by `list_entries`/`resolve_path`).
    """

    if path != "artifacts" and not path.startswith("artifacts/"):
        raise HTTPException(status_code=400, detail="path must be under artifacts/")

    workspace = get_upload_store(request).workspace
    try:
        entries = workspace.list_entries(path)
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
    """Read one generated artifact or user upload for preview."""

    if path in ("artifacts", "uploads") or not _is_viewable_path(path):
        raise HTTPException(status_code=400, detail="path must be a file under artifacts/ or uploads/")

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
    """Serve one generated artifact or user upload for browser preview."""

    if path in ("artifacts", "uploads") or not _is_viewable_path(path):
        raise HTTPException(status_code=400, detail="path must be a file under artifacts/ or uploads/")

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


@router.get("/threads", response_model=list[ThreadWorkspaceRecord])
async def list_thread_workspaces(request: Request) -> list[ThreadWorkspaceRecord]:
    """Per-thread view for the unified workspace panel: model-generated artifacts plus
    user uploads, labeled by thread title. Uploads are virtually grouped from each
    thread's chat messages (no storage migration)."""

    repo = get_chat_repo(request)
    workspace = get_upload_store(request).workspace

    records: list[ThreadWorkspaceRecord] = []
    for thread in repo.list_threads():
        try:
            generated = [
                WorkspaceEntryRecord(path=entry.path, kind=entry.kind, size_bytes=entry.size_bytes)
                for entry in workspace.list_entries(f"artifacts/{thread.id}")
            ]
        except WorkspacePathError:
            generated = []
        records.append(
            ThreadWorkspaceRecord(
                thread_id=thread.id,
                title=thread.title,
                generated=generated,
                uploads=_collect_thread_uploads(repo, workspace, thread.id),
            )
        )
    return records


def _collect_thread_uploads(repo, workspace, thread_id: str) -> list[WorkspaceEntryRecord]:
    """Gather a thread's user uploads from its message metadata (deduped, existing only)."""

    seen: set[str] = set()
    uploads: list[WorkspaceEntryRecord] = []
    for message in repo.list_messages(thread_id):
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        for item in metadata.get("uploaded_files", []) or []:
            if not isinstance(item, dict):
                continue
            path = item.get("workspace_path")
            if not isinstance(path, str) or path in seen:
                continue
            try:
                exists = workspace.resolve_path(path).is_file()
            except WorkspacePathError:
                exists = False
            if not exists:
                continue
            seen.add(path)
            size = item.get("size_bytes")
            uploads.append(
                WorkspaceEntryRecord(
                    path=path,
                    kind="file",
                    size_bytes=size if isinstance(size, int) else None,
                )
            )
    return uploads
