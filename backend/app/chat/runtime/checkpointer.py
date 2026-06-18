"""LangGraph checkpointer construction for the SlotFlow runtime.

SlotFlow 用 async API 流式跑 graph，所以持久化后端（SQLite/Postgres）必须走
`create_async_checkpointer`；同步工厂只负责 none / memory。
"""

from __future__ import annotations

import inspect
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import InMemorySaver

if TYPE_CHECKING:
    from langgraph.types import Checkpointer

    from app.chat.runtime.config import SlotFlowRuntimeConfig


def create_checkpointer(
    runtime_config: SlotFlowRuntimeConfig,
) -> Checkpointer | None:
    """创建同步可用的 LangGraph checkpointer。"""

    if runtime_config.checkpointer_backend == "none":
        return None
    if runtime_config.checkpointer_backend == "memory":
        return InMemorySaver()
    if runtime_config.checkpointer_backend in ("sqlite", "postgres"):
        raise ValueError(
            "persistent checkpointer backends require create_async_checkpointer() "
            "because SlotFlow streams LangGraph with async APIs",
        )
    raise ValueError(f"unknown checkpointer backend: {runtime_config.checkpointer_backend!r}")


async def create_async_checkpointer(
    runtime_config: SlotFlowRuntimeConfig,
) -> Checkpointer | None:
    """创建 async graph stream 可用的 LangGraph checkpointer。"""

    if runtime_config.checkpointer_backend in ("none", "memory"):
        return create_checkpointer(runtime_config)
    if runtime_config.checkpointer_backend == "sqlite":
        return await create_sqlite_checkpointer(
            runtime_config.checkpointer_sqlite_path,
            setup=runtime_config.checkpointer_setup,
        )
    if runtime_config.checkpointer_backend == "postgres":
        if runtime_config.checkpointer_postgres_uri is None:
            raise ValueError(
                "SLOTFLOW_CHECKPOINTER_POSTGRES_URI is required when "
                "SLOTFLOW_CHECKPOINTER_BACKEND=postgres",
            )
        return await create_postgres_checkpointer(
            runtime_config.checkpointer_postgres_uri,
            setup=runtime_config.checkpointer_setup,
        )
    raise ValueError(f"unknown checkpointer backend: {runtime_config.checkpointer_backend!r}")


async def create_sqlite_checkpointer(
    database_path: str | Path,
    *,
    setup: bool = True,
) -> Checkpointer:
    """创建 LangGraph Async SQLite checkpointer。"""

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    database_path_text = str(database_path)
    if database_path_text != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    connection = await aiosqlite.connect(database_path_text)
    checkpointer = AsyncSqliteSaver(connection)
    if setup:
        await checkpointer.setup()
    return checkpointer


async def create_postgres_checkpointer(
    conn_string: str,
    *,
    setup: bool = True,
) -> Checkpointer:
    """创建 LangGraph Async Postgres checkpointer。"""

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    connection = await AsyncConnection.connect(
        conn_string,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    checkpointer = AsyncPostgresSaver(connection)
    if setup:
        await checkpointer.setup()
    return checkpointer


def close_checkpointer(checkpointer: Checkpointer | None) -> None:
    """关闭官方 saver 持有的底层数据库连接。"""

    if checkpointer is None:
        return

    connection = getattr(checkpointer, "conn", None)
    close = getattr(connection, "close", None)
    if callable(close) and not inspect.iscoroutinefunction(close):
        with suppress(Exception):
            close()


async def aclose_checkpointer(checkpointer: Checkpointer | None) -> None:
    """异步关闭官方 saver 持有的底层数据库连接。"""

    if checkpointer is None:
        return

    connection = getattr(checkpointer, "conn", None)
    close = getattr(connection, "close", None)
    if callable(close):
        with suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                await result
