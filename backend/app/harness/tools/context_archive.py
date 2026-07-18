"""Thread-scoped read-only access to canonical LangGraph message history."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from app.harness.utils import message_content_text, message_role


def build_context_archive_tools() -> list[BaseTool]:
    @tool("context_archive_search")
    def context_archive_search(
        query: str,
        state: Annotated[dict[str, Any], InjectedState],
        limit: int = 10,
    ) -> str:
        """Search older full conversation/tool messages hidden by context compaction."""

        terms = [term for term in query.casefold().split() if term]
        matches = []
        messages = list(state.get("messages") or [])
        for index, message in enumerate(messages):
            text = message_content_text(message)
            haystack = text.casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            matches.append({
                "archive_id": f"message:{index}",
                "role": message_role(message),
                "preview": text[:500],
                "size_chars": len(text),
            })
            if len(matches) >= max(1, min(limit, 20)):
                break
        return json.dumps({"query": query, "matches": matches}, ensure_ascii=False)

    @tool("context_archive_read")
    def context_archive_read(
        archive_id: str,
        state: Annotated[dict[str, Any], InjectedState],
        offset: int = 0,
        max_chars: int = 12000,
    ) -> str:
        """Read one current-thread archive message by id with a bounded character range."""

        prefix = "message:"
        if not archive_id.startswith(prefix):
            return json.dumps({"error": "invalid archive_id"})
        try:
            index = int(archive_id[len(prefix):])
        except ValueError:
            return json.dumps({"error": "invalid archive_id"})
        messages = list(state.get("messages") or [])
        if index < 0 or index >= len(messages):
            return json.dumps({"error": "archive entry not found"})
        text = message_content_text(messages[index])
        start = max(0, offset)
        size = max(1, min(max_chars, 24000))
        end = min(len(text), start + size)
        return json.dumps({
            "archive_id": archive_id,
            "role": message_role(messages[index]),
            "offset": start,
            "next_offset": end if end < len(text) else None,
            "size_chars": len(text),
            "content": text[start:end],
        }, ensure_ascii=False)

    return [context_archive_search, context_archive_read]
