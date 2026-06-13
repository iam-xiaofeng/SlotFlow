"""聊天相关 FastAPI 路由。

模块六第一次把前面的模块串起来：

```txt
HTTP 请求
-> 仓库创建 run
-> build_run_config
-> AgentAdapter.stream_events
-> iter_business_events
-> encode_sse_event
-> StreamingResponse
```

这一层是“编排层”。它不自己生成回答，也不自己解析 LangGraph 投影；那些事情已经分别
交给模块四和模块五。路由只负责把一次 HTTP 请求变成一次可记录、可流式返回的 run。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.chat.agent_adapter import AgentAdapter
from app.chat.models import (
    ChatStreamRequest,
    MessageRecord,
    ThreadCreateRequest,
    ThreadRecord,
    UploadedFileContext,
)
from app.chat.repository import ChatRepository, ThreadNotFoundError
from app.chat.run_config import build_run_config
from app.chat.sse import BusinessSseEvent, encode_sse_event, iter_business_events
from app.uploads.models import UploadedFileRecord
from app.uploads.storage import SlotFlowUploadStore, UploadNotFoundError


router = APIRouter(prefix="/api/chat", tags=["Chat"])


@router.post("/threads", response_model=ThreadRecord)
async def create_thread(
    body: ThreadCreateRequest,
    request: Request,
) -> ThreadRecord:
    """创建一条空会话。

    前端第一次打开新聊天时可以先建 thread，再把用户消息发到这个 thread 的
    `runs/stream` 接口。
    """

    return get_repo(request).create_thread(title=body.title)


@router.get("/threads", response_model=list[ThreadRecord])
async def list_threads(request: Request) -> list[ThreadRecord]:
    """列出所有会话，最近活动的排在前面。"""

    return get_repo(request).list_threads()


@router.get("/threads/{thread_id}", response_model=ThreadRecord)
async def get_thread(thread_id: str, request: Request) -> ThreadRecord:
    """读取单条会话，不存在时返回 404。"""

    repo = get_repo(request)
    try:
        return repo.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.get("/threads/{thread_id}/messages", response_model=list[MessageRecord])
async def list_messages(thread_id: str, request: Request) -> list[MessageRecord]:
    """读取某个 thread 下已经保存的消息。"""

    repo = get_repo(request)
    try:
        return repo.list_messages(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.post("/threads/{thread_id}/runs/stream")
async def stream_thread_run(
    thread_id: str,
    body: ChatStreamRequest,
    request: Request,
) -> StreamingResponse:
    """启动一次 assistant run，并用 SSE 返回流式事件。

    这里是后端学习链路的核心入口。它做的事情按顺序是：

    1. 确认 thread 存在；
    2. 解析请求里的上传文件 ID；
    3. 保存用户消息；
    4. 创建 run；
    5. 构建 `config + context`；
    6. 调用 agent adapter；
    7. 把业务事件编码成 SSE；
    8. 根据流式结果更新 run 状态和 assistant 消息。
    """

    repo = get_repo(request)
    adapter = get_agent_adapter(request)

    try:
        repo.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc

    upload_store = get_upload_store(request)
    validate_uploaded_files_exist(body.files, store=upload_store)
    run = repo.create_run(
        thread_id,
        model_name=body.model_name,
        mode=body.mode,
        agent_name=body.agent_name,
    )
    uploaded_files = stage_uploaded_files(
        body.files,
        run_id=run.id,
        store=upload_store,
    )
    uploaded_file_metadata = [
        uploaded_file.model_dump(mode="json")
        for uploaded_file in uploaded_files
    ]

    repo.add_message(
        thread_id,
        role="user",
        content=body.message,
        metadata={
            "files": list(body.files),
            "uploaded_files": uploaded_file_metadata,
            "request_metadata": dict(body.metadata),
        },
    )
    bundle = build_run_config(
        thread_id=thread_id,
        run_id=run.id,
        request=body,
        uploaded_files=uploaded_files,
    )
    repo.update_run_status(run.id, status="running")

    async def frames() -> AsyncIterator[str]:
        assistant_text_parts: list[str] = []
        snapshot_message_content: str | None = None
        completed = False

        events = adapter.stream_events(request=body, bundle=bundle)
        async for event in iter_business_events(events):
            if event.event == "message.delta":
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    assistant_text_parts.append(delta)

            if event.event == "state.snapshot":
                snapshot_message_content = latest_assistant_content(event)

            if event.event == "run.error":
                repo.update_run_status(
                    run.id,
                    status="failed",
                    error=str(event.data.get("message", "agent stream failed")),
                )
                yield encode_sse_event(event)
                return

            if event.event == "run.finished":
                content = snapshot_message_content or "".join(assistant_text_parts)
                if content:
                    repo.add_message(
                        thread_id,
                        role="assistant",
                        content=content,
                        run_id=run.id,
                        metadata={"source": "agent"},
                    )
                repo.update_run_status(run.id, status="completed")
                completed = True

            yield encode_sse_event(event)

        if not completed:
            repo.update_run_status(run.id, status="completed")

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def latest_assistant_content(event: BusinessSseEvent) -> str | None:
    """从 state.snapshot 里取最后一条 assistant 消息正文。

    不同模型或 LangChain message 对象里，assistant 的角色可能叫 `assistant`，也可能
    规范化后叫 `ai`。这里先兼容这两个常见名字。
    """

    messages = event.data.get("messages")
    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in ("assistant", "ai") and isinstance(content, str):
            return content
    return None


def validate_uploaded_files_exist(
    file_ids: list[str],
    *,
    store: SlotFlowUploadStore,
) -> None:
    """Fail before run/message creation if any requested upload is missing."""

    for file_id in file_ids:
        try:
            store.get_upload(file_id)
        except UploadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="upload not found") from exc


def stage_uploaded_files(
    file_ids: list[str],
    *,
    run_id: str,
    store: SlotFlowUploadStore,
) -> list[UploadedFileContext]:
    """把请求里的 file_id 落位成本次 run 可读取的上传文件元数据。"""

    uploaded_files: list[UploadedFileContext] = []
    for file_id in file_ids:
        try:
            staged = store.stage_upload_for_run(file_id, run_id=run_id)
            uploaded_files.append(uploaded_file_to_context(staged))
        except UploadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="upload not found") from exc
    return uploaded_files


def uploaded_file_to_context(record: UploadedFileRecord) -> UploadedFileContext:
    """把上传存储层记录转换成 chat runtime 使用的上下文记录。"""

    return UploadedFileContext.model_validate(record.model_dump())


def get_repo(request: Request) -> ChatRepository:
    """从 app.state 取 chat 仓库。"""

    return request.app.state.chat_repo


def get_agent_adapter(request: Request) -> AgentAdapter:
    """从 app.state 取 agent adapter。"""

    return request.app.state.agent_adapter


def get_upload_store(request: Request) -> SlotFlowUploadStore:
    """从 app.state 取上传文件存储边界。"""

    return request.app.state.upload_store
