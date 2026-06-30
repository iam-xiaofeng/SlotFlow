"""Agent middleware for SlotFlow long-term memory.

Thin delegate to ``app.harness.steps.long_term_memory``. Memory retrieval/injection, explicit
``请记住X`` save, and background LLM extraction logic live in the step module.

Backward-compat re-exports keep the old import surface (``build_turn_memory_content`` etc.)
working for tests that still import from the middleware module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.memory import SlotFlowMemoryStore
from app.harness.memory.extractor import SlotFlowMemoryExtractor
from app.harness.state import SlotFlowAgentState
from app.harness.steps.long_term_memory import (
    MemoryCandidate,
    append_memory_system_message,
    build_extraction_conversation,
    build_memory_prompt,
    build_turn_memory_candidate,
    build_turn_memory_content,
    explicit_save_update,
    extract_explicit_memory,
    latest_message_index,
    latest_user_text,
    maybe_schedule_extraction,
    memory_save_tool_used_for_run,
    normalize_memory_sentence,
    retrieve_memories,
    tool_message_name,
)


class SlotFlowLongTermMemoryMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject relevant persisted memories, save explicit "记住X" turns, and extract other
    durable facts in the background via an LLM (see ``memory.extractor``)."""

    def __init__(
        self,
        *,
        memory_store: SlotFlowMemoryStore,
        run_context: RunContext | None = None,
        tools_enabled: bool = True,
        max_results: int = 5,
        model: Any = None,
        proactive_extraction_enabled: bool = True,
    ) -> None:
        self._memory_store = memory_store
        self._tools_enabled = tools_enabled
        self._max_results = max_results
        self._proactive_extraction_enabled = proactive_extraction_enabled
        self._extractor = SlotFlowMemoryExtractor(model)
        self.tools = []
        if tools_enabled:
            from app.harness.tools.memory import build_memory_tools

            self.tools = build_memory_tools(
                memory_store=memory_store,
                run_context=run_context,
            )

    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None:
            return None
        memories = retrieve_memories(
            messages=list(state.get("messages") or []),
            context=context,
            memory_store=self._memory_store,
            max_results=self._max_results,
        )
        if not memories:
            return None
        existing = dict(state.get("slotflow") or {})
        existing["long_term_memory"] = [memory.model_dump(mode="json") for memory in memories]
        return {"slotflow": existing}

    def wrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], ModelResponse],
    ) -> ModelResponse:
        return handler(self._request_with_memories(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._request_with_memories(request))

    async def abefore_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    async def aafter_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        sync_update = self.after_agent(state, runtime)
        self._maybe_schedule_extraction(state, runtime)
        return sync_update

    def _maybe_schedule_extraction(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> None:
        context = runtime.context
        if context is None:
            return
        maybe_schedule_extraction(
            messages=list(state.get("messages") or []),
            context=context,
            extractor=self._extractor,
            memory_store=self._memory_store,
            proactive_extraction_enabled=self._proactive_extraction_enabled,
        )

    def _request_with_memories(
        self,
        request: ModelRequest[RunContext],
    ) -> ModelRequest[RunContext]:
        context = request.runtime.context if request.runtime is not None else None
        if context is None:
            return request
        memories = retrieve_memories(
            messages=list(request.messages or []),
            context=context,
            memory_store=self._memory_store,
            max_results=self._max_results,
        )
        system_message = append_memory_system_message(
            request.system_message,
            memories=memories,
            tools_enabled=self._tools_enabled,
        )
        return replace(request, system_message=system_message)


    async def _aextract_and_save(self, conversation: str, context: RunContext) -> None:
        from app.harness.steps.long_term_memory import aextract_and_save

        await aextract_and_save(
            conversation=conversation,
            context=context,
            extractor=self._extractor,
            memory_store=self._memory_store,
        )

    def after_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None:
            return None
        return explicit_save_update(
            messages=list(state.get("messages") or []),
            context=context,
            memory_store=self._memory_store,
        )


__all__ = [
    "SlotFlowLongTermMemoryMiddleware",
    "append_memory_system_message",
    "build_memory_prompt",
    "build_turn_memory_candidate",
    "build_turn_memory_content",
    "build_extraction_conversation",
    "MemoryCandidate",
    "extract_explicit_memory",
    "normalize_memory_sentence",
    "latest_user_text",
    "latest_message_index",
    "memory_save_tool_used_for_run",
    "tool_message_name",
    "retrieve_memories",
    "maybe_schedule_extraction",
    "explicit_save_update",
]
