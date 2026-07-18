"""Deterministic thread-title behavior."""

from __future__ import annotations

import pytest

from app.chat import title_generation
from app.chat.models import MessageRecord, ThreadRecord


@pytest.mark.asyncio
async def test_disabled_title_model_uses_local_fallback_without_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLOTFLOW_TITLE_MODEL_ENABLED", "false")

    def fail_if_model_is_created(*_args, **_kwargs):
        raise AssertionError("deterministic title mode must not create a model")

    monkeypatch.setattr(title_generation, "create_chat_model", fail_if_model_is_created)
    thread = ThreadRecord(id="thread_title", title="New conversation")
    messages = [
        MessageRecord(
            id="message_user",
            thread_id=thread.id,
            role="user",
            content="  Analyze   this rate-limit issue  ",
        ),
        MessageRecord(
            id="message_assistant",
            thread_id=thread.id,
            role="assistant",
            content="Analysis complete",
        ),
    ]

    title = await title_generation.maybe_generate_thread_title(
        thread=thread,
        messages=messages,
        model_name="custom-selected-model",
        provider="custom",
    )

    assert title == "Analyze this rate-limit issue"
