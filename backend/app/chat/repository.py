"""chat thread / message / run 仓库。

这些 thread、message、run 在进入数据库之前，先放在哪里？

默认实现先用内存字典，同时保留 SQLite 实现。这样可以让路由层依赖清晰的
仓库边界，而不是直接绑死某种存储：

- thread 是会话容器；
- message 属于某个 thread；
- run 也属于某个 thread；
- 新增 message 或更新 run 时，thread 的 updated_at 要跟着变化。

以后切换默认存储时，只需要替换实现和注入点。
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable, Literal, Protocol

from app.chat.ids import new_message_id, new_run_id, new_thread_id
from app.chat.models import (
    ChatMode,
    MessageRecord,
    MessageRole,
    RunRecord,
    RunStatus,
    ThreadSearchResultRecord,
    ThreadRecord,
    utc_now,
)


ChatRepositoryBackend = Literal["memory", "sqlite"]
DEFAULT_SQLITE_REPOSITORY_PATH = Path(".slotflow/chat.sqlite3")


@dataclass(frozen=True, slots=True)
class ChatRepositoryConfig:
    """应用启动阶段选择 chat 仓库实现所需的最小配置。"""

    backend: ChatRepositoryBackend = "memory"
    sqlite_path: Path = DEFAULT_SQLITE_REPOSITORY_PATH


class ChatRepositoryError(Exception):
    """chat 仓库相关错误的基类。"""


class ThreadNotFoundError(ChatRepositoryError):
    """请求的 thread 不存在。

    后面接 FastAPI 时，这个错误会被路由层翻译成 404。仓库层只表达业务事实，
    不直接知道 HTTP 状态码。
    """


class RunNotFoundError(ChatRepositoryError):
    """请求的 run 不存在。"""


class MessageNotFoundError(ChatRepositoryError):
    """请求的 message 不存在。"""


class ChatRepository(Protocol):
    """聊天仓库的最小业务边界。

    当前默认实现还是内存版，但路由和应用启动阶段应该依赖这组行为，而不是绑死某个
    具体存储类。这样后面切到 SQLite / Postgres 时，只需要替换实现和注入点。
    """

    def create_thread(self, *, title: str | None = None) -> ThreadRecord: ...

    def list_threads(self) -> list[ThreadRecord]: ...

    def get_thread(self, thread_id: str) -> ThreadRecord: ...

    def delete_thread(self, thread_id: str) -> None: ...

    def update_thread_title(self, thread_id: str, title: str) -> ThreadRecord: ...

    def search_threads(self, query: str, *, limit: int = 20) -> list[ThreadSearchResultRecord]: ...

    def add_message(
        self,
        thread_id: str,
        *,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> MessageRecord: ...

    def list_messages(self, thread_id: str) -> list[MessageRecord]: ...

    def update_message_content(
        self,
        thread_id: str,
        message_id: str,
        *,
        content: str,
    ) -> MessageRecord: ...

    def delete_messages_after(self, thread_id: str, message_id: str) -> int: ...

    def create_run(
        self,
        thread_id: str,
        *,
        model_name: str,
        mode: ChatMode,
        agent_name: str,
    ) -> RunRecord: ...

    def list_runs(self, thread_id: str) -> list[RunRecord]: ...

    def get_run(self, run_id: str) -> RunRecord: ...

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: str | None = None,
    ) -> RunRecord: ...


class ChatRecordCopyMixin:
    @staticmethod
    def _copy_thread(thread: ThreadRecord) -> ThreadRecord:
        return thread.model_copy(deep=True)

    @staticmethod
    def _copy_message(message: MessageRecord) -> MessageRecord:
        return message.model_copy(deep=True)

    @staticmethod
    def _copy_run(run: RunRecord) -> RunRecord:
        return run.model_copy(deep=True)


class InMemoryChatRepository(ChatRecordCopyMixin):
    """用内存字典保存 thread / message / run 的仓库。

    这个类故意保持同步接口。FastAPI 的路由可以在 async 函数里直接调用它，
    因为这些操作只是本地内存读写，不会阻塞网络或磁盘。

    这里使用 `RLock` 是为了让“检查 thread 存在 -> 写入 message -> 更新 thread”
    这类组合操作保持原子。它不是分布式锁，只保护当前 Python 进程里的内存状态。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._threads: dict[str, ThreadRecord] = {}  # thread_id -> ThreadRecord
        self._messages_by_thread: dict[str, list[MessageRecord]] = {}
        self._runs: dict[str, RunRecord] = {}  # run_id -> RunRecord
        self._run_ids_by_thread: dict[str, list[str]] = {}  # thread_id -> run_id 列表

    def create_thread(self, *, title: str | None = None) -> ThreadRecord:
        """创建一条新会话。

        如果前端还没有标题，就用“新会话”占位。后面真实 agent 生成自动标题时，
        可以再通过更新 thread 标题的接口替换它。
        """

        clean_title = title.strip() if title else ""
        thread = ThreadRecord(id=new_thread_id(), title=clean_title or "新会话")

        with self._lock:
            self._threads[thread.id] = thread
            self._messages_by_thread[thread.id] = []
            self._run_ids_by_thread[thread.id] = []
            return self._copy_thread(thread)

    def list_threads(self) -> list[ThreadRecord]:
        """列出所有会话，最近更新的排在前面。

        聊天产品的侧边栏一般按最新活动排序。这个规则现在写进仓库测试，后面换
        数据库实现时也要保持一致。
        """

        with self._lock:
            threads = sorted(
                self._threads.values(),
                key=lambda thread: thread.updated_at,
                reverse=True,
            )
            return [self._copy_thread(thread) for thread in threads]

    def get_thread(self, thread_id: str) -> ThreadRecord:
        """读取一条 thread，不存在时抛出明确错误。"""

        with self._lock:
            return self._copy_thread(self._require_thread(thread_id))

    def delete_thread(self, thread_id: str) -> None:
        """删除一条 thread 以及它下面的 messages / runs。"""

        with self._lock:
            self._require_thread(thread_id)
            for run_id in self._run_ids_by_thread.get(thread_id, []):
                self._runs.pop(run_id, None)
            self._run_ids_by_thread.pop(thread_id, None)
            self._messages_by_thread.pop(thread_id, None)
            self._threads.pop(thread_id, None)

    def update_thread_title(self, thread_id: str, title: str) -> ThreadRecord:
        """Update the display title for an existing thread."""

        clean_title = title.strip()
        if not clean_title:
            return self.get_thread(thread_id)

        with self._lock:
            thread = self._require_thread(thread_id)
            thread.title = clean_title
            self._touch_thread(thread_id)
            return self._copy_thread(thread)

    def search_threads(self, query: str, *, limit: int = 20) -> list[ThreadSearchResultRecord]:
        """Search thread titles and stored message content."""

        with self._lock:
            return search_thread_records(
                query=query,
                threads=[self._copy_thread(thread) for thread in self._threads.values()],
                messages_for_thread=lambda thread: [
                    self._copy_message(message)
                    for message in self._messages_by_thread.get(thread.id, [])
                ],
                limit=limit,
            )

    def add_message(
        self,
        thread_id: str,
        *,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> MessageRecord:
        """往 thread 里追加一条消息。

        仓库不负责判断这条消息来自用户还是 assistant，只负责保存。调用方通过
        `role` 明确告诉仓库消息身份。
        """

        with self._lock:
            self._require_thread(thread_id)
            message = MessageRecord(
                id=new_message_id(),
                thread_id=thread_id,
                role=role,
                content=content,
                run_id=run_id,
                metadata=metadata or {},
            )
            self._messages_by_thread[thread_id].append(message)
            self._touch_thread(thread_id)
            return self._copy_message(message)

    def list_messages(self, thread_id: str) -> list[MessageRecord]:
        """按写入顺序列出 thread 下的消息。"""

        with self._lock:
            self._require_thread(thread_id)
            return [self._copy_message(message) for message in self._messages_by_thread[thread_id]]

    def update_message_content(
        self,
        thread_id: str,
        message_id: str,
        *,
        content: str,
    ) -> MessageRecord:
        """更新一条已有消息正文。"""

        with self._lock:
            self._require_thread(thread_id)
            for message in self._messages_by_thread[thread_id]:
                if message.id == message_id:
                    message.content = content
                    self._touch_thread(thread_id)
                    return self._copy_message(message)
        raise MessageNotFoundError(f"message not found: {message_id}")

    def delete_messages_after(self, thread_id: str, message_id: str) -> int:
        """删除某条消息之后的所有消息，保留锚点消息。"""

        with self._lock:
            self._require_thread(thread_id)
            messages = self._messages_by_thread[thread_id]
            for index, message in enumerate(messages):
                if message.id == message_id:
                    deleted_count = len(messages) - index - 1
                    if deleted_count:
                        del messages[index + 1 :]
                        self._touch_thread(thread_id)
                    return deleted_count
        raise MessageNotFoundError(f"message not found: {message_id}")

    def create_run(
        self,
        thread_id: str,
        *,
        model_name: str,
        mode: ChatMode,
        agent_name: str,
    ) -> RunRecord:
        """为某个 thread 创建一次执行记录。

        run 刚创建时是 `queued`。流式执行开始后，才会把它更新为
        `running`、`completed` 或 `failed`。
        """

        with self._lock:
            self._require_thread(thread_id)
            run = RunRecord(
                id=new_run_id(),
                thread_id=thread_id,
                model_name=model_name,
                mode=mode,
                agent_name=agent_name,
            )
            self._runs[run.id] = run
            self._run_ids_by_thread[thread_id].append(run.id)
            self._touch_thread(thread_id)
            return self._copy_run(run)

    def list_runs(self, thread_id: str) -> list[RunRecord]:
        """按创建顺序列出 thread 下的 run。"""

        with self._lock:
            self._require_thread(thread_id)
            return [self._copy_run(self._runs[run_id]) for run_id in self._run_ids_by_thread[thread_id]]

    def get_run(self, run_id: str) -> RunRecord:
        """读取一次 run，不存在时抛出明确错误。"""

        with self._lock:
            return self._copy_run(self._require_run(run_id))

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: str | None = None,
    ) -> RunRecord:
        """更新 run 状态。

        `error` 只在失败时有值。这里不强行根据 status 推断 error，因为后面可能有
        取消、重试等状态；调用方应该明确告诉仓库最终要保存什么。
        """

        with self._lock:
            run = self._require_run(run_id)
            run.status = status
            run.error = error
            run.updated_at = utc_now()
            self._touch_thread(run.thread_id)
            return self._copy_run(run)

    def _require_thread(self, thread_id: str) -> ThreadRecord:
        """在锁内读取 thread；不存在就抛出仓库错误。"""

        try:
            return self._threads[thread_id]
        except KeyError as exc:
            raise ThreadNotFoundError(f"thread not found: {thread_id}") from exc

    def _require_run(self, run_id: str) -> RunRecord:
        """在锁内读取 run；不存在就抛出仓库错误。"""

        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(f"run not found: {run_id}") from exc

    def _touch_thread(self, thread_id: str) -> None:
        """更新 thread 的活动时间。

        message 和 run 都是 thread 下的活动。只要它们变化，侧边栏里的 thread 排序
        就应该跟着更新。
        """

        self._threads[thread_id].updated_at = utc_now()

class SQLiteChatRepository(ChatRecordCopyMixin):
    """用 SQLite 保存 thread / message / run 的仓库。

    它实现和 `InMemoryChatRepository` 相同的同步接口。SQLite 操作会碰磁盘，但当前
    仓库调用都很小，先保持同步边界可以让 FastAPI 路由和测试不用改变形状。
    """

    def __init__(self, database_path: str | Path = DEFAULT_SQLITE_REPOSITORY_PATH) -> None:
        self.database_path = Path(database_path)
        self._database_path_text = str(database_path)
        self._lock = RLock()

        if self._database_path_text != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(
            self._database_path_text,
            check_same_thread=False,
            timeout=30.0,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        if self._database_path_text != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def close(self) -> None:
        """关闭 SQLite 连接。测试或脚本显式释放文件句柄时可以调用。"""

        with self._lock:
            self._connection.close()

    def create_thread(self, *, title: str | None = None) -> ThreadRecord:
        """创建一条新会话，并写入 SQLite。"""

        clean_title = title.strip() if title else ""
        thread = ThreadRecord(id=new_thread_id(), title=clean_title or "新会话")

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO threads (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    thread.id,
                    thread.title,
                    self._datetime_to_text(thread.created_at),
                    self._datetime_to_text(thread.updated_at),
                ),
            )
            return self._copy_thread(thread)

    def list_threads(self) -> list[ThreadRecord]:
        """列出所有会话，最近更新的排在前面。"""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM threads
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """
            ).fetchall()
            return [self._row_to_thread(row) for row in rows]

    def get_thread(self, thread_id: str) -> ThreadRecord:
        """读取一条 thread，不存在时抛出明确错误。"""

        with self._lock:
            return self._row_to_thread(self._fetch_thread_row(thread_id))

    def delete_thread(self, thread_id: str) -> None:
        """删除一条 thread，SQLite 外键会级联删除 messages / runs。"""

        with self._lock, self._connection:
            self._fetch_thread_row(thread_id)
            self._connection.execute(
                "DELETE FROM threads WHERE id = ?",
                (thread_id,),
            )

    def update_thread_title(self, thread_id: str, title: str) -> ThreadRecord:
        """Update the display title for an existing thread."""

        clean_title = title.strip()
        if not clean_title:
            return self.get_thread(thread_id)

        with self._lock, self._connection:
            self._fetch_thread_row(thread_id)
            updated_at = utc_now()
            self._connection.execute(
                """
                UPDATE threads
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_title, self._datetime_to_text(updated_at), thread_id),
            )
            return self._row_to_thread(self._fetch_thread_row(thread_id))

    def search_threads(self, query: str, *, limit: int = 20) -> list[ThreadSearchResultRecord]:
        """Search thread titles and stored message content."""

        with self._lock:
            return search_thread_records(
                query=query,
                threads=self.list_threads(),
                messages_for_thread=lambda thread: self.list_messages(thread.id),
                limit=limit,
            )

    def add_message(
        self,
        thread_id: str,
        *,
        role: MessageRole,
        content: str,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> MessageRecord:
        """往 thread 里追加一条消息，并推进 thread 更新时间。"""

        with self._lock, self._connection:
            self._fetch_thread_row(thread_id)
            message = MessageRecord(
                id=new_message_id(),
                thread_id=thread_id,
                role=role,
                content=content,
                run_id=run_id,
                metadata=metadata or {},
            )
            self._connection.execute(
                """
                INSERT INTO messages (
                    id, thread_id, role, content, run_id, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.thread_id,
                    message.role,
                    message.content,
                    message.run_id,
                    self._metadata_to_text(message.metadata),
                    self._datetime_to_text(message.created_at),
                ),
            )
            self._touch_thread(thread_id)
            return self._copy_message(message)

    def list_messages(self, thread_id: str) -> list[MessageRecord]:
        """按写入顺序列出 thread 下的消息。"""

        with self._lock:
            self._fetch_thread_row(thread_id)
            rows = self._connection.execute(
                """
                SELECT id, thread_id, role, content, run_id, metadata_json, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY sequence ASC
                """,
                (thread_id,),
            ).fetchall()
            return [self._row_to_message(row) for row in rows]

    def update_message_content(
        self,
        thread_id: str,
        message_id: str,
        *,
        content: str,
    ) -> MessageRecord:
        """更新一条已有消息正文，并推进 thread 更新时间。"""

        with self._lock, self._connection:
            self._fetch_message_row(thread_id, message_id)
            self._connection.execute(
                """
                UPDATE messages
                SET content = ?
                WHERE thread_id = ? AND id = ?
                """,
                (content, thread_id, message_id),
            )
            self._touch_thread(thread_id)
            return self._row_to_message(self._fetch_message_row(thread_id, message_id))

    def delete_messages_after(self, thread_id: str, message_id: str) -> int:
        """删除某条消息之后的所有消息，保留锚点消息。"""

        with self._lock, self._connection:
            row = self._fetch_message_row(thread_id, message_id)
            cursor = self._connection.execute(
                """
                DELETE FROM messages
                WHERE thread_id = ? AND sequence > ?
                """,
                (thread_id, row["sequence"]),
            )
            deleted_count = cursor.rowcount if cursor.rowcount is not None else 0
            if deleted_count:
                self._touch_thread(thread_id)
            return deleted_count

    def create_run(
        self,
        thread_id: str,
        *,
        model_name: str,
        mode: ChatMode,
        agent_name: str,
    ) -> RunRecord:
        """为某个 thread 创建一次执行记录。"""

        with self._lock, self._connection:
            self._fetch_thread_row(thread_id)
            run = RunRecord(
                id=new_run_id(),
                thread_id=thread_id,
                model_name=model_name,
                mode=mode,
                agent_name=agent_name,
            )
            self._connection.execute(
                """
                INSERT INTO runs (
                    id, thread_id, status, model_name, mode, agent_name,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.thread_id,
                    run.status,
                    run.model_name,
                    run.mode,
                    run.agent_name,
                    run.error,
                    self._datetime_to_text(run.created_at),
                    self._datetime_to_text(run.updated_at),
                ),
            )
            self._touch_thread(thread_id)
            return self._copy_run(run)

    def list_runs(self, thread_id: str) -> list[RunRecord]:
        """按创建顺序列出 thread 下的 run。"""

        with self._lock:
            self._fetch_thread_row(thread_id)
            rows = self._connection.execute(
                """
                SELECT id, thread_id, status, model_name, mode, agent_name,
                    error, created_at, updated_at
                FROM runs
                WHERE thread_id = ?
                ORDER BY sequence ASC
                """,
                (thread_id,),
            ).fetchall()
            return [self._row_to_run(row) for row in rows]

    def get_run(self, run_id: str) -> RunRecord:
        """读取一次 run，不存在时抛出明确错误。"""

        with self._lock:
            return self._row_to_run(self._fetch_run_row(run_id))

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: str | None = None,
    ) -> RunRecord:
        """更新 run 状态，并推进所属 thread 更新时间。"""

        with self._lock, self._connection:
            run_row = self._fetch_run_row(run_id)
            updated_at = utc_now()
            self._connection.execute(
                """
                UPDATE runs
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, self._datetime_to_text(updated_at), run_id),
            )
            self._touch_thread(str(run_row["thread_id"]))
            return self._row_to_run(self._fetch_run_row(run_id))

    def _initialize_schema(self) -> None:
        """创建 chat 仓库需要的 SQLite 表结构。"""

        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_id TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_thread_sequence
                    ON messages(thread_id, sequence);

                CREATE TABLE IF NOT EXISTS runs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_runs_thread_sequence
                    ON runs(thread_id, sequence);

                CREATE INDEX IF NOT EXISTS idx_threads_updated_at
                    ON threads(updated_at DESC);
                """
            )

    def _fetch_thread_row(self, thread_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM threads
            WHERE id = ?
            """,
            (thread_id,),
        ).fetchone()
        if row is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return row

    def _fetch_run_row(self, run_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT id, thread_id, status, model_name, mode, agent_name,
                error, created_at, updated_at
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return row

    def _fetch_message_row(self, thread_id: str, message_id: str) -> sqlite3.Row:
        self._fetch_thread_row(thread_id)
        row = self._connection.execute(
            """
            SELECT sequence, id, thread_id, role, content, run_id, metadata_json, created_at
            FROM messages
            WHERE thread_id = ? AND id = ?
            """,
            (thread_id, message_id),
        ).fetchone()
        if row is None:
            raise MessageNotFoundError(f"message not found: {message_id}")
        return row

    def _touch_thread(self, thread_id: str) -> None:
        self._connection.execute(
            """
            UPDATE threads
            SET updated_at = ?
            WHERE id = ?
            """,
            (self._datetime_to_text(utc_now()), thread_id),
        )

    @staticmethod
    def _row_to_thread(row: sqlite3.Row) -> ThreadRecord:
        return ThreadRecord(
            id=str(row["id"]),
            title=str(row["title"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MessageRecord:
        metadata = json.loads(str(row["metadata_json"]))
        return MessageRecord(
            id=str(row["id"]),
            thread_id=str(row["thread_id"]),
            role=row["role"],
            content=str(row["content"]),
            run_id=row["run_id"],
            metadata=metadata,
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=str(row["id"]),
            thread_id=str(row["thread_id"]),
            status=row["status"],
            model_name=str(row["model_name"]),
            mode=row["mode"],
            agent_name=str(row["agent_name"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _metadata_to_text(metadata: dict) -> str:
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _datetime_to_text(value) -> str:
        return value.isoformat()

def load_chat_repository_config_from_env() -> ChatRepositoryConfig:
    """从环境变量读取 chat 仓库配置。"""

    backend = os.environ.get("SLOTFLOW_CHAT_REPOSITORY_BACKEND", "memory").strip().lower()
    sqlite_path_value = os.environ.get("SLOTFLOW_CHAT_SQLITE_PATH")
    sqlite_path = (
        Path(sqlite_path_value.strip())
        if sqlite_path_value and sqlite_path_value.strip()
        else DEFAULT_SQLITE_REPOSITORY_PATH
    )

    if backend == "memory":
        return ChatRepositoryConfig(backend="memory", sqlite_path=sqlite_path)
    if backend == "sqlite":
        return ChatRepositoryConfig(backend="sqlite", sqlite_path=sqlite_path)
    raise ValueError(
        "SLOTFLOW_CHAT_REPOSITORY_BACKEND must be 'memory' or 'sqlite', "
        f"got {backend!r}",
    )


def build_chat_repository(config: ChatRepositoryConfig | None = None) -> ChatRepository:
    """按配置创建 chat 仓库实现。"""

    resolved = config or load_chat_repository_config_from_env()
    if resolved.backend == "memory":
        return InMemoryChatRepository()
    if resolved.backend == "sqlite":
        return SQLiteChatRepository(resolved.sqlite_path)
    raise ValueError(f"unsupported chat repository backend: {resolved.backend!r}")


def normalize_search_query(query: str) -> str:
    return " ".join(query.casefold().split())


def search_thread_records(
    *,
    query: str,
    threads: Iterable[ThreadRecord],
    messages_for_thread: Callable[[ThreadRecord], Iterable[MessageRecord]],
    limit: int,
) -> list[ThreadSearchResultRecord]:
    normalized_query = normalize_search_query(query)
    if not normalized_query:
        return []

    results: list[ThreadSearchResultRecord] = []
    for thread in threads:
        if search_text_matches(thread.title, normalized_query):
            results.append(
                ThreadSearchResultRecord(
                    thread=thread,
                    match_type="title",
                    snippet=build_search_snippet(thread.title, normalized_query),
                    score=100,
                )
            )
        for message in messages_for_thread(thread):
            if search_text_matches(message.content, normalized_query):
                results.append(
                    ThreadSearchResultRecord(
                        thread=thread,
                        message=message,
                        match_type="message",
                        snippet=build_search_snippet(message.content, normalized_query),
                        score=80 if message.role == "user" else 60,
                    )
                )

    results.sort(
        key=lambda result: (
            result.score,
            result.message.created_at if result.message else result.thread.updated_at,
        ),
        reverse=True,
    )
    return results[: max(1, limit)]


def search_text_matches(text: str, normalized_query: str) -> bool:
    return normalized_query in " ".join(text.casefold().split())


def build_search_snippet(text: str, normalized_query: str, *, radius: int = 56) -> str:
    compact = " ".join(text.split())
    folded = compact.casefold()
    index = folded.find(normalized_query)
    if index < 0:
        return compact[: radius * 2].strip()

    start = max(0, index - radius)
    end = min(len(compact), index + len(normalized_query) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"
