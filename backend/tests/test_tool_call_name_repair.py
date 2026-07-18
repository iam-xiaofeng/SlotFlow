"""Recovering tool-call names dropped by relay streaming assembly."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.chat.litellm_provider import repair_streamed_tool_call_names


def _message(tool_calls, raw):
    return AIMessage(content="", tool_calls=tool_calls, additional_kwargs={"tool_calls": raw})


def test_backfills_empty_name_from_additional_kwargs_by_id() -> None:
    msg = _message(
        [{"name": "", "args": {"query": "x"}, "id": "call-abc-0", "type": "tool_call"}],
        [{"id": "call-abc-0", "function": {"name": "web_search", "arguments": "{}"}, "type": "function", "index": 0}],
    )
    repair_streamed_tool_call_names(msg)
    assert msg.tool_calls[0]["name"] == "web_search"


def test_positional_fallback_when_all_missing_and_counts_match() -> None:
    msg = _message(
        [
            {"name": "", "args": {}, "id": "x1", "type": "tool_call"},
            {"name": "", "args": {}, "id": "x2", "type": "tool_call"},
        ],
        [
            {"id": "y1", "function": {"name": "web_search"}, "type": "function"},
            {"id": "y2", "function": {"name": "web_fetch"}, "type": "function"},
        ],
    )
    repair_streamed_tool_call_names(msg)
    assert [tc["name"] for tc in msg.tool_calls] == ["web_search", "web_fetch"]


def test_noop_when_name_already_present() -> None:
    msg = _message(
        [{"name": "web_search", "args": {}, "id": "i", "type": "tool_call"}],
        [{"id": "i", "function": {"name": "OTHER"}, "type": "function"}],
    )
    repair_streamed_tool_call_names(msg)
    assert msg.tool_calls[0]["name"] == "web_search"  # never overwrite a present name


def test_noop_without_tool_calls() -> None:
    msg = AIMessage(content="hello")
    assert repair_streamed_tool_call_names(msg) is msg
    assert msg.tool_calls == []
