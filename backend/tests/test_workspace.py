"""Workspace directory-listing route tests: drill-down + sandbox guard."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app
from app.uploads import SlotFlowUploadStore


def _client(tmp_path: Path) -> tuple[TestClient, SlotFlowUploadStore]:
    store = SlotFlowUploadStore(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )
    return TestClient(create_app(upload_store=store)), store


def _write(store: SlotFlowUploadStore, relative_path: str, text: str = "x") -> None:
    target = store.workspace.resolve_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_list_artifacts_root_returns_immediate_children(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _write(store, "artifacts/threadA/a.md")
    _write(store, "artifacts/top.txt")

    response = client.get("/api/workspace/artifacts")

    assert response.status_code == 200
    entries = {(entry["path"], entry["kind"]) for entry in response.json()}
    assert ("artifacts/threadA", "directory") in entries
    assert ("artifacts/top.txt", "file") in entries


def test_list_artifacts_drills_into_subdirectory(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _write(store, "artifacts/threadA/nested/deep.md", "hello")

    response = client.get(
        "/api/workspace/artifacts", params={"path": "artifacts/threadA"}
    )

    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()}
    assert "artifacts/threadA/nested" in paths


def test_list_artifacts_rejects_path_outside_artifacts(tmp_path: Path) -> None:
    """The directory browser must stay inside the artifacts sandbox."""

    client, _ = _client(tmp_path)

    response = client.get("/api/workspace/artifacts", params={"path": "uploads"})

    assert response.status_code == 400
