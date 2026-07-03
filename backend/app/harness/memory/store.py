"""SQLite-backed long-term memory store."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.harness.memory.models import MEMORY_KINDS, MemoryKind, MemoryRecord, utc_now


class MemoryNotFoundError(KeyError):
    """Raised when a memory item cannot be found."""


class SlotFlowMemoryStore:
    """Persist and retrieve compact memory records."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._schema_ready = False
        self._lock = threading.Lock()

    def add_memory(
        self,
        *,
        content: str,
        kind: MemoryKind = "manual",
        thread_id: str | None = None,
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """Insert a memory record, touching existing equivalent memories."""

        validated_kind = validate_memory_kind(kind)
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise ValueError("memory content cannot be blank")

        existing = self.get_by_source_run_id(source_run_id) if source_run_id else None
        if existing is not None:
            return existing

        existing = self.get_by_kind_content(validated_kind, normalized_content)
        if existing is not None:
            return self._touch_memory(
                existing,
                thread_id=thread_id,
                source_run_id=source_run_id,
                metadata=metadata,
            )

        now = utc_now()
        record = MemoryRecord(
            id=new_memory_id(),
            thread_id=thread_id,
            kind=validated_kind,
            content=normalized_content,
            source_run_id=source_run_id,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        self._execute(
            """
            insert into memories (
                id, thread_id, kind, content, source_run_id, metadata_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.thread_id,
                record.kind,
                record.content,
                record.source_run_id,
                json.dumps(record.metadata, ensure_ascii=False),
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )
        return record

    def get_by_kind_content(
        self,
        kind: MemoryKind,
        content: str,
    ) -> MemoryRecord | None:
        """Return an existing memory with the same normalized kind and content."""

        validated_kind = validate_memory_kind(kind)
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            return None
        rows = self._fetchall(
            "select * from memories where kind = ? and content = ? order by updated_at desc limit 1",
            (validated_kind, normalized_content),
        )
        return row_to_memory(rows[0]) if rows else None

    def _touch_memory(
        self,
        existing: MemoryRecord,
        *,
        thread_id: str | None,
        source_run_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> MemoryRecord:
        """Refresh metadata for an existing semantic duplicate without inserting."""

        now = utc_now()
        next_metadata = dict(existing.metadata)
        if metadata:
            next_metadata.update(metadata)
        if source_run_id and source_run_id != existing.source_run_id:
            next_metadata["last_source_run_id"] = source_run_id

        next_source_run_id = existing.source_run_id
        if next_source_run_id is None and source_run_id:
            next_source_run_id = source_run_id

        self._execute(
            """
            update memories
            set thread_id = ?, source_run_id = ?, metadata_json = ?, updated_at = ?
            where id = ?
            """,
            (
                thread_id or existing.thread_id,
                next_source_run_id,
                json.dumps(next_metadata, ensure_ascii=False),
                now.isoformat(),
                existing.id,
            ),
        )
        return self.get_memory(existing.id)

    def list_memories(
        self,
        *,
        thread_id: str | None = None,
        kind: MemoryKind | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        """List recent memories, newest first."""

        bounded_limit = max(1, min(limit, 200))
        filters: list[str] = []
        params: list[Any] = []
        if thread_id:
            filters.append("thread_id = ?")
            params.append(thread_id)
        if kind:
            filters.append("kind = ?")
            params.append(validate_memory_kind(kind))

        where_clause = f"where {' and '.join(filters)}" if filters else ""
        rows = self._fetchall(
            f"""
            select * from memories
            {where_clause}
            order by updated_at desc, created_at desc, id desc
            limit ?
            """,
            (*params, bounded_limit),
        )
        return [row_to_memory(row) for row in rows]

    def search_memories(
        self,
        *,
        query: str,
        thread_id: str | None = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Return relevant memories using a small local keyword scorer."""

        candidates = self.list_memories(limit=200)
        if not candidates:
            return []

        query_terms = tokenize_memory_text(query)
        scored: list[tuple[int, int, MemoryRecord]] = []
        for index, record in enumerate(candidates):
            terms = tokenize_memory_text(record.content)
            overlap = len(query_terms & terms) if query_terms else 0
            thread_bonus = 2 if thread_id and record.thread_id == thread_id else 0
            score = overlap + thread_bonus
            if score > 0 or not query_terms:
                scored.append((score, -index, record))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[: max(1, min(limit, 20))]]

    def get_by_source_run_id(self, source_run_id: str | None) -> MemoryRecord | None:
        """Return a memory created from a run, if one already exists."""

        if not source_run_id:
            return None
        rows = self._fetchall(
            "select * from memories where source_run_id = ? limit 1",
            (source_run_id,),
        )
        return row_to_memory(rows[0]) if rows else None

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str,
        kind: MemoryKind | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """Replace one memory's content and optionally merge metadata."""

        existing = self.get_memory(memory_id)
        next_kind = validate_memory_kind(kind) if kind is not None else existing.kind
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise ValueError("memory content cannot be blank")

        next_metadata = dict(existing.metadata)
        if metadata:
            next_metadata.update(metadata)
        now = utc_now()
        self._execute(
            """
            update memories
            set kind = ?, content = ?, metadata_json = ?, updated_at = ?
            where id = ?
            """,
            (
                next_kind,
                normalized_content,
                json.dumps(next_metadata, ensure_ascii=False),
                now.isoformat(),
                memory_id,
            ),
        )
        return self.get_memory(memory_id)

    def get_memory(self, memory_id: str) -> MemoryRecord:
        """Return one memory by ID."""

        rows = self._fetchall(
            "select * from memories where id = ? limit 1",
            (memory_id,),
        )
        if not rows:
            raise MemoryNotFoundError(memory_id)
        return row_to_memory(rows[0])

    def delete_memory(self, memory_id: str) -> None:
        """Delete one memory by ID."""

        cursor = self._execute("delete from memories where id = ?", (memory_id,))
        if cursor.rowcount == 0:
            raise MemoryNotFoundError(memory_id)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                connection.execute(
                    """
                    create table if not exists memories (
                        id text primary key,
                        thread_id text,
                        kind text not null default 'manual',
                        content text not null,
                        source_run_id text unique,
                        metadata_json text not null default '{}',
                        created_at text not null,
                        updated_at text not null
                    )
                    """
                )
                ensure_memory_column(connection, "kind", "text not null default 'manual'")
                ensure_memory_column(connection, "updated_at", "text")
                connection.execute(
                    "update memories set updated_at = created_at where updated_at is null"
                )
                connection.execute(
                    "create index if not exists idx_memories_thread on memories(thread_id)"
                )
                connection.execute(
                    """
                    create index if not exists idx_memories_created
                    on memories(updated_at desc, created_at desc)
                    """
                )
                connection.execute(
                    "create index if not exists idx_memories_kind on memories(kind)"
                )
            self._schema_ready = True

    def _execute(self, sql: str, params: tuple[Any, ...]) -> sqlite3.Cursor:
        self._ensure_schema()
        with self._connect() as connection:
            return connection.execute(sql, params)

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        self._ensure_schema()
        with self._connect() as connection:
            return list(connection.execute(sql, params).fetchall())


def new_memory_id() -> str:
    return f"mem_{secrets.token_hex(6)}"


def normalize_memory_content(
    content: str,
    *,
    max_chars: int = 1600,
) -> str:
    """写入边界的卫生化：剥指令前缀、压空白、补句号、限长。

    语义改写（第三人称化、要素归纳、风格统一）是 LLM 的职责——``memory_save`` 工具
    的内容由模型撰写、后台抽取由 ``SlotFlowMemoryExtractor`` 改写。store 不做语义
    加工，避免用正则揉搓 LLM 已经写好的内容。
    """

    compact = ensure_chinese_sentence(strip_memory_command_prefix(content))
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1]}..."


def strip_memory_command_prefix(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip(" ：:")
    if not compact:
        return ""

    prefixes = [
        r"^(?:请)?(?:再)?(?:帮我)?(?:记住|保存到记忆|加入记忆|记录一下)[:：]?",
        r"^(?:请)?(?:再)?(?:帮我)?(?:在)?(?:你的|用户的)?长期记忆(?:中|里)?(?:记住|记录)?(?:事实|偏好|资料)?[:：]?",
        r"^(?:事实|偏好|资料|近期|手动)[:：]",
    ]
    cleaned = compact
    changed = True
    while changed:
        changed = False
        for pattern in prefixes:
            next_value = re.sub(pattern, "", cleaned).strip(" ：:")
            if next_value != cleaned:
                cleaned = next_value
                changed = True
    return cleaned


def ensure_chinese_sentence(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip(" ：:")
    if not compact:
        return ""
    if compact.endswith(("。", "！", "？", ".", "!", "?")):
        return compact
    return f"{compact}。"


def validate_memory_kind(kind: str) -> MemoryKind:
    if kind not in MEMORY_KINDS:
        raise ValueError(f"memory kind must be one of {sorted(MEMORY_KINDS)}")
    return kind  # type: ignore[return-value]


def ensure_memory_column(
    connection: sqlite3.Connection,
    name: str,
    definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute("pragma table_info(memories)").fetchall()
    }
    if name not in columns:
        connection.execute(f"alter table memories add column {name} {definition}")


def tokenize_memory_text(value: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]{2,}", value)
        if token.strip()
    }
    for cjk_text in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        tokens.update(cjk_text)
        tokens.update(
            cjk_text[index : index + 2]
            for index in range(len(cjk_text) - 1)
        )
    return tokens


def row_to_memory(row: sqlite3.Row) -> MemoryRecord:
    metadata = json.loads(row["metadata_json"] or "{}")
    kind = validate_memory_kind(row["kind"] or "manual")
    # 规范化只发生在写入边界(add/update);读取返回存储原文,避免规则演进时
    # 每次读取都用新规则改写旧数据的呈现。
    return MemoryRecord(
        id=row["id"],
        thread_id=row["thread_id"],
        kind=kind,
        content=row["content"],
        source_run_id=row["source_run_id"],
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"] or row["created_at"],
    )
