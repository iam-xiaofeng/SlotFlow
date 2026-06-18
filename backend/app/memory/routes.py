"""FastAPI routes for long-term memory inspection."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.dependencies import get_memory_store
from app.harness.memory import MemoryKind, MemoryNotFoundError, MemoryRecord
from app.memory.models import MemoryCreateRequest, MemoryUpdateRequest


router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.get("", response_model=list[MemoryRecord])
async def list_memories(
    request: Request,
    thread_id: str | None = None,
    kind: MemoryKind | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryRecord]:
    """List persisted long-term memories."""

    return get_memory_store(request).list_memories(
        thread_id=thread_id,
        kind=kind,
        limit=limit,
    )


@router.post("", response_model=MemoryRecord)
async def create_memory(body: MemoryCreateRequest, request: Request) -> MemoryRecord:
    """Create a user-managed long-term memory item."""

    try:
        return get_memory_store(request).add_memory(
            thread_id=body.thread_id,
            kind=body.kind,
            content=body.content,
            metadata={
                **body.metadata,
                "source": body.metadata.get("source", "user_memory_api"),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{memory_id}", response_model=MemoryRecord)
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    request: Request,
) -> MemoryRecord:
    """Update one long-term memory item."""

    try:
        return get_memory_store(request).update_memory(
            memory_id,
            kind=body.kind,
            content=body.content,
            metadata={
                **body.metadata,
                "updated_by": body.metadata.get("updated_by", "user_memory_api"),
            },
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, request: Request) -> Response:
    """Delete one long-term memory item."""

    try:
        get_memory_store(request).delete_memory(memory_id)
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory not found") from exc
    return Response(status_code=204)
