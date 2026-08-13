"""FastAPI routes for safe workspace views."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.dependencies import get_chat_repo, get_upload_store
from app.harness.sandbox import WorkspaceFileTooLargeError, WorkspacePathError
from app.harness.sandbox.layout import (
    LEGACY_ARTIFACTS_DIR,
    is_artifact_path,
    thread_artifacts_dir,
    viewable_kind,
)
from app.harness.sandbox.workspace import SlotFlowWorkspace
from app.workspace.models import (
    ThreadWorkspaceRecord,
    WorkspaceEntryRecord,
    WorkspaceReadRecord,
)


router = APIRouter(prefix="/api/workspace", tags=["Workspace"])


def _is_viewable_path(path: str) -> bool:
    """Read/preview is allowed only for generated artifacts and user uploads; other
    workspace areas (scratch `work/`, upload originals, skills) stay private. The
    sandbox is still enforced by `resolve_path`."""

    return viewable_kind(path) is not None


@router.get("/artifacts", response_model=list[WorkspaceEntryRecord])
async def list_artifacts(
    request: Request,
    path: str = Query(""),
) -> list[WorkspaceEntryRecord]:
    """List artifacts.

    不带 `path` 时返回**聚合视图**:所有对话的 `<thread>/artifacts/` 递归展开,加上旧布局
    遗留在 `artifacts/` 下的文件。带 `path` 时是目录浏览器的下钻,只能落在产物区
    (工作区越界另由 `list_entries`/`resolve_path` 兜住)。
    """

    workspace = get_upload_store(request).workspace
    if not path:
        return await run_in_threadpool(_list_all_artifacts, workspace)

    if not is_artifact_path(path):
        raise HTTPException(status_code=400, detail="path must be an artifact directory")

    try:
        entries = await run_in_threadpool(workspace.list_entries, path)
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

    if not _is_viewable_path(path):
        raise HTTPException(status_code=400, detail="path must be a file under an artifacts/ or uploads/ folder")

    workspace = get_upload_store(request).workspace
    try:
        result = await run_in_threadpool(workspace.read_file, path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except WorkspaceFileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    return WorkspaceReadRecord.model_validate(result.model_dump())


@router.get("/artifacts/raw")
async def raw_artifact(
    request: Request,
    path: str = Query(min_length=1),
    download: bool = False,
) -> FileResponse:
    """Serve one generated artifact or user upload for browser preview."""

    if not _is_viewable_path(path):
        raise HTTPException(status_code=400, detail="path must be a file under an artifacts/ or uploads/ folder")

    workspace = get_upload_store(request).workspace
    try:
        target = await run_in_threadpool(workspace.resolve_path, path)
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

    if not is_artifact_path(path):
        raise HTTPException(status_code=400, detail="artifact path must be under an artifacts/ folder")

    workspace = get_upload_store(request).workspace
    try:
        target = await run_in_threadpool(workspace.resolve_path, path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")

    await run_in_threadpool(target.unlink)
    return Response(status_code=204)


@router.get("/threads", response_model=list[ThreadWorkspaceRecord])
async def list_thread_workspaces(request: Request) -> list[ThreadWorkspaceRecord]:
    """Per-thread view for the unified workspace panel: model-generated artifacts plus
    user uploads, labeled by thread title. Uploads are virtually grouped from each
    thread's chat messages (no storage migration)."""

    repo = get_chat_repo(request)
    workspace = get_upload_store(request).workspace

    records: list[ThreadWorkspaceRecord] = []
    threads = repo.list_threads()
    legacy_owned_prefixes = {f"{LEGACY_ARTIFACTS_DIR}/{thread.id}/" for thread in threads}
    for thread in threads:
        generated = _list_workspace_files(workspace, thread_artifacts_dir(thread.id))
        # 旧布局 artifacts/<thread_id>/ 里的存量文件继续展示,免得迁移前后前端"少东西"。
        generated += _list_workspace_files(
            workspace, f"{LEGACY_ARTIFACTS_DIR}/{thread.id}"
        )
        uploads = _collect_thread_uploads(repo, workspace, thread.id)
        if not generated and not uploads:
            continue
        records.append(
            ThreadWorkspaceRecord(
                thread_id=thread.id,
                title=thread.title,
                generated=generated,
                uploads=uploads,
            )
        )

    # Legacy/flat artifacts written directly under artifacts/ (not namespaced to a thread)
    # must still be findable — surface them as an "未归类产物" group.
    flat = [
        entry
        for entry in _list_workspace_files(workspace, LEGACY_ARTIFACTS_DIR)
        if not any(entry.path.startswith(prefix) for prefix in legacy_owned_prefixes)
    ]
    if flat:
        records.append(
            ThreadWorkspaceRecord(
                thread_id="__legacy_artifacts__",
                title="未归类产物",
                generated=flat,
                uploads=[],
            )
        )

    return records


def _list_all_artifacts(workspace: SlotFlowWorkspace) -> list[WorkspaceEntryRecord]:
    """聚合所有对话的产物文件:``<thread>/artifacts/**`` 加上旧布局的 ``artifacts/**``。

    以点开头的目录是 SlotFlow 自己的存储(上传原件、卸载文件、浏览器状态),跳过;
    ``work/`` 是沙箱 scratch,不在产物区,``_list_workspace_files`` 只被指向 artifacts 子目录。
    """

    entries: list[WorkspaceEntryRecord] = []
    seen: set[str] = set()
    try:
        children = sorted(workspace.root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []

    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == LEGACY_ARTIFACTS_DIR:
            continue
        for entry in _list_workspace_files(workspace, f"{child.name}/artifacts"):
            if entry.path not in seen:
                seen.add(entry.path)
                entries.append(entry)

    for entry in _list_workspace_files(workspace, LEGACY_ARTIFACTS_DIR):
        if entry.path not in seen:
            seen.add(entry.path)
            entries.append(entry)
    return entries


def _list_workspace_files(
    workspace: SlotFlowWorkspace,
    path: str,
) -> list[WorkspaceEntryRecord]:
    """Return all files under a workspace path, recursively and sandboxed."""

    try:
        root = workspace.resolve_path(path)
    except WorkspacePathError:
        return []
    if not root.exists():
        return []
    if root.is_file():
        return [
            WorkspaceEntryRecord(
                path=root.relative_to(workspace.root).as_posix(),
                kind="file",
                size_bytes=root.stat().st_size,
            )
        ]

    entries: list[WorkspaceEntryRecord] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not candidate.is_file():
            continue
        try:
            resolved = workspace.resolve_path(candidate.relative_to(workspace.root).as_posix())
        except WorkspacePathError:
            continue
        entries.append(
            WorkspaceEntryRecord(
                path=resolved.relative_to(workspace.root).as_posix(),
                kind="file",
                size_bytes=resolved.stat().st_size,
            )
        )
    return entries


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
