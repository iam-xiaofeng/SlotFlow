"""内存版 chat 仓库。

模块一只定义了数据“长什么样”。模块二开始回答另一个问题：
这些 thread、message、run 在进入数据库之前，先放在哪里？

第一阶段我们先用内存字典，不马上引入 SQLite / Postgres。这样做不是为了逃避
持久化，而是为了把核心业务边界看清楚：

- thread 是会话容器；
- message 属于某个 thread；
- run 也属于某个 thread；
- 新增 message 或更新 run 时，thread 的 updated_at 要跟着变化。

等后续链路跑通，再把这个类替换成数据库实现会更稳，因为 API 和测试已经先固定了。
"""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from app.chat.ids import new_message_id, new_run_id, new_thread_id
from app.chat.models import (
    ChatMode,
    MessageRecord,
    MessageRole,
    RunRecord,
    RunStatus,
    ThreadRecord,
    utc_now,
)


class ChatRepositoryError(Exception):
    """chat 仓库相关错误的基类。"""


class ThreadNotFoundError(ChatRepositoryError):
    """请求的 thread 不存在。

    后面接 FastAPI 时，这个错误会被路由层翻译成 404。仓库层只表达业务事实，
    不直接知道 HTTP 状态码。
    """


class RunNotFoundError(ChatRepositoryError):
    """请求的 run 不存在。"""


class ChatRepository(Protocol):
    """聊天仓库的最小业务边界。

    当前默认实现还是内存版，但路由和应用启动阶段应该依赖这组行为，而不是绑死某个
    具体存储类。这样后面切到 SQLite / Postgres 时，只需要替换实现和注入点。
    """

    def create_thread(self, *, title: str | None = None) -> ThreadRecord: ...

    def list_threads(self) -> list[ThreadRecord]: ...

    def get_thread(self, thread_id: str) -> ThreadRecord: ...

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


class InMemoryChatRepository:
    """用内存字典保存 thread / message / run 的仓库。

    这个类故意保持同步接口。FastAPI 的路由可以在 async 函数里直接调用它，
    因为这些操作只是本地内存读写，不会阻塞网络或磁盘。

    这里使用 `RLock` 是为了让“检查 thread 存在 -> 写入 message -> 更新 thread”
    这类组合操作保持原子。它不是分布式锁，只保护当前 Python 进程里的内存状态。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._threads: dict[str, ThreadRecord] = {} # thread_id -> ThreadRecord
        self._messages_by_thread: dict[str, list[MessageRecord]] = {}
        self._runs: dict[str, RunRecord] = {}  # run_id -> RunRecord
        self._run_ids_by_thread: dict[str, list[str]] = {} # thread_id -> run_id 列表

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

    def create_run(
        self,
        thread_id: str,
        *,
        model_name: str,
        mode: ChatMode,
        agent_name: str,
    ) -> RunRecord:
        """为某个 thread 创建一次执行记录。

        run 刚创建时是 `queued`。模块四/五开始流式执行后，才会把它更新为
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

    @staticmethod
    def _copy_thread(thread: ThreadRecord) -> ThreadRecord:
        """返回 thread 副本，避免调用方改到仓库内部对象。"""

        return thread.model_copy(deep=True)

    @staticmethod
    def _copy_message(message: MessageRecord) -> MessageRecord:
        """返回 message 副本，避免 metadata 被外部共享修改。"""

        return message.model_copy(deep=True)

    @staticmethod
    def _copy_run(run: RunRecord) -> RunRecord:
        """返回 run 副本，避免调用方绕过仓库修改状态。"""

        return run.model_copy(deep=True)
