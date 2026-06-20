"""聊天相关 FastAPI 路由。

聊天 stream 路由把请求链路串起来：

```txt
HTTP 请求
-> 仓库创建 run
-> build_run_config
-> AgentAdapter.stream_events
-> iter_business_events
-> encode_sse_event
-> StreamingResponse
```

这一层是“编排层”。它不自己生成回答，也不自己解析 LangGraph 投影。路由只负责把
一次 HTTP 请求变成一次可记录、可流式返回的 run。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.chat.models import (
    ChatStreamRequest,
    MessageRecord,
    ModelCatalogRecord,
    ModelProvider,
    ThreadSearchResultRecord,
    ThreadCreateRequest,
    ThreadRecord,
    UploadedFileContext,
)
from app.chat.model_catalog import discover_model_catalog
from app.chat.repository import ChatRepository, MessageNotFoundError, ThreadNotFoundError
from app.chat.run_config import build_run_config, request_thinking_enabled
from app.chat.sse import BusinessSseEvent, encode_sse_event, iter_business_events
from app.chat.title_generation import maybe_generate_thread_title
from app.dependencies import get_agent_adapter, get_chat_repo, get_upload_store
from app.uploads.models import UploadedFileRecord
from app.uploads.storage import SlotFlowUploadStore, UploadNotFoundError


router = APIRouter(prefix="/api/chat", tags=["Chat"])


@router.get("/models", response_model=ModelCatalogRecord)
async def list_models() -> ModelCatalogRecord:
    """Return models discovered from configured provider API credentials."""

    return await discover_model_catalog()


@router.post("/threads", response_model=ThreadRecord)
async def create_thread(
    body: ThreadCreateRequest,
    request: Request,
) -> ThreadRecord:
    """创建一条空会话。

    前端第一次打开新聊天时可以先建 thread，再把用户消息发到这个 thread 的
    `runs/stream` 接口。
    """

    return get_chat_repo(request).create_thread(title=body.title)


@router.get("/threads", response_model=list[ThreadRecord])
async def list_threads(request: Request) -> list[ThreadRecord]:
    """列出所有会话，最近活动的排在前面。"""

    return get_chat_repo(request).list_threads()


@router.get("/search", response_model=list[ThreadSearchResultRecord])
async def search_threads(
    request: Request,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[ThreadSearchResultRecord]:
    """Search stored thread titles and message content."""

    return get_chat_repo(request).search_threads(q, limit=limit)


@router.get("/threads/{thread_id}", response_model=ThreadRecord)
async def get_thread(thread_id: str, request: Request) -> ThreadRecord:
    """读取单条会话，不存在时返回 404。"""

    repo = get_chat_repo(request)
    try:
        return repo.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, request: Request) -> None:
    """删除一条会话及其消息记录。"""

    repo = get_chat_repo(request)
    try:
        repo.delete_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.get("/threads/{thread_id}/messages", response_model=list[MessageRecord])
async def list_messages(thread_id: str, request: Request) -> list[MessageRecord]:
    """读取某个 thread 下已经保存的消息。"""

    repo = get_chat_repo(request)
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

    这里是后端聊天运行链路的核心入口。它做的事情按顺序是：

    1. 确认 thread 存在；
    2. 解析请求里的上传文件 ID；
    3. 保存用户消息；
    4. 创建 run；
    5. 构建 `config + context`；
    6. 调用 agent adapter；
    7. 把业务事件编码成 SSE；
    8. 根据流式结果更新 run 状态和 assistant 消息。
    """

    repo = get_chat_repo(request)
    adapter = get_agent_adapter(request)

    try:
        repo.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc

    upload_store = get_upload_store(request)
    validate_uploaded_files_exist(body.files, store=upload_store)
    if body.reuse_user_message_id:
        reuse_user_message(
            repo,
            thread_id=thread_id,
            message_id=body.reuse_user_message_id,
            content=body.message,
        )
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

    if body.reuse_user_message_id is None:
        repo.add_message(
            thread_id,
            role="user",
            content=body.message,
            metadata={
                "files": list(body.files),
                "model_name": body.model_name,
                "mode": body.mode,
                "thinking_enabled": request_thinking_enabled(body),
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
        assistant_reasoning_parts: list[str] = []
        snapshot_message_content: str | None = None
        snapshot_reasoning_content: str | None = None
        clarification_saved = False
        completed = False

        try:
            events = adapter.stream_events(request=body, bundle=bundle)
            async for event in iter_business_events(events):
                if event.event == "message.delta":
                    delta = event.data.get("delta")
                    if isinstance(delta, str):
                        if event.data.get("channel") == "reasoning":
                            assistant_reasoning_parts.append(delta)
                        else:
                            assistant_text_parts.append(delta)

                if event.event == "state.snapshot":
                    snapshot_message_content = latest_assistant_content(event)
                    snapshot_reasoning_content = latest_assistant_reasoning_content(event)

                if event.event == "clarification.requested" and not clarification_saved:
                    repo.add_message(
                        thread_id,
                        role="assistant",
                        content=format_clarification_content(event.data),
                        run_id=run.id,
                        metadata={
                            "source": "clarification",
                            "clarification": dict(event.data),
                        },
                    )
                    clarification_saved = True

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
                    reasoning_content = select_assistant_reasoning_content(
                        snapshot_reasoning_content=snapshot_reasoning_content,
                        streamed_reasoning_content="".join(assistant_reasoning_parts),
                    )
                    if content and not clarification_saved:
                        repo.add_message(
                            thread_id,
                            role="assistant",
                            content=content,
                            run_id=run.id,
                            metadata=assistant_message_metadata(
                                reasoning_content,
                                thinking_enabled=bundle.context.thinking_enabled,
                            ),
                        )
                        await update_thread_title_after_first_exchange(
                            repo,
                            thread_id=thread_id,
                            model_name=body.model_name,
                            provider=body.provider,
                        )
                    repo.update_run_status(run.id, status="completed")
                    completed = True

                yield encode_sse_event(event)
        except asyncio.CancelledError:
            repo.update_run_status(run.id, status="cancelled")
            raise

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

    return latest_assistant_message_field(event, "content")


async def update_thread_title_after_first_exchange(
    repo: ChatRepository,
    *,
    thread_id: str,
    model_name: str,
    provider: ModelProvider | None = None,
) -> None:
    thread = repo.get_thread(thread_id)
    messages = repo.list_messages(thread_id)
    title = await maybe_generate_thread_title(
        thread=thread,
        messages=messages,
        model_name=model_name,
        provider=provider,
    )
    if title and title != thread.title:
        repo.update_thread_title(thread_id, title)


def latest_assistant_reasoning_content(event: BusinessSseEvent) -> str | None:
    """从 state.snapshot 里取最后一条 assistant reasoning_content。"""

    return latest_assistant_message_field(event, "reasoning_content")


def select_assistant_reasoning_content(
    *,
    snapshot_reasoning_content: str | None,
    streamed_reasoning_content: str,
) -> str | None:
    streamed = streamed_reasoning_content.strip()
    snapshot = (
        snapshot_reasoning_content.strip()
        if isinstance(snapshot_reasoning_content, str)
        else ""
    )
    if streamed and snapshot:
        # LangGraph values snapshots can retain only the latest reasoning chunk,
        # while message.delta carries the complete live stream.
        return snapshot if len(snapshot) > len(streamed) else streamed

    if streamed:
        return streamed

    if snapshot:
        return snapshot

    return None


def latest_assistant_message_field(event: BusinessSseEvent, field: str) -> str | None:
    messages = event.data.get("messages")
    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        value = message.get(field)
        if role in ("assistant", "ai") and isinstance(value, str):
            return value
    return None


def assistant_message_metadata(
    reasoning_content: str | None,
    *,
    thinking_enabled: bool = False,
) -> dict:
    metadata = {"source": "agent"}
    if thinking_enabled:
        metadata["thinking_enabled"] = True
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        metadata["reasoning_content"] = reasoning_content
    return metadata


def format_clarification_content(payload: dict) -> str:
    """Build readable fallback text for persisted clarification messages."""

    lines: list[str] = []
    context = payload.get("context")
    question = payload.get("question")
    if isinstance(context, str) and context.strip():
        lines.append(context.strip())
    if isinstance(question, str) and question.strip():
        lines.append(question.strip())

    options = payload.get("options")
    if isinstance(options, list) and options:
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            label = option.get("label")
            if isinstance(option_id, str) and isinstance(label, str):
                lines.append(f"{option_id}. {label}")

    return "\n".join(lines).strip() or "请确认下一步。"


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


def reuse_user_message(
    repo: ChatRepository,
    *,
    thread_id: str,
    message_id: str,
    content: str,
) -> None:
    """Overwrite a user message and remove later messages before regenerating."""

    try:
        target = next(
            message
            for message in repo.list_messages(thread_id)
            if message.id == message_id
        )
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="message not found") from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc

    if target.role != "user":
        raise HTTPException(status_code=400, detail="message must be a user message")

    try:
        repo.update_message_content(thread_id, message_id, content=content)
        repo.delete_messages_after(thread_id, message_id)
    except MessageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="message not found") from exc


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
