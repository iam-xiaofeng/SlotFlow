"""LangGraph v3 event streaming 的业务适配层。

这一层把 LangGraph / LangChain agent 的 v3 typed projection 整理成 SlotFlow 自己的
`AgentEvent`，让前端不必认识 `GraphRunStream.messages` / `.values` 这些内部投影。

按职责拆成子模块：

- `events`：`AgentEvent` / `AgentAdapter` 与 run 生命周期事件、agent 输入组装；
- `projections`：把投影 item 解析成业务事件与 JSON 友好结构的纯函数；
- `streaming`：`LangGraphEventAgentAdapter` 与多 channel 异步合流。

为保持既有导入路径不变，这里重新导出全部公开符号，调用方仍可
`from app.chat.agent_adapter import ...`。
"""

from __future__ import annotations

from app.chat.agent_adapter import events, projections, streaming
from app.chat.agent_adapter.events import (
    AgentAdapter,
    AgentEvent,
    AgentEventName,
    assistant_message_id,
    build_agent_input,
    build_user_message_content,
    collect_agent_events,
    make_context_compressing_event,
    make_finished_event,
    make_prepared_event,
)
from app.chat.agent_adapter.projections import (
    clarification_event_from_interrupt,
    extract_content_block_delta,
    extract_message_delta,
    extract_message_delta_parts,
    extract_provider_reasoning_content,
    extract_reasoning_from_content_block,
    extract_reasoning_text,
    extract_standard_reasoning_text,
    extract_text_block_text,
    has_lc_source_summarization,
    is_summarization_item,
    is_summarization_node_name,
    iter_content_blocks,
    list_content_blocks,
    normalize_mapping,
    normalize_message,
    normalize_message_content,
    normalize_messages,
    normalize_todos,
    normalize_values_snapshot,
    parse_clarification_payload,
    projection_item_to_agent_event,
    to_jsonable,
    todo_event_from_snapshot,
    tool_status_event_from_tool_call,
)
from app.chat.agent_adapter.streaming import (
    LangGraphEventAgentAdapter,
    ProjectionEnvelope,
    drain_message_projection_item,
    flatten_message_projection_items,
    iter_projection_agent_events,
    iter_typed_message_delta_items,
    projection_channels,
    typed_message_delta_channels,
)

__all__ = [
    "events",
    "projections",
    "streaming",
    "AgentAdapter",
    "AgentEvent",
    "AgentEventName",
    "assistant_message_id",
    "build_agent_input",
    "build_user_message_content",
    "collect_agent_events",
    "make_context_compressing_event",
    "make_finished_event",
    "make_prepared_event",
    "clarification_event_from_interrupt",
    "extract_content_block_delta",
    "extract_message_delta",
    "extract_message_delta_parts",
    "extract_provider_reasoning_content",
    "extract_reasoning_from_content_block",
    "extract_reasoning_text",
    "extract_standard_reasoning_text",
    "extract_text_block_text",
    "has_lc_source_summarization",
    "is_summarization_item",
    "is_summarization_node_name",
    "iter_content_blocks",
    "list_content_blocks",
    "normalize_mapping",
    "normalize_message",
    "normalize_message_content",
    "normalize_messages",
    "normalize_todos",
    "normalize_values_snapshot",
    "parse_clarification_payload",
    "projection_item_to_agent_event",
    "to_jsonable",
    "todo_event_from_snapshot",
    "tool_status_event_from_tool_call",
    "LangGraphEventAgentAdapter",
    "ProjectionEnvelope",
    "drain_message_projection_item",
    "flatten_message_projection_items",
    "iter_projection_agent_events",
    "iter_typed_message_delta_items",
    "projection_channels",
    "typed_message_delta_channels",
]
