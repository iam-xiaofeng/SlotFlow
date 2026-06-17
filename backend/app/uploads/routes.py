"""FastAPI routes for SlotFlow file uploads."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.dependencies import get_upload_store
from app.uploads.models import UploadedFileRecord
from app.uploads.storage import (
    UploadFileTooLargeError,
    UploadNotFoundError,
)


router = APIRouter(prefix="/api/uploads", tags=["Uploads"])


@router.post("", response_model=UploadedFileRecord)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
) -> UploadedFileRecord:
    """Store one uploaded file under the SlotFlow workspace."""

    store = get_upload_store(request)
    data = await file.read(store.max_upload_bytes + 1)
    try:
        return store.save_upload(
            filename=file.filename or "upload.bin",
            content_type=file.content_type,
            data=data,
        )
    except UploadFileTooLargeError as exc:
        raise HTTPException(status_code=413, detail="upload too large") from exc


@router.get("/{file_id}", response_model=UploadedFileRecord)
async def get_uploaded_file(
    file_id: str,
    request: Request,
) -> UploadedFileRecord:
    """Return uploaded file metadata by file ID."""

    try:
        return get_upload_store(request).get_upload(file_id)
    except UploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="upload not found") from exc


@router.get("/{file_id}/raw")
async def raw_uploaded_file(
    file_id: str,
    request: Request,
) -> FileResponse:
    """Serve one uploaded file for chat thumbnails or previews."""

    store = get_upload_store(request)
    try:
        record = store.get_upload(file_id)
        target = store.get_upload_path(file_id)
    except UploadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="upload not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="upload not found")

    media_type = record.content_type or mimetypes.guess_type(record.filename)[0]
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": "inline"},
    )
