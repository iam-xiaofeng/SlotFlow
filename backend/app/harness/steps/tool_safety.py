"""Step: tool-call safety — turn unsafe failures into model-readable error ToolMessages.

Extracted from ``SlotFlowToolSafetyMiddleware`` (the error ToolMessage builders are shared with
the dangling-tool-call step).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage

from app.harness.tools.host_execution import (
    is_unsafe_host_execution_tool_name,
    unsafe_host_execution_tool_message,
)

SLOTFLOW_TOOL_ERROR_SOURCE = "slotflow_tool_safety"


def build_error_tool_message(
    tool_call: Any,
    *,
    error_type: str,
    message: str,
    exception_type: str | None = None,
    source: str = SLOTFLOW_TOOL_ERROR_SOURCE,
) -> ToolMessage:
    call_id = tool_call_id(tool_call) or "missing_tool_call_id"
    name = tool_call_name(tool_call)
    payload: dict[str, Any] = {
        "error": {
            "type": error_type,
            "message": message,
            "tool_name": name,
            "tool_call_id": call_id,
            "source": source,
        }
    }
    if exception_type is not None:
        payload["error"]["exception_type"] = exception_type
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=name,
        tool_call_id=call_id,
        status="error",
    )


def build_unknown_tool_error_message(tool_call: Any) -> ToolMessage:
    name = tool_call_name(tool_call)
    if is_unsafe_host_execution_tool_name(name):
        return build_error_tool_message(
            tool_call,
            error_type="unsafe_host_execution_tool",
            message=unsafe_host_execution_tool_message(name),
        )
    return build_error_tool_message(
        tool_call,
        error_type="unknown_tool",
        message=f"tool is not registered: {name!r}",
    )


def tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        raw_id = tool_call.get("id")
    else:
        raw_id = getattr(tool_call, "id", None)
    if raw_id is None:
        return None
    return str(raw_id)


def tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        raw_name = tool_call.get("name")
        function = tool_call.get("function")
        if raw_name is None and isinstance(function, dict):
            raw_name = function.get("name")
    else:
        raw_name = getattr(tool_call, "name", None)
    return str(raw_name or "unknown_tool")
