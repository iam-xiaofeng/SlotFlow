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
        """Insert a memory record, returning the existing item for duplicate runs."""

        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise ValueError("memory content cannot be blank")
        validated_kind = validate_memory_kind(kind)

        existing = self.get_by_source_run_id(source_run_id) if source_run_id else None
        if existing is not None:
            return existing

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
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise ValueError("memory content cannot be blank")
        next_kind = validate_memory_kind(kind) if kind is not None else existing.kind

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


def normalize_memory_content(content: str, *, max_chars: int = 1600) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1]}..."


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
    return MemoryRecord(
        id=row["id"],
        thread_id=row["thread_id"],
        kind=validate_memory_kind(row["kind"] or "manual"),
        content=row["content"],
        source_run_id=row["source_run_id"],
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"] or row["created_at"],
    )
