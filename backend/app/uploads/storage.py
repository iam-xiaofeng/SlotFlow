"""Store user uploads under the SlotFlow workspace."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.chat.ids import new_file_id
from app.harness.sandbox import (
    SlotFlowSandboxConfig,
    WorkspaceFileTooLargeError,
    build_slotflow_workspace,
)
from app.uploads.models import UploadedFileRecord


class UploadNotFoundError(FileNotFoundError):
    """Raised when an uploaded file ID has no metadata."""


class UploadFileTooLargeError(WorkspaceFileTooLargeError):
    """Raised when an upload exceeds configured size limits."""


class SlotFlowUploadStore:
    """Persist uploaded files inside `workspace/uploads`."""

    def __init__(self, config: SlotFlowSandboxConfig | None = None) -> None:
        self.workspace = build_slotflow_workspace(config)

    @property
    def max_upload_bytes(self) -> int:
        """Reuse the workspace write limit for explicit user uploads."""

        return self.workspace.config.max_write_bytes

    def save_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> UploadedFileRecord:
        """Write uploaded bytes and metadata under a new file ID."""

        if len(data) > self.max_upload_bytes:
            raise UploadFileTooLargeError(
                "upload exceeds max_upload_bytes: "
                f"{len(data)} > {self.max_upload_bytes}",
            )

        file_id = new_file_id()
        original_filename = normalize_upload_display_filename(filename)
        safe_filename = sanitize_upload_filename(filename)
        workspace_path = f"uploads/{file_id}/{safe_filename}"
        target = self.workspace.resolve_path(workspace_path)
        metadata_target = self._metadata_path(file_id)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        record = UploadedFileRecord(
            id=file_id,
            filename=safe_filename,
            original_filename=original_filename,
            content_type=content_type,
            size_bytes=len(data),
            workspace_path=workspace_path,
        )
        metadata_target.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return record

    def get_upload(self, file_id: str) -> UploadedFileRecord:
        """Read uploaded file metadata by ID."""

        metadata_target = self._metadata_path(file_id)
        if not metadata_target.is_file():
            raise UploadNotFoundError(f"upload not found: {file_id}")
        return UploadedFileRecord.model_validate_json(
            metadata_target.read_text(encoding="utf-8")
        )

    def get_upload_path(self, file_id: str) -> Path:
        """Resolve the stored file path for an upload ID."""

        record = self.get_upload(file_id)
        return self.workspace.resolve_path(record.workspace_path)

    def stage_upload_for_run(self, file_id: str, *, run_id: str) -> UploadedFileRecord:
        """Copy an uploaded file into a run-scoped workspace upload path."""

        validate_run_id(run_id)
        record = self.get_upload(file_id)
        if record.workspace_path.startswith(f"uploads/{run_id}/"):
            return record

        source = self.workspace.resolve_path(record.workspace_path)
        if not source.is_file():
            raise UploadNotFoundError(f"upload not found: {file_id}")

        run_workspace_path = self._next_run_upload_path(run_id, record.filename)
        target = self.workspace.resolve_path(run_workspace_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

        staged_record = record.model_copy(
            update={
                "workspace_path": run_workspace_path,
                "size_bytes": target.stat().st_size,
            }
        )
        self._write_metadata(staged_record)
        return staged_record

    def _metadata_path(self, file_id: str) -> Path:
        validate_upload_id(file_id)
        target = self.workspace.resolve_path(f"uploads/{file_id}/metadata.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _write_metadata(self, record: UploadedFileRecord) -> None:
        self._metadata_path(record.id).write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _next_run_upload_path(self, run_id: str, filename: str) -> str:
        safe_filename = sanitize_upload_filename(filename)
        stem, dot, suffix = safe_filename.rpartition(".")
        name_stem = stem if dot else safe_filename
        name_suffix = f".{suffix}" if dot else ""

        for index in range(1, 1000):
            candidate_name = (
                safe_filename
                if index == 1
                else f"{name_stem}-{index}{name_suffix}"
            )
            candidate = f"uploads/{run_id}/{candidate_name}"
            if not self.workspace.resolve_path(candidate).exists():
                return candidate

        raise UploadFileTooLargeError("too many uploads with the same filename")


def sanitize_upload_filename(filename: str | None) -> str:
    """Make user-supplied filenames safe for workspace storage."""

    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not raw:
        raw = "upload.bin"

    raw_stem, dot, raw_suffix = raw.rpartition(".")
    if dot and raw_stem and re.fullmatch(r"[A-Za-z0-9]{1,16}", raw_suffix):
        suffix = raw_suffix.lower()
        stem = sanitize_filename_stem(raw_stem) or "upload"
        sanitized = f"{stem}.{suffix}"
    else:
        sanitized = sanitize_filename_stem(raw) or "upload.bin"

    if len(sanitized) <= 128:
        return sanitized

    stem, dot, suffix = sanitized.rpartition(".")
    if dot and suffix:
        return f"{stem[: 127 - len(suffix)]}.{suffix}"[:128]
    return sanitized[:128]


def sanitize_filename_stem(value: str) -> str:
    """Sanitize the filename stem without accidentally deleting the suffix."""

    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")


def normalize_upload_display_filename(filename: str | None) -> str:
    """Keep a user-facing filename without allowing path separators/control bytes."""

    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    raw = raw.replace("\x00", "")
    raw = re.sub(r"[\r\n\t]+", " ", raw).strip()
    if not raw:
        return "upload.bin"
    if len(raw) <= 255:
        return raw
    stem, dot, suffix = raw.rpartition(".")
    if dot and suffix:
        return f"{stem[: 254 - len(suffix)]}.{suffix}"[:255]
    return raw[:255]


def validate_upload_id(file_id: str) -> None:
    """Accept only IDs generated by `new_file_id()`."""

    if not re.fullmatch(r"file_[0-9a-f]{12}", file_id):
        raise UploadNotFoundError(f"upload not found: {file_id}")


def validate_run_id(run_id: str) -> None:
    """Accept only IDs generated by `new_run_id()`."""

    if not re.fullmatch(r"run_[0-9a-f]{12}", run_id):
        raise UploadNotFoundError(f"upload not found for run: {run_id}")
