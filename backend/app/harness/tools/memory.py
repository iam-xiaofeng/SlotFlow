"""SlotFlow long-term memory tools."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from app.chat.models import RunContext
from app.harness.memory import MemoryKind, MemoryNotFoundError, SlotFlowMemoryStore


def build_memory_tools(
    *,
    memory_store: SlotFlowMemoryStore | None,
    run_context: RunContext | None,
) -> list[BaseTool]:
    """Build tools that let the agent explicitly manage long-term memory."""

    if memory_store is None:
        return []

    @tool("memory_list")
    def memory_list(query: str = "", limit: int = 10) -> str:
        """Search or list SlotFlow long-term memories."""

        bounded_limit = max(1, min(limit, 50))
        if query.strip():
            records = memory_store.search_memories(
                query=query,
                thread_id=run_context.thread_id if run_context else None,
                limit=bounded_limit,
            )
        else:
            records = memory_store.list_memories(
                thread_id=None,
                limit=bounded_limit,
            )
        return json.dumps(
            {
                "memories": [record.model_dump(mode="json") for record in records],
                "source": "slotflow_long_term_memory",
            },
            ensure_ascii=False,
        )

    @tool("memory_save")
    def memory_save(content: str, kind: MemoryKind = "manual") -> str:
        """Save a durable SlotFlow long-term memory with a category."""

        try:
            record = memory_store.add_memory(
                thread_id=run_context.thread_id if run_context else None,
                kind=kind,
                content=content,
                metadata={
                    "source": "slotflow_memory_tool",
                    "run_id": run_context.run_id if run_context else None,
                },
            )
        except ValueError as exc:
            return json.dumps(
                {"error": str(exc), "source": "slotflow_long_term_memory"},
                ensure_ascii=False,
            )
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False)

    @tool("memory_update")
    def memory_update(memory_id: str, content: str, kind: MemoryKind | None = None) -> str:
        """Replace the content of an existing SlotFlow long-term memory."""

        try:
            record = memory_store.update_memory(
                memory_id,
                kind=kind,
                content=content,
                metadata={
                    "updated_by": "slotflow_memory_tool",
                    "run_id": run_context.run_id if run_context else None,
                },
            )
        except MemoryNotFoundError:
            return json.dumps(
                {
                    "error": "memory_not_found",
                    "memory_id": memory_id,
                    "source": "slotflow_long_term_memory",
                },
                ensure_ascii=False,
            )
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False)

    @tool("memory_delete")
    def memory_delete(memory_id: str) -> str:
        """Delete one SlotFlow long-term memory by ID."""

        try:
            memory_store.delete_memory(memory_id)
        except MemoryNotFoundError:
            return json.dumps(
                {
                    "deleted": False,
                    "error": "memory_not_found",
                    "memory_id": memory_id,
                    "source": "slotflow_long_term_memory",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "deleted": True,
                "memory_id": memory_id,
                "source": "slotflow_long_term_memory",
            },
            ensure_ascii=False,
        )

    return [memory_list, memory_save, memory_update, memory_delete]
