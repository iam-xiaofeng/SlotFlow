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
    assert "thinking_blocks" not in assistant_payload


@pytest.mark.asyncio
async def test_litellm_stream_round_trips_signed_thinking_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic/Bedrock carrier shape: signed thinking must survive a tool loop.

    LiteLLM streams unsigned partial thinking blocks per delta and repeats the
    full accumulated text on the signature-bearing block; the next request must
    carry the signed block top-level, or LiteLLM silently disables extended
    thinking for the continuation turn.
    """

    responses = [
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "analyze the request",
                        "thinking_blocks": [
                            {"type": "thinking", "thinking": "analyze the request"}
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": "",
                        "reasoning_content": "",
                        "thinking_blocks": [
                            {
                                "type": "thinking",
                                "thinking": "analyze the request, then read the file",
                                "signature": "sig-abc123",
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "content": "",
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
        model="anthropic/claude-sonnet-4-5",
        api_key="key",
        streaming=True,
    )
    monkeypatch.setattr(model.client, "acompletion", fake_acompletion)

    merged = None
    async for chunk in model.astream([HumanMessage(content="test")]):
        merged = chunk if merged is None else merged + chunk
    assert merged is not None

    captured = merged.additional_kwargs["thinking_blocks"]
    assert {
        "type": "thinking",
        "thinking": "analyze the request, then read the file",
        "signature": "sig-abc123",
    } in captured

    followup = AIMessage(
        content=merged.content,
        additional_kwargs=merged.additional_kwargs,
        tool_calls=[
            {"name": "workspace_read", "args": {"path": "README.md"}, "id": "call_1"}
        ],
    )
    messages, _ = model._create_message_dicts(
        [
            HumanMessage(content="analyze"),
            followup,
            ToolMessage(content="ok", tool_call_id="call_1"),
        ],
        None,
    )

    assistant_payload = messages[1]
    assert assistant_payload["content"] == ""
    assert assistant_payload["thinking_blocks"] == [
        {
            "type": "thinking",
            "thinking": "analyze the request, then read the file",
            "signature": "sig-abc123",
        }
    ]
    assert assistant_payload["reasoning_content"] == "analyze the request"
    assert assistant_payload["tool_calls"][0]["id"] == "call_1"


def test_litellm_nonstream_response_captures_thinking_blocks() -> None:
    model = ChatLiteLLM(model="anthropic/claude-sonnet-4-5", api_key="key")
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "reasoning_content": "why",
                    "thinking_blocks": [
                        {"type": "thinking", "thinking": "why", "signature": "sig-1"}
                    ],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    message = model._create_chat_result(response).generations[0].message

    assert message.additional_kwargs["thinking_blocks"] == [
        {"type": "thinking", "thinking": "why", "signature": "sig-1"}
    ]
    assert message.additional_kwargs["reasoning_content"] == "why"


def test_thinking_blocks_consolidation_prefers_complete_blocks() -> None:
    """Signed blocks subsume unsigned partials; redacted blocks pass through in order."""

    model = ChatLiteLLM(model="anthropic/claude-sonnet-4-5", api_key="key")
    messages, _ = model._create_message_dicts(
        [
            AIMessage(
                content="",
                additional_kwargs={
                    "thinking_blocks": [
                        {"type": "thinking", "thinking": "partial one"},
                        {"type": "thinking", "thinking": "partial two"},
                        {
                            "type": "thinking",
                            "thinking": "partial onepartial two",
                            "signature": "sig-full",
                        },
                        {"type": "redacted_thinking", "data": "opaque-bytes"},
                    ]
                },
            ),
        ],
        None,
    )

    assert messages[0]["thinking_blocks"] == [
        {
            "type": "thinking",
            "thinking": "partial onepartial two",
            "signature": "sig-full",
        },
        {"type": "redacted_thinking", "data": "opaque-bytes"},
    ]


def test_unsigned_thinking_partials_merge_when_no_signature_exists() -> None:
    model = ChatLiteLLM(model="deepseek/deepseek-v4-pro", api_key="key")
    messages, _ = model._create_message_dicts(
        [
            AIMessage(
                content="",
                additional_kwargs={
                    "thinking_blocks": [
                        {"type": "thinking", "thinking": "first "},
                        {"type": "thinking", "thinking": "second"},
                    ]
                },
            ),
        ],
        None,
    )

    assert messages[0]["thinking_blocks"] == [
        {"type": "thinking", "thinking": "first second"}
    ]


def test_gemini_thought_signature_tool_call_id_round_trips() -> None:
    """Gemini carrier shape: LiteLLM encodes the thought signature into the
    tool_call_id, so the id must survive the round trip byte-for-byte."""

    model = ChatLiteLLM(model="gemini/gemini-2.5-pro", api_key="key")
    signed_id = "call_7__thought__c2lnbmF0dXJl"
    messages, _ = model._create_message_dicts(
        [
            HumanMessage(content="hi"),
            AIMessage(
                content="",
                tool_calls=[{"name": "workspace_read", "args": {}, "id": signed_id}],
            ),
            ToolMessage(content="ok", tool_call_id=signed_id),
        ],
        None,
    )

    assert messages[1]["tool_calls"][0]["id"] == signed_id
    assert messages[2]["tool_call_id"] == signed_id


def test_plain_assistant_text_content_normalizes_to_string() -> None:
    """OpenAI Chat Completions shape: text-only assistant content — including the
    bare-string items left behind by streamed chunk merging — collapses to a plain
    string, the most universally accepted assistant shape. A list payload such as
    ``["answer"]`` crashes LiteLLM's DeepSeek transform and is silently dropped by
    lenient relays, which surfaced as the agent losing its own previous replies."""

    model = ChatLiteLLM(model="openai/gpt-5.2", api_key="key")
    messages, _ = model._create_message_dicts(
        [
            HumanMessage(content="hi"),
            AIMessage(
                content=[{"type": "text", "text": "hello"}],
                tool_calls=[{"name": "workspace_read", "args": {}, "id": "call_2"}],
            ),
            ToolMessage(content="ok", tool_call_id="call_2"),
            AIMessage(
                content=[{"type": "thinking", "thinking": "hmm"}, "final answer"],
            ),
        ],
        None,
    )

    assistant_payload = messages[1]
    assert assistant_payload["content"] == "hello"
    assert "thinking_blocks" not in assistant_payload
    assert "reasoning_content" not in assistant_payload
    assert assistant_payload["tool_calls"][0]["id"] == "call_2"

    merged_stream_payload = messages[3]
    assert merged_stream_payload["content"] == "final answer"


def test_structured_assistant_content_preserves_block_order() -> None:
    """When structural blocks remain, filtering must not reorder content: bare
    strings are wrapped as text blocks in place and empty text items are dropped
    (providers such as Anthropic reject empty text blocks)."""

    image = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    model = ChatLiteLLM(model="openai/gpt-5.2", api_key="key")
    messages, _ = model._create_message_dicts(
        [
            AIMessage(
                content=[
                    image,
                    {"type": "thinking", "thinking": "hmm"},
                    "caption",
                    "",
                    {"type": "text", "text": ""},
                ],
            ),
        ],
        None,
    )

    assert messages[0]["content"] == [
        image,
        {"type": "text", "text": "caption"},
    ]


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