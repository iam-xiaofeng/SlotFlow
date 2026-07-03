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
        normalized_content = normalize_memory_content(content, kind=validated_kind)
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
        normalized_content = normalize_memory_content(
            content,
            kind=validated_kind,
        )
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
        normalized_content = normalize_memory_content(content, kind=next_kind)
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
    kind: MemoryKind | None = None,
    max_chars: int = 1600,
) -> str:
    compact = canonicalize_memory_content(kind, strip_memory_command_prefix(content))
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


def canonicalize_memory_content(kind: MemoryKind | None, content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip(" ：:")
    if not compact:
        return ""

    if kind == "preference":
        return canonical_prefixed_sentence(
            "用户的偏好是：",
            strip_subject_prefix(compact),
        )
    if kind == "profile":
        return canonicalize_profile_memory(compact)
    if kind == "topic":
        return canonical_prefixed_sentence(
            "用户近期关注：",
            strip_subject_prefix(compact),
        )
    if kind == "fact":
        return canonicalize_fact_memory(compact)
    if kind == "manual":
        return canonical_prefixed_sentence("用户记录：", compact)
    return ensure_chinese_sentence(compact)


def canonical_prefixed_sentence(prefix: str, content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip(" ：:")
    if compact.startswith(prefix):
        return ensure_chinese_sentence(compact)
    return ensure_chinese_sentence(f"{prefix}{compact}")


def canonicalize_profile_memory(content: str) -> str:
    if content.startswith("用户资料："):
        return ensure_chinese_sentence(content)
    if re.search(r"用户的(?:姓名|职业|专业|生日)是", content):
        return ensure_chinese_sentence(content)

    fields: list[str] = []
    name = extract_first_match(
        content,
        [
            r"(?:我叫|我的名字是|用户叫|用户的姓名是)([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_.·-]{0,20})",
        ],
    )
    if name:
        fields.append(f"用户的姓名是{name}")

    profession = extract_first_match(
        content,
        [
            r"(?:职业是|工作是|身份是|用户的职业是)([^，。；;、]{1,30})",
        ],
    )
    if profession:
        fields.append(f"用户的职业是{strip_subject_prefix(profession)}")

    major = extract_first_match(
        content,
        [
            r"(?:专业是|专业为|用户的专业是)([^，。；;、]{1,30})",
        ],
    )
    if major:
        fields.append(f"用户的专业是{strip_subject_prefix(major)}")

    birthday = extract_birthday(content)
    if birthday:
        fields.append(f"用户的生日是{birthday}")

    if fields:
        return "。".join(fields) + "。"
    return canonical_prefixed_sentence("用户资料：", strip_subject_prefix(content))


def canonicalize_fact_memory(content: str) -> str:
    if content.startswith("用户事实：") or re.search(r"用户的.+是", content):
        return ensure_chinese_sentence(content)

    birthday = extract_birthday(content)
    if birthday:
        return ensure_chinese_sentence(f"用户的生日是{birthday}")
    return canonical_prefixed_sentence("用户事实：", strip_subject_prefix(content))


def strip_subject_prefix(content: str) -> str:
    cleaned = content.strip(" ：:")
    replacements = [
        (r"^用户(?:的)?", ""),
        (r"^(?:偏好|喜好)(?:是|为)?", ""),
        (r"^(?:资料|事实|近期关注|记录)", ""),
        (r"^我希望", ""),
        (r"^我更喜欢", "喜欢"),
        (r"^我喜欢", "喜欢"),
        (r"^我是", ""),
        (r"^我叫", ""),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned).strip(" ：:")
    return cleaned


def extract_birthday(content: str) -> str | None:
    match = re.search(r"((?:农历|公历)?\s*\d{1,2}月\d{1,2}日)(?:是)?(?:我|用户)?的?生日", content)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    match = re.search(r"(?:我|用户)?的?生日(?:是|为)\s*((?:农历|公历)?\s*\d{1,2}月\d{1,2}日)", content)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    return None


def extract_first_match(content: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            value = match.group(1).strip(" ：:，。；;、")
            if value:
                return value
    return None


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
