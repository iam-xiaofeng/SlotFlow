"""模块二测试：内存版 thread/message/run 仓库。

模块一只证明“数据盒子”长得对；模块二开始证明这些数据能被保存、读取和更新。

这里仍然不碰 FastAPI，也不碰 agent。原因是仓库本身应该是一个清楚的小边界：
上层告诉它要保存什么，它负责保存并按规则取回。
"""

from __future__ import annotations

from time import sleep

import pytest

from app.chat.repository import InMemoryChatRepository, RunNotFoundError, ThreadNotFoundError


def test_create_thread_uses_default_title_when_title_is_blank() -> None:
    """前端还没生成标题时，仓库先给一个稳定的占位标题。"""

    repo = InMemoryChatRepository()

    thread = repo.create_thread(title="   ")

    assert thread.id.startswith("thread_")
    assert thread.title == "新会话"
    assert repo.get_thread(thread.id).title == "新会话"


def test_create_thread_strips_title_and_lists_recent_first() -> None:
    """thread 标题去掉两侧空白；会话列表按最近活动排序。"""

    repo = InMemoryChatRepository()

    first = repo.create_thread(title="  第一条  ")
    sleep(0.001)
    second = repo.create_thread(title="第二条")
    sleep(0.001)

    threads = repo.list_threads()
    assert [thread.id for thread in threads] == [second.id, first.id]

    repo.add_message(first.id, role="user", content="把第一条重新变成最近活动")

    threads = repo.list_threads()
    assert first.title == "第一条"
    assert [thread.id for thread in threads] == [first.id, second.id]


def test_add_and_list_messages_keep_write_order() -> None:
    """消息按写入顺序保存，这是聊天记录能正确展示的基础。"""

    repo = InMemoryChatRepository()
    thread = repo.create_thread()

    first = repo.add_message(thread.id, role="user", content="你好")
    second = repo.add_message(
        thread.id,
        role="assistant",
        content="你好，我是 SlotFlow",
        run_id="run_demo",
        metadata={"source": "fake-agent"},
    )

    messages = repo.list_messages(thread.id)

    assert [message.id for message in messages] == [first.id, second.id]
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"
    assert messages[1].run_id == "run_demo"
    assert messages[1].metadata == {"source": "fake-agent"}


def test_message_operations_fail_for_missing_thread() -> None:
    """不存在的 thread 不能悄悄创建，否则调用方很难发现传错了 ID。"""

    repo = InMemoryChatRepository()

    with pytest.raises(ThreadNotFoundError, match="thread not found"):
        repo.add_message("thread_missing", role="user", content="hello")

    with pytest.raises(ThreadNotFoundError, match="thread not found"):
        repo.list_messages("thread_missing")


def test_create_list_get_and_update_run() -> None:
    """run 从 queued 开始，随后可以被更新为 running/completed/failed 等状态。"""

    repo = InMemoryChatRepository()
    thread = repo.create_thread()

    run = repo.create_run(
        thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )
    running = repo.update_run_status(run.id, status="running")
    completed = repo.update_run_status(run.id, status="completed")

    runs = repo.list_runs(thread.id)

    assert run.id.startswith("run_")
    assert run.status == "queued"
    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.error is None
    assert repo.get_run(run.id).status == "completed"
    assert [item.id for item in runs] == [run.id]


def test_update_run_can_store_error_message() -> None:
    """stream 失败时，仓库要保存最终错误，后续 API 才能返回给前端。"""

    repo = InMemoryChatRepository()
    thread = repo.create_thread()
    run = repo.create_run(
        thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )

    failed = repo.update_run_status(run.id, status="failed", error="agent crashed")

    assert failed.status == "failed"
    assert failed.error == "agent crashed"


def test_run_operations_fail_for_missing_objects() -> None:
    """仓库层用明确异常表达缺失对象，后面路由层再翻译成 HTTP 404。"""

    repo = InMemoryChatRepository()

    with pytest.raises(ThreadNotFoundError, match="thread not found"):
        repo.create_run(
            "thread_missing",
            model_name="fake-model",
            mode="pro",
            agent_name="default",
        )

    with pytest.raises(RunNotFoundError, match="run not found"):
        repo.get_run("run_missing")

    with pytest.raises(RunNotFoundError, match="run not found"):
        repo.update_run_status("run_missing", status="running")


def test_repository_returns_copies_not_internal_objects() -> None:
    """调用方拿到的是副本，不能绕过仓库直接改内部状态。"""

    repo = InMemoryChatRepository()
    thread = repo.create_thread(title="原始标题")
    message = repo.add_message(thread.id, role="assistant", content="原始回答", metadata={"tool": "none"})
    run = repo.create_run(
        thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )

    thread.title = "外部改坏的标题"
    message.metadata["tool"] = "external-mutation"
    run.status = "failed"

    stored_thread = repo.get_thread(thread.id)
    stored_message = repo.list_messages(thread.id)[0]
    stored_run = repo.get_run(run.id)

    assert stored_thread.title == "原始标题"
    assert stored_message.metadata == {"tool": "none"}
    assert stored_run.status == "queued"
