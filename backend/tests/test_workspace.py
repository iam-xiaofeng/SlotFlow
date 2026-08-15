"""Workspace directory-listing route tests: drill-down + sandbox guard."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.models import MessageRecord
from app.chat.repository import SQLiteChatRepository
from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app
from app.uploads import SlotFlowUploadStore
import app.workspace.routes as workspace_routes
from app.workspace.routes import _collect_thread_uploads


class NoopAgentAdapter:
    """Workspace route tests never stream a run, but create_app needs an adapter."""


def _client(tmp_path: Path) -> tuple[TestClient, SlotFlowUploadStore]:
    store = SlotFlowUploadStore(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )
    return (
        TestClient(
            create_app(
                chat_repo=SQLiteChatRepository(":memory:"),
                agent_adapter=NoopAgentAdapter(),
                upload_store=store,
            )
        ),
        store,
    )


def _write(store: SlotFlowUploadStore, relative_path: str, text: str = "x") -> None:
    target = store.workspace.resolve_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_list_artifacts_root_aggregates_every_conversation(tmp_path: Path) -> None:
    """不带 path 时返回聚合视图:各对话的 <thread>/artifacts/**,加旧布局遗留。"""

    client, store = _client(tmp_path)
    _write(store, "thread_a/artifacts/a.md")
    _write(store, "thread_b/artifacts/nested/b.md")
    _write(store, "thread_a/work/scratch.py")  # scratch 不是产物
    _write(store, ".uploads/file_x/orig.txt")  # 上传原件不对外
    _write(store, "artifacts/threadA/a.md")  # 旧布局存量
    _write(store, "artifacts/top.txt")

    response = client.get("/api/workspace/artifacts")

    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()}
    assert paths == {
        "thread_a/artifacts/a.md",
        "thread_b/artifacts/nested/b.md",
        "artifacts/threadA/a.md",
        "artifacts/top.txt",
    }


def test_list_artifacts_drills_into_thread_folder(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _write(store, "thread_a/artifacts/nested/deep.md", "hello")

    response = client.get(
        "/api/workspace/artifacts", params={"path": "thread_a/artifacts"}
    )

    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()}
    assert "thread_a/artifacts/nested" in paths


def test_list_artifacts_rejects_scratch_and_upload_originals(tmp_path: Path) -> None:
    """产物浏览器不能被用来翻 work/ scratch 或上传原件。"""

    client, store = _client(tmp_path)
    _write(store, "thread_a/work/scratch.py")
    _write(store, ".uploads/file_x/orig.txt")

    for path in ("thread_a/work", ".uploads", ".uploads/file_x", "uploads"):
        response = client.get("/api/workspace/artifacts", params={"path": path})
        assert response.status_code == 400, path


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


def test_read_artifact_uses_threadpool_for_structured_preview(monkeypatch, tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _write(store, "artifacts/report.md", "# report")
    calls: list[str] = []

    async def spy_run_in_threadpool(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(workspace_routes, "run_in_threadpool", spy_run_in_threadpool)

    response = client.get(
        "/api/workspace/artifacts/read",
        params={"path": "artifacts/report.md"},
    )

    assert response.status_code == 200
    assert "read_file" in calls


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


def test_list_thread_workspaces_recursively_groups_thread_artifacts(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    thread = client.post("/api/chat/threads", json={"title": "旅行计划"}).json()
    _write(store, f"artifacts/{thread['id']}/plans/final.md", "plan")

    response = client.get("/api/workspace/threads")

    assert response.status_code == 200
    record = next(item for item in response.json() if item["thread_id"] == thread["id"])
    assert record["title"] == "旅行计划"
    assert record["generated"] == [
        {
            "path": f"artifacts/{thread['id']}/plans/final.md",
            "kind": "file",
            "size_bytes": 4,
        }
    ]


def test_list_thread_workspaces_keeps_legacy_artifacts_visible(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    thread = client.post("/api/chat/threads", json={"title": "新会话"}).json()
    _write(store, f"artifacts/{thread['id']}/report.md", "new")
    _write(store, "artifacts/old/report.md", "old")

    response = client.get("/api/workspace/threads")

    assert response.status_code == 200
    legacy = next(item for item in response.json() if item["thread_id"] == "__legacy_artifacts__")
    assert legacy["title"] == "未归类产物"
    assert legacy["generated"] == [
        {
            "path": "artifacts/old/report.md",
            "kind": "file",
            "size_bytes": 3,
        }
    ]


def test_raw_artifact_path_style_url_lets_relative_assets_resolve(tmp_path: Path) -> None:
    """带相对引用的 HTML 产物必须能预览。

    2026-08-15 真机:产物面板注入 `<base href="<raw url>">`,而 `?path=` 形式的 URL 做相对
    解析时**会丢掉 query string** —— `./assets/x.js` 解析成
    `/api/workspace/artifacts/assets/x.js`,404,整页白屏,一个 Vite 构建产物因此永远
    预览不了。所以 raw 必须另有路径式入口。
    """

    from urllib.parse import urljoin

    client, store = _client(tmp_path)
    _write(
        store,
        "thread_abc/artifacts/app/dist/index.html",
        '<script type="module" crossorigin src="./assets/main.js"></script>',
    )
    _write(store, "thread_abc/artifacts/app/dist/assets/main.js", "console.log(1)")

    base = "/api/workspace/artifacts/raw/thread_abc/artifacts/app/dist/index.html"
    assert client.get(base).status_code == 200

    # 关键断言:浏览器就是这样解析 <base href> 下相对引用的。
    resolved = urljoin(f"http://testserver{base}", "./assets/main.js")
    asset = client.get(resolved.removeprefix("http://testserver"))
    assert asset.status_code == 200
    assert asset.text == "console.log(1)"
    # iframe 是不透明源(sandbox 无 allow-same-origin),module script 强制走 CORS。
    assert asset.headers["access-control-allow-origin"] == "*"

    # 老的 ?path= 形式不能断:已有链接还在用。
    legacy = client.get(
        "/api/workspace/artifacts/raw",
        params={"path": "thread_abc/artifacts/app/dist/index.html"},
    )
    assert legacy.status_code == 200

    # 路径式入口不能成为绕过读权限的后门。
    assert client.get("/api/workspace/artifacts/raw/thread_abc/work/scratch.py").status_code == 400
    assert client.get("/api/workspace/artifacts/raw/.uploads/file_x/orig.txt").status_code == 400


def test_artifact_listing_hides_dependency_directories(tmp_path: Path) -> None:
    """node_modules 不该出现在产物面板里。

    真机上一次 React 重构在 artifacts/ 下装出 2660 个依赖文件,列表接口返回 2814 条、
    394 KB JSON,真正的交付物被彻底淹没。
    """

    client, store = _client(tmp_path)
    _write(store, "thread_abc/artifacts/node_modules/react/index.js")
    _write(store, "thread_abc/artifacts/dist/assets/app.js")  # 构建产物本身要留着
    _write(store, "thread_abc/artifacts/report.md", "# 交付物")

    paths = [item["path"] for item in client.get("/api/workspace/artifacts").json()]
    assert paths == [
        "thread_abc/artifacts/dist/assets/app.js",
        "thread_abc/artifacts/report.md",
    ]
