"""Contracts for LiteLLM reasoning, content, and tool-call streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.chat.runtime.models import ChatLiteLLM

from app.chat.agent_adapter import projection_item_to_agent_event
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config


def _bundle():
    return build_run_config(
        thread_id="thread_contract",
        run_id="run_contract",
        request=ChatStreamRequest(message="contract"),
    )


@pytest.mark.asyncio
async def test_litellm_stream_normalizes_reasoning_and_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "analyze first",
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": "answer",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "workspace_read",
                                    "arguments": '{"path":"README.md"}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
    ]

    async def fake_acompletion(**_: Any) -> AsyncIterator[dict[str, Any]]:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            for response in responses:
                yield response

        return stream()

    model = ChatLiteLLM(
        model="deepseek-v4-pro",
        custom_llm_provider="deepseek",
        api_key="key",
        streaming=True,
    )
    monkeypatch.setattr(model.client, "acompletion", fake_acompletion)

    chunks = [chunk async for chunk in model.astream([HumanMessage(content="test")])]

    reasoning_chunk = next(
        chunk for chunk in chunks if chunk.additional_kwargs.get("reasoning_content")
    )
    assert reasoning_chunk.additional_kwargs["reasoning_content"] == "analyze first"
    assert reasoning_chunk.content_blocks[0] == {
        "type": "reasoning",
        "reasoning": "analyze first",
    }
    reasoning_event = projection_item_to_agent_event(
        projection="messages",
        item=reasoning_chunk,
        bundle=_bundle(),
    )
    assert reasoning_event is not None
    assert reasoning_event.data["channel"] == "reasoning"
    assert reasoning_event.data["delta"] == "analyze first"

    tool_chunk = next(chunk for chunk in chunks if chunk.tool_call_chunks)
    assert tool_chunk.tool_call_chunks == [
        {
            "name": "workspace_read",
            "args": '{"path":"README.md"}',
            "id": "call_1",
            "index": 0,
            "type": "tool_call_chunk",
        }
    ]


def test_litellm_tool_followup_strips_reasoning_metadata_from_content() -> None:
    model = ChatLiteLLM(
        model="deepseek/deepseek-v4-pro",
        api_key="key",
        streaming=True,
    )
    messages, _ = model._create_message_dicts(
        [
            HumanMessage(content="analyze"),
            AIMessage(
                content=[
                    {"type": "reasoning", "reasoning": "analyze first"},
                    {
                        "type": "non_standard",
                        "value": {"type": "thinking", "thinking": "analyze first"},
                    },
                ],
                additional_kwargs={"reasoning_content": "analyze first"},
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {"path": "README.md"},
                        "id": "call_1",
                    }
                ],
            ),
            ToolMessage(content="ok", tool_call_id="call_1"),
        ],
        None,
    )

    assistant_payload = messages[1]
    assert assistant_payload["content"] == ""
    assert assistant_payload["reasoning_content"] == "analyze first"
    assert assistant_payload["tool_calls"][0]["id"] == "call_1"


@pytest.mark.parametrize(
    ("label", "item", "expected_channel", "expected_delta"),
    [
        (
            "typed-reasoning",
            {"channel": "reasoning", "delta": "analyze"},
            "reasoning",
            "analyze",
        ),
        (
            "typed-content",
            {"channel": "content", "delta": "answer"},
            "content",
            "answer",
        ),
        (
            "litellm-reasoning-content",
            {"additional_kwargs": {"reasoning_content": "fallback reasoning"}},
            "reasoning",
            "fallback reasoning",
        ),
        (
            "langchain-reasoning-block",
            {"type": "reasoning", "reasoning": "standard reasoning"},
            "reasoning",
            "standard reasoning",
        ),
        (
            "litellm-thinking-block",
            {"type": "thinking", "thinking": "normalized reasoning"},
            "reasoning",
            "normalized reasoning",
        ),
        (
            "text-block",
            {"type": "text", "text": "final answer"},
            "content",
            "final answer",
        ),
    ],
)
def test_message_projection_consumes_litellm_contract(
    label: str,
    item: object,
    expected_channel: str,
    expected_delta: str,
) -> None:
    event = projection_item_to_agent_event(
        projection="messages",
        item=item,
        bundle=_bundle(),
    )

    assert event is not None, f"{label}: produced no message.delta"
    assert event.event == "message.delta"
    assert event.data["channel"] == expected_channel, f"{label}: wrong channel"
    assert event.data["delta"] == expected_delta, f"{label}: wrong delta"


def test_reasoning_and_content_never_cross_channels() -> None:
    reasoning_items = [
        {"type": "thinking", "thinking": "think"},
        {"type": "reasoning", "reasoning": "reason"},
        {"channel": "reasoning", "delta": "typed-reason"},
        {"additional_kwargs": {"reasoning_content": "kw-reason"}},
    ]
    content_items = [
        {"type": "text", "text": "answer"},
        {"channel": "content", "delta": "typed-content"},
    ]

    for item in reasoning_items:
        event = projection_item_to_agent_event(
            projection="messages", item=item, bundle=_bundle()
        )
        assert event is not None and event.data["channel"] == "reasoning", item

    for item in content_items:
        event = projection_item_to_agent_event(
            projection="messages", item=item, bundle=_bundle()
        )
        assert event is not None and event.data["channel"] == "content", item


def test_full_assistant_turn_segments_in_order() -> None:
    stream = [
        {"channel": "reasoning", "delta": "first"},
        {"channel": "reasoning", "delta": "second"},
        {"channel": "content", "delta": "therefore"},
        {"channel": "content", "delta": "the answer is X"},
    ]
    observed = [
        (event.data["channel"], event.data["delta"])
        for item in stream
        if (
            event := projection_item_to_agent_event(
                projection="messages", item=item, bundle=_bundle()
            )
        )
        is not None
    ]

    assert observed == [
        ("reasoning", "first"),
        ("reasoning", "second"),
        ("content", "therefore"),
        ("content", "the answer is X"),
    ]