"""Workspace directory-listing route tests: drill-down + sandbox guard."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.models import MessageRecord
from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app
from app.uploads import SlotFlowUploadStore
from app.workspace.routes import _collect_thread_uploads


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


def test_read_allows_uploads_but_not_other_areas(tmp_path: Path) -> None:
    """Preview is opened up to user uploads, but other workspace areas stay private."""

    client, store = _client(tmp_path)
    _write(store, "uploads/run1/note.txt", "hello upload")
    _write(store, "skills/secret/SKILL.md", "private")

    ok = client.get("/api/workspace/artifacts/read", params={"path": "uploads/run1/note.txt"})
    assert ok.status_code == 200

    blocked = client.get(
        "/api/workspace/artifacts/read", params={"path": "skills/secret/SKILL.md"}
    )
    assert blocked.status_code == 400


def test_collect_thread_uploads_dedupes_and_filters_missing(tmp_path: Path) -> None:
    _, store = _client(tmp_path)
    _write(store, "uploads/run1/report.md", "hi")

    messages = [
        MessageRecord(
            id="m1",
            thread_id="t",
            role="user",
            content="x",
            metadata={
                "uploaded_files": [
                    {"workspace_path": "uploads/run1/report.md", "size_bytes": 2},
                    {"workspace_path": "uploads/run1/report.md", "size_bytes": 2},  # dup
                    {"workspace_path": "uploads/ghost/missing.txt", "size_bytes": 9},  # missing
                ]
            },
        )
    ]

    class _Repo:
        def list_messages(self, thread_id: str):
            return messages

    uploads = _collect_thread_uploads(_Repo(), store.workspace, "t")

    assert [item.path for item in uploads] == ["uploads/run1/report.md"]
    assert uploads[0].size_bytes == 2


def test_list_thread_workspaces_returns_list(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.get("/api/workspace/threads")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
