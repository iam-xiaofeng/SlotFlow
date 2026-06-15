"""Middleware that makes uploaded workspace files explicit to the model."""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext, UploadedFileContext
from app.harness.state import SlotFlowAgentState


UPLOADS_BLOCK_START = "<slotflow-uploaded-files>"
UPLOADS_BLOCK_END = "</slotflow-uploaded-files>"


class SlotFlowUploadsMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject current-run upload metadata into the latest user message."""

    name = "SlotFlowUploadsMiddleware"

    @override
    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None or not context.uploaded_files:
            return None

        update = _slotflow_update(state, context.uploaded_files)
        messages = list(state.get("messages") or [])
        if messages and isinstance(messages[-1], HumanMessage):
            last_message = messages[-1]
            if not _content_contains_uploads_block(last_message.content):
                upload_context = _format_uploaded_files(context.uploaded_files)
                messages[-1] = HumanMessage(
                    content=_prepend_text(last_message.content, f"{upload_context}\n\n"),
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
        "files": [
            uploaded_file.model_dump(mode="json") for uploaded_file in uploaded_files
        ],
    }
    return {"slotflow": slotflow}


def _format_uploaded_files(uploaded_files: list[UploadedFileContext]) -> str:
    lines = [
        UPLOADS_BLOCK_START,
        "Files uploaded for this message are available in the SlotFlow workspace.",
        "Use `workspace_read(path)` before answering file-content questions.",
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
