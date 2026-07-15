"""领域模型和可读 ID 测试。

这组测试不碰 FastAPI，也不碰 agent。它只保护最底层的数据形状：

- ID 是否能让人一眼看出对象类型；
- 前端发来的 stream 请求是否有稳定默认值；
- 空白提问和非法枚举是否会被拦住；
- Pydantic 的默认 list/dict 是否彼此独立，避免一次请求污染下一次请求。

这些测试保护最底层的 API 数据契约，方便后续定位边界问题。
"""

from __future__ import annotations

import re
from datetime import UTC

import pytest
from pydantic import ValidationError

from app.chat.ids import new_message_id, new_run_id, new_thread_id
from app.chat.models import (
    ChatStreamRequest,
    MessageRecord,
    RunConfigBundle,
    RunContext,
    RunRecord,
    ThreadCreateRequest,
    ThreadRecord,
    utc_now,
)


def test_id_helpers_create_readable_ids() -> None:
    """ID 前缀要能说明对象类型，随机尾巴要保持短而可读。"""

    thread_id = new_thread_id()
    message_id = new_message_id()
    run_id = new_run_id()

    assert re.fullmatch(r"thread_[0-9a-f]{12}", thread_id)
    assert re.fullmatch(r"msg_[0-9a-f]{12}", message_id)
    assert re.fullmatch(r"run_[0-9a-f]{12}", run_id)


def test_id_helpers_do_not_reuse_values_in_a_small_batch() -> None:
    """本地 ID 至少不能在小批量里重复。"""

    ids = {new_thread_id() for _ in range(500)}

    assert len(ids) == 500


def test_thread_create_request_has_optional_title() -> None:
    """创建会话时，前端可以先不传标题。"""

    request = ThreadCreateRequest()

    assert request.title is None


def test_chat_stream_request_defaults_match_first_learning_flow() -> None:
    """最小发送请求只需要 message，其余字段由后端给出默认值。"""

    request = ChatStreamRequest(message="解释一下 SlotFlow")

    assert request.message == "解释一下 SlotFlow"
    assert request.model_name == "deepseek/deepseek-v4-pro"
    assert request.mode == "pro"
    assert request.agent_name == "default"
    assert request.files == []
    assert request.metadata == {}


def test_chat_stream_request_rejects_blank_message() -> None:
    """只有空格的提问没有业务意义，应该在进入 agent 前就被挡住。"""

    with pytest.raises(ValidationError, match="message cannot be blank"):
        ChatStreamRequest(message="   ")


def test_chat_stream_request_rejects_unknown_mode() -> None:
    """mode 是后续功能开关的来源，不能接受随手拼出来的字符串。"""

    with pytest.raises(ValidationError):
        ChatStreamRequest(message="hello", mode="turbo")  # type: ignore[arg-type]


def test_default_collections_are_not_shared_between_requests() -> None:
    """files 和 metadata 必须每个请求独立，不能共享同一个 list/dict。"""

    first = ChatStreamRequest(message="first")
    second = ChatStreamRequest(message="second")

    first.files.append("upload-1")
    first.metadata["source"] = "test"

    assert second.files == []
    assert second.metadata == {}


def test_record_timestamps_are_timezone_aware_utc() -> None:
    """记录时间统一用 UTC，后面前端再按用户时区展示。"""

    now = utc_now()
    thread = ThreadRecord(id="thread_test", title="测试")
    message = MessageRecord(id="msg_test", thread_id=thread.id, role="user", content="你好")
    run = RunRecord(
        id="run_test",
        thread_id=thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )

    assert now.tzinfo is UTC
    assert thread.created_at.tzinfo is UTC
    assert thread.updated_at.tzinfo is UTC
    assert message.created_at.tzinfo is UTC
    assert run.created_at.tzinfo is UTC
    assert run.updated_at.tzinfo is UTC


def test_message_record_keeps_metadata_independent() -> None:
    """metadata 以后会放工具调用、引用来源等信息，每条消息必须单独保存。"""

    first = MessageRecord(id="msg_1", thread_id="thread_1", role="assistant", content="A")
    second = MessageRecord(id="msg_2", thread_id="thread_1", role="assistant", content="B")

    first.metadata["tool"] = "search"

    assert second.metadata == {}


def test_run_record_starts_as_queued() -> None:
    """run 创建时只是排队状态，真正开始流式输出后才会变成 running。"""

    run = RunRecord(
        id="run_test",
        thread_id="thread_test",
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )

    assert run.status == "queued"
    assert run.error is None


def test_run_config_bundle_keeps_config_and_context_separate() -> None:
    """config 和 context 在模型层保持独立。"""

    context = RunContext(
        thread_id="thread_1",
        run_id="run_1",
        model_name="fake-model",
        mode="ultra",
        agent_name="default",
        files=[],
        thinking_enabled=True,
        is_plan_mode=True,
        subagent_enabled=True,
    )
    bundle = RunConfigBundle(
        config={"configurable": {"thread_id": "thread_1"}},
        context=context,
    )

    assert bundle.config["configurable"]["thread_id"] == "thread_1"
    assert bundle.context.subagent_enabled is True
    assert bundle.config["configurable"]["thread_id"] == bundle.context.thread_id
