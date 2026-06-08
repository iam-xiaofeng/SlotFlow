"""Module 22 tests: SlotFlow file upload API."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app
from app.uploads import SlotFlowUploadStore


def _client(tmp_path: Path, *, max_write_bytes: int = 1024) -> tuple[TestClient, SlotFlowUploadStore]:
    store = SlotFlowUploadStore(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            max_write_bytes=max_write_bytes,
        )
    )
    return TestClient(create_app(upload_store=store)), store


def test_upload_file_stores_bytes_and_metadata_under_workspace(tmp_path: Path) -> None:
    client, store = _client(tmp_path)

    response = client.post(
        "/api/uploads",
        files={"file": ("notes/hello world.txt", b"hello", "text/plain")},
    )
    body = response.json()

    assert response.status_code == 200
    assert re.fullmatch(r"file_[0-9a-f]{12}", body["id"])
    assert body["filename"] == "hello_world.txt"
    assert body["content_type"] == "text/plain"
    assert body["size_bytes"] == 5
    assert body["workspace_path"] == f"uploads/{body['id']}/hello_world.txt"

    stored_path = store.workspace.resolve_path(body["workspace_path"])
    metadata_path = store.workspace.resolve_path(f"uploads/{body['id']}/metadata.json")

    assert stored_path.read_bytes() == b"hello"
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["id"] == body["id"]


def test_get_uploaded_file_returns_metadata(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("report.md", b"# report", "text/markdown")},
    ).json()

    response = client.get(f"/api/uploads/{uploaded['id']}")

    assert response.status_code == 200
    assert response.json()["workspace_path"] == uploaded["workspace_path"]


def test_upload_rejects_oversized_file(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, max_write_bytes=4)

    response = client.post(
        "/api/uploads",
        files={"file": ("too-large.txt", b"abcde", "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "upload too large"


def test_get_uploaded_file_returns_404_for_unknown_id(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/api/uploads/file_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "upload not found"
