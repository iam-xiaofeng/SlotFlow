"""FastAPI routes for SlotFlow file uploads."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.uploads.models import UploadedFileRecord
from app.uploads.storage import (
    SlotFlowUploadStore,
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


def get_upload_store(request: Request) -> SlotFlowUploadStore:
    """Read upload store from app.state."""

    return request.app.state.upload_store
