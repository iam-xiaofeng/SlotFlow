"""thread/message/run 仓库测试。

这组仓库契约测试覆盖本地 SQLite 实现。这里不碰 agent，原因是仓库本身应该是一个
清楚的小边界：上层告诉它要保存什么，它负责保存并按规则取回。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from time import sleep

import pytest

from app.chat.repository import (
    ChatRepository,
    RunNotFoundError,
    SQLiteChatRepository,
    ThreadNotFoundError,
    build_chat_repository,
)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[ChatRepository]:
    """每个测试拿到一个独立的临时 SQLite 仓库。"""

    sqlite_repo = SQLiteChatRepository(tmp_path / "chat.sqlite3")
    try:
        yield sqlite_repo
    finally:
        sqlite_repo.close()


def test_create_thread_uses_default_title_when_title_is_blank(repo: ChatRepository) -> None:
    """前端还没生成标题时，仓库先给一个稳定的占位标题。"""

    thread = repo.create_thread(title="   ")

    assert thread.id.startswith("thread_")
    assert thread.title == "新会话"
    assert repo.get_thread(thread.id).title == "新会话"


def test_create_thread_strips_title_and_lists_recent_first(repo: ChatRepository) -> None:
    """thread 标题去掉两侧空白；会话列表按最近活动排序。"""

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


def test_update_thread_title_and_search_messages(repo: ChatRepository) -> None:
    thread = repo.create_thread(title="临时标题")
    repo.add_message(thread.id, role="user", content="请解释 LangChain middleware")
    assistant = repo.add_message(thread.id, role="assistant", content="middleware 可以在模型调用前后改写状态")

    updated = repo.update_thread_title(thread.id, "LangChain 中间件")
    title_results = repo.search_threads("中间件")
    message_results = repo.search_threads("改写状态")

    assert updated.title == "LangChain 中间件"
    assert title_results[0].thread.id == thread.id
    assert title_results[0].match_type == "title"
    assert message_results[0].thread.id == thread.id
    assert message_results[0].message is not None
    assert message_results[0].message.id == assistant.id
    assert "改写状态" in message_results[0].snippet


def test_add_and_list_messages_keep_write_order(repo: ChatRepository) -> None:
    """消息按写入顺序保存，这是聊天记录能正确展示的基础。"""

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


def test_update_message_and_delete_later_messages(repo: ChatRepository) -> None:
    """编辑最后一条用户消息时，可以覆盖正文并删除它后面的旧回复。"""

    thread = repo.create_thread()
    first_user = repo.add_message(thread.id, role="user", content="第一问")
    first_assistant = repo.add_message(thread.id, role="assistant", content="第一答")
    second_user = repo.add_message(thread.id, role="user", content="旧问题")
    repo.add_message(thread.id, role="assistant", content="旧回答")

    updated = repo.update_message_content(
        thread.id,
        second_user.id,
        content="新问题",
    )
    deleted_count = repo.delete_messages_after(thread.id, second_user.id)
    messages = repo.list_messages(thread.id)

    assert updated.content == "新问题"
    assert deleted_count == 1
    assert [message.id for message in messages] == [
        first_user.id,
        first_assistant.id,
        second_user.id,
    ]
    assert messages[-1].content == "新问题"


def test_message_operations_fail_for_missing_thread(repo: ChatRepository) -> None:
    """不存在的 thread 不能悄悄创建，否则调用方很难发现传错了 ID。"""

    with pytest.raises(ThreadNotFoundError, match="thread not found"):
        repo.add_message("thread_missing", role="user", content="hello")

    with pytest.raises(ThreadNotFoundError, match="thread not found"):
        repo.list_messages("thread_missing")


def test_delete_thread_removes_messages_and_runs(repo: ChatRepository) -> None:
    """删除 thread 时，其消息和 run 也不能再被读取。"""

    thread = repo.create_thread()
    run = repo.create_run(
        thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )
    repo.add_message(thread.id, role="user", content="hello")

    repo.delete_thread(thread.id)

    assert repo.list_threads() == []
    with pytest.raises(ThreadNotFoundError):
        repo.get_thread(thread.id)
    with pytest.raises(ThreadNotFoundError):
        repo.list_messages(thread.id)
    with pytest.raises(RunNotFoundError):
        repo.get_run(run.id)


def test_create_list_get_and_update_run(repo: ChatRepository) -> None:
    """run 从 queued 开始，随后可以被更新为 running/completed/failed 等状态。"""

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


def test_update_run_can_store_error_message(repo: ChatRepository) -> None:
    """stream 失败时，仓库要保存最终错误，后续 API 才能返回给前端。"""

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


def test_run_operations_fail_for_missing_objects(repo: ChatRepository) -> None:
    """仓库层用明确异常表达缺失对象，后面路由层再翻译成 HTTP 404。"""

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


def test_repository_returns_copies_not_internal_objects(repo: ChatRepository) -> None:
    """调用方拿到的是副本，不能绕过仓库直接改内部状态。"""

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


def test_sqlite_repository_persists_records_across_instances(tmp_path: Path) -> None:
    """SQLite 版重开连接后仍能读回 thread/message/run。"""

    db_path = tmp_path / "chat.sqlite3"
    first_repo = SQLiteChatRepository(db_path)
    thread = first_repo.create_thread(title="持久化会话")
    message = first_repo.add_message(
        thread.id,
        role="assistant",
        content="这条消息会落盘",
        metadata={"source": "sqlite-test"},
    )
    run = first_repo.create_run(
        thread.id,
        model_name="fake-model",
        mode="pro",
        agent_name="default",
    )
    first_repo.update_run_status(run.id, status="completed")
    first_repo.close()

    second_repo = SQLiteChatRepository(db_path)
    try:
        assert second_repo.get_thread(thread.id).title == "持久化会话"
        assert second_repo.list_messages(thread.id)[0].id == message.id
        assert second_repo.list_messages(thread.id)[0].metadata == {"source": "sqlite-test"}
        assert second_repo.get_run(run.id).status == "completed"
    finally:
        second_repo.close()


def test_build_chat_repository_reads_sqlite_path_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """启动入口默认创建 SQLite 仓库，路径来自环境变量。"""

    db_path = tmp_path / "chat.sqlite3"
    monkeypatch.setenv("SLOTFLOW_CHAT_SQLITE_PATH", str(db_path))

    repo = build_chat_repository()
    try:
        assert isinstance(repo, SQLiteChatRepository)
        assert repo.database_path == db_path
    finally:
        repo.close()


def test_build_chat_repository_accepts_explicit_path(tmp_path: Path) -> None:
    """显式传入路径时直接使用该路径。"""

    repo = build_chat_repository(tmp_path / "chat.sqlite3")
    try:
        assert isinstance(repo, SQLiteChatRepository)
    finally:
        repo.close()
