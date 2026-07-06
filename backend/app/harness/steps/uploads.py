"""Step: inject uploaded workspace files into the latest user message.

Extracted from ``SlotFlowUploadsMiddleware.before_agent``. Stateless; reuses the
``build_slotflow_workspace`` sandbox reader so image uploads become image_url blocks.
"""

from __future__ import annotations

import base64
from typing import Any

from langchain_core.messages import HumanMessage

from app.chat.models import RunContext, UploadedFileContext
from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.state import SlotFlowAgentState

UPLOADS_BLOCK_START = "<slotflow-uploaded-files>"
UPLOADS_BLOCK_END = "</slotflow-uploaded-files>"


def uploads_update(
    *,
    state: SlotFlowAgentState,
    context: RunContext,
    sandbox_config: SlotFlowSandboxConfig | None = None,
) -> dict[str, Any] | None:
    if context is None or not context.uploaded_files:
        return None
    workspace = build_slotflow_workspace(sandbox_config)
    update = _slotflow_update(state, context.uploaded_files)
    messages = list(state.get("messages") or [])
    if messages and isinstance(messages[-1], HumanMessage):
        last_message = messages[-1]
        if not _content_contains_uploads_block(last_message.content):
            upload_context = _format_uploaded_files(context.uploaded_files)
            image_blocks = _image_content_blocks(context.uploaded_files, workspace=workspace)
            messages[-1] = HumanMessage(
                content=_prepend_upload_content(
                    last_message.content,
                    f"{upload_context}\n\n",
                    image_blocks=image_blocks,
                ),
                id=last_message.id,
                name=last_message.name,
                additional_kwargs=last_message.additional_kwargs,
                response_metadata=last_message.response_metadata,
            )
            update["messages"] = messages
    return update


def _slotflow_update(
    state: SlotFlowAgentState,
    uploaded_files: list[UploadedFileContext],
) -> dict[str, Any]:
    slotflow = dict(state.get("slotflow") or {})
    slotflow["uploads"] = {
        "count": len(uploaded_files),
        "files": [uploaded_file.model_dump(mode="json") for uploaded_file in uploaded_files],
    }
    return {"slotflow": slotflow}


def _format_uploaded_files(uploaded_files: list[UploadedFileContext]) -> str:
    lines = [
        UPLOADS_BLOCK_START,
        "Files uploaded for this message are available in the SlotFlow workspace.",
        "Use `workspace_read(path)` before answering file-content questions.",
        "Image uploads are also attached as image_url content blocks for vision-capable models.",
        "",
    ]
    for uploaded_file in uploaded_files:
        display_name = uploaded_file.original_filename or uploaded_file.filename
        content_type = uploaded_file.content_type or "unknown"
        lines.append(
            "- "
            f"path={uploaded_file.workspace_path}; "
            f"filename={display_name}; "
            f"stored_filename={uploaded_file.filename}; "
            f"content_type={content_type}; "
            f"size_bytes={uploaded_file.size_bytes}"
        )
    lines.append(UPLOADS_BLOCK_END)
    return "\n".join(lines)


def _content_contains_uploads_block(content: Any) -> bool:
    if isinstance(content, str):
        return UPLOADS_BLOCK_START in content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str) and UPLOADS_BLOCK_START in item:
                return True
            if (
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and UPLOADS_BLOCK_START in item["text"]
            ):
                return True
    return False


def _prepend_text(content: Any, prefix: str) -> Any:
    if isinstance(content, str):
        return f"{prefix}{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}, *content]
    return content


def _prepend_upload_content(
    content: Any,
    prefix: str,
    *,
    image_blocks: list[dict[str, Any]],
) -> Any:
    if not image_blocks:
        return _prepend_text(content, prefix)
    if isinstance(content, str):
        return [{"type": "text", "text": f"{prefix}{content}"}, *image_blocks]
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}, *content, *image_blocks]
    return content


def _image_content_blocks(
    uploaded_files: list[UploadedFileContext],
    *,
    workspace,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for uploaded_file in uploaded_files:
        if not _is_image_upload(uploaded_file):
            continue
        try:
            target = workspace.resolve_path(uploaded_file.workspace_path)
            if not target.is_file() or target.stat().st_size > workspace.config.max_read_bytes:
                continue
            encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        except Exception:
            continue
        media_type = uploaded_file.content_type or _image_media_type_from_name(uploaded_file.filename)
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{encoded}"},
            }
        )
    return blocks


def _is_image_upload(uploaded_file: UploadedFileContext) -> bool:
    content_type = uploaded_file.content_type or ""
    if content_type.startswith("image/"):
        return True
    return uploaded_file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _image_media_type_from_name(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
