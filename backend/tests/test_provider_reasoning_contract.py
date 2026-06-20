"""Provider contract fixtures for reasoning/content streaming.

This is the red-line guard for the fragile multi-provider streaming layer. Upstream
libraries disagree on how reasoning is emitted:

- DeepSeek (langchain-deepseek) -> additional_kwargs.reasoning_content, which SlotFlow's
  model subclass bridges into a standard {"type": "reasoning"} content block, so it also
  arrives via LangGraph v3's typed message.reasoning channel ({"channel": "reasoning"}).
- OpenAI reasoning models -> {"type": "reasoning"} content blocks / reasoning_content.
- Anthropic extended thinking -> {"type": "thinking"} content blocks.

Whatever the provider, the SlotFlow pipeline must normalize every chunk to exactly one
channel ("reasoning" or "content") with the right text, and never cross channels (reasoning
must not leak into the answer body, and answer text must not be tagged as reasoning).

`projection_item_to_agent_event(projection="messages", ...)` is the single normalization
entry these fixtures pin. If a future compat change regresses any provider, one of these
assertions fails immediately instead of surfacing as "thinking leaks into the answer".
"""

from __future__ import annotations

import pytest

from app.chat.agent_adapter import projection_item_to_agent_event
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config


def _bundle():
    return build_run_config(
        thread_id="thread_contract",
        run_id="run_contract",
        request=ChatStreamRequest(message="contract"),
    )


# (provider label, raw message-projection item, expected channel, expected delta)
PROVIDER_REASONING_CASES = [
    # DeepSeek: bridged reasoning arrives via the v3 typed reasoning channel.
    ("deepseek-typed-reasoning", {"channel": "reasoning", "delta": "先分析问题"}, "reasoning", "先分析问题"),
    ("deepseek-typed-content", {"channel": "content", "delta": "结论是 42"}, "content", "结论是 42"),
    # DeepSeek fallback: reasoning only in additional_kwargs (no content block yet).
    (
        "deepseek-additional-kwargs",
        {"additional_kwargs": {"reasoning_content": "通过 additional_kwargs"}},
        "reasoning",
        "通过 additional_kwargs",
    ),
    # OpenAI / DeepSeek standard reasoning content block.
    ("openai-reasoning-block", {"type": "reasoning", "reasoning": "逐步推理"}, "reasoning", "逐步推理"),
    # OpenRouter-style reasoning alias in additional_kwargs.
    (
        "openrouter-reasoning-alias",
        {"additional_kwargs": {"reasoning": "openrouter 推理"}},
        "reasoning",
        "openrouter 推理",
    ),
    # Anthropic extended thinking block.
    ("anthropic-thinking-block", {"type": "thinking", "thinking": "Claude 在思考"}, "reasoning", "Claude 在思考"),
    # Plain text content blocks across providers.
    ("openai-text-block", {"type": "text", "text": "最终答案"}, "content", "最终答案"),
    (
        "content-block-delta-event",
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "逐字"}},
        "content",
        "逐字",
    ),
]


@pytest.mark.parametrize(
    ("label", "item", "expected_channel", "expected_delta"),
    PROVIDER_REASONING_CASES,
    ids=[case[0] for case in PROVIDER_REASONING_CASES],
)
def test_message_projection_normalizes_to_single_channel(
    label: str,
    item: object,
    expected_channel: str,
    expected_delta: str,
) -> None:
    """每个产家的 chunk 形状都归一到正确的单一通道，且不串道。"""

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
    """思考块不能进入正文通道；正文块不能被标成思考。"""

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
        event = projection_item_to_agent_event(projection="messages", item=item, bundle=_bundle())
        assert event is not None and event.data["channel"] == "reasoning", item

    for item in content_items:
        event = projection_item_to_agent_event(projection="messages", item=item, bundle=_bundle())
        assert event is not None and event.data["channel"] == "content", item


def test_full_assistant_turn_segments_in_order() -> None:
    """一轮典型 assistant 流：先若干 reasoning，再若干 content，顺序与通道都正确。"""

    stream = [
        {"channel": "reasoning", "delta": "第一步"},
        {"channel": "reasoning", "delta": "第二步"},
        {"channel": "content", "delta": "因此"},
        {"channel": "content", "delta": "答案是 X"},
    ]
    observed = [
        (
            event.data["channel"],
            event.data["delta"],
        )
        for item in stream
        if (event := projection_item_to_agent_event(projection="messages", item=item, bundle=_bundle()))
        is not None
    ]
    assert observed == [
        ("reasoning", "第一步"),
        ("reasoning", "第二步"),
        ("content", "因此"),
        ("content", "答案是 X"),
    ]
