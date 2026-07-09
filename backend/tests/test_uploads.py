"""Module 22 tests: SlotFlow file upload API."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import app.uploads.routes as upload_routes
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools.workspace import build_workspace_tools
from app.main import create_app
from app.uploads import SlotFlowUploadStore
from app.uploads.storage import normalize_upload_display_filename, sanitize_upload_filename


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


def test_upload_file_persists_via_threadpool(monkeypatch, tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    calls: list[str] = []

    async def spy_run_in_threadpool(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(upload_routes, "run_in_threadpool", spy_run_in_threadpool)

    response = client.post(
        "/api/uploads",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert "save_upload" in calls


def test_sanitize_upload_filename_preserves_suffix_for_chinese_docx_name() -> None:
    filename = "一种俯视监控视角下无骨架依赖的小目标相似行人跟踪方法.docx"

    assert sanitize_upload_filename(filename) == "upload.docx"
    assert normalize_upload_display_filename(filename) == filename


def test_upload_response_keeps_original_filename_for_display(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    filename = "实时交通视频分析系统.pdf"

    response = client.post(
        "/api/uploads",
        files={"file": (filename, b"%PDF-1.4", "application/pdf")},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["filename"] == "upload.pdf"
    assert body["original_filename"] == filename


def test_get_uploaded_file_returns_metadata(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("report.md", b"# report", "text/markdown")},
    ).json()

    response = client.get(f"/api/uploads/{uploaded['id']}")

    assert response.status_code == 200
    assert response.json()["workspace_path"] == uploaded["workspace_path"]


def test_raw_uploaded_file_serves_bytes_inline(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    ).json()

    response = client.get(f"/api/uploads/{uploaded['id']}/raw")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == "inline"
    assert response.content == b"\x89PNG\r\n\x1a\nimage"


def test_workspace_read_can_read_uploaded_file_by_workspace_path(tmp_path: Path) -> None:
    """模块 23：上传文件返回的 workspace_path 仍然受 workspace 工具边界保护。"""

    client, store = _client(tmp_path)
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("notes.txt", b"hello from upload", "text/plain")},
    ).json()
    workspace_read = next(
        tool for tool in build_workspace_tools(store.workspace.config)
        if tool.name == "workspace_read"
    )

    payload = json.loads(workspace_read.invoke({"path": uploaded["workspace_path"]}))

    assert payload["path"] == uploaded["workspace_path"]
    assert payload["content"] == "hello from upload"
    assert payload["source"] == "slotflow_workspace"


def test_upload_store_stages_file_under_run_scoped_path(tmp_path: Path) -> None:
    """发送消息时上传文件会复制到 uploads/<run_id>/ 下供 agent 读取。"""

    client, store = _client(tmp_path)
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("report.md", b"# report", "text/markdown")},
    ).json()

    staged = store.stage_upload_for_run(uploaded["id"], run_id="run_abc123abc123")

    assert staged.workspace_path == "uploads/run_abc123abc123/report.md"
    assert store.get_upload(uploaded["id"]).workspace_path == staged.workspace_path
    assert store.workspace.resolve_path(staged.workspace_path).read_bytes() == b"# report"


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


def test_list_artifacts_returns_workspace_artifact_entries(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/summary.md")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Summary", encoding="utf-8")

    response = client.get("/api/workspace/artifacts")

    assert response.status_code == 200
    assert response.json() == [
        {
            "path": "artifacts/summary.md",
            "kind": "file",
            "size_bytes": 9,
        }
    ]


def test_read_artifact_returns_workspace_read_payload(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/summary.md")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Summary", encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "artifacts/summary.md"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "path": "artifacts/summary.md",
        "kind": "text",
        "media_type": "text/markdown",
        "size_bytes": 9,
        "source": "slotflow_workspace",
        "metadata": {"format": "md"},
        "content": "# Summary",
        "warning": None,
    }


def test_read_artifact_returns_html_as_text(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/report.html")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<!doctype html><title>Report</title>", encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "artifacts/report.html"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "path": "artifacts/report.html",
        "kind": "text",
        "media_type": "text/html",
        "size_bytes": 36,
        "source": "slotflow_workspace",
        "metadata": {"format": "html"},
        "content": "<!doctype html><title>Report</title>",
        "warning": None,
    }


def test_read_artifact_previews_large_docx_with_embedded_media(tmp_path: Path) -> None:
    """A docx can exceed the generic 1 MiB text limit because images live in the package."""

    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/thread1/report.docx")
    artifact_path.parent.mkdir(parents=True)
    create_docx_with_large_media(artifact_path, "Docx preview still works")

    assert artifact_path.stat().st_size > store.workspace.config.max_read_bytes

    response = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "artifacts/thread1/report.docx"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "document"
    assert body["metadata"] == {"format": "docx"}
    assert body["content"] == "Docx preview still works"


def test_read_artifact_returns_413_for_oversized_plain_text(tmp_path: Path) -> None:
    """Oversized plain text previews should be a client-visible 413, not a 500."""

    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/too-large.md")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("x" * (store.workspace.config.max_read_bytes + 1), encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "artifacts/too-large.md"},
    )

    assert response.status_code == 413
    assert "workspace read exceeds max_read_bytes" in response.json()["detail"]


def test_read_rejects_private_workspace_paths(tmp_path: Path) -> None:
    """Read/preview is limited to artifacts/ and uploads/; other areas stay private."""

    client, store = _client(tmp_path)
    secret = store.workspace.resolve_path("skills/secret/SKILL.md")
    secret.parent.mkdir(parents=True)
    secret.write_text("private", encoding="utf-8")

    blocked = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "skills/secret/SKILL.md"},
    )
    assert blocked.status_code == 400

    # User uploads ARE viewable (the unified workspace panel previews them).
    upload_path = store.workspace.resolve_path("uploads/file_abc123abc123/note.md")
    upload_path.parent.mkdir(parents=True)
    upload_path.write_text("# upload", encoding="utf-8")
    allowed = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "uploads/file_abc123abc123/note.md"},
    )
    assert allowed.status_code == 200


def test_raw_artifact_serves_html_inline(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/chart.html")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<button>zoom</button>", encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/raw",
        params={"path": "artifacts/chart.html"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == "inline"
    assert response.text == "<button>zoom</button>"


def create_docx_with_large_media(path: Path, text: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/media/image1.png", b"x" * (1024 * 1024 + 1))


def test_raw_artifact_can_be_downloaded_as_attachment(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/chart.html")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<button>zoom</button>", encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/raw",
        params={"path": "artifacts/chart.html", "download": "true"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="chart.html"'
    assert response.text == "<button>zoom</button>"


def test_raw_artifact_download_uses_encoded_non_ascii_filename(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/广州天气.html")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<button>zoom</button>", encoding="utf-8")

    response = client.get(
        "/api/workspace/artifacts/raw",
        params={"path": "artifacts/广州天气.html", "download": "true"},
    )

    assert response.status_code == 200
    content_disposition = response.headers["content-disposition"]
    assert content_disposition.startswith("attachment; filename*=utf-8''")
    assert "%E5%B9%BF%E5%B7%9E%E5%A4%A9%E6%B0%94.html" in content_disposition
    assert response.text == "<button>zoom</button>"


def test_delete_artifact_removes_file(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    artifact_path = store.workspace.resolve_path("artifacts/chart.html")
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("<button>zoom</button>", encoding="utf-8")

    response = client.delete(
        "/api/workspace/artifacts",
        params={"path": "artifacts/chart.html"},
    )

    assert response.status_code == 204
    assert not artifact_path.exists()
    assert client.get(
        "/api/workspace/artifacts/raw",
        params={"path": "artifacts/chart.html"},
    ).status_code == 404
