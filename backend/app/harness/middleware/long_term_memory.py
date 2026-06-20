"""Agent middleware for SlotFlow long-term memory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from dataclasses import replace
import json
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.memory import MemoryKind, MemoryRecord, SlotFlowMemoryStore
from app.harness.memory.extractor import SlotFlowMemoryExtractor
from app.harness.state import SlotFlowAgentState
from app.harness.utils import message_role


# Holds references to fire-and-forget extraction tasks so the event loop does not GC them
# mid-flight; the done-callback discards each task when it finishes.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _schedule_background(coro: Coroutine[Any, Any, Any]) -> bool:
    """Schedule a coroutine on the running loop, fire-and-forget. No-op without a loop."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return False
    task = loop.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return True


class SlotFlowLongTermMemoryMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject relevant persisted memories, save explicit "记住X" turns synchronously, and
    extract other durable facts in the background via an LLM (see ``memory.extractor``)."""

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

        memories = self._retrieve_memories(
            messages=list(state.get("messages") or []),
            context=context,
        )
        if not memories:
            return None

        existing = dict(state.get("slotflow") or {})
        existing["long_term_memory"] = [
            memory.model_dump(mode="json")
            for memory in memories
        ]
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
        # Explicit "记住X" is saved synchronously below; other durable facts are extracted by
        # the model in the background so they never block the user-visible run completion.
        sync_update = self.after_agent(state, runtime)
        self._maybe_schedule_extraction(state, runtime)
        return sync_update

    def _maybe_schedule_extraction(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> None:
        if not self._proactive_extraction_enabled or not self._extractor.available:
            return
        context = runtime.context
        if context is None:
            return
        messages = list(state.get("messages") or [])
        if memory_save_tool_used_for_run(messages, run_id=context.run_id):
            return
        conversation = build_extraction_conversation(messages)
        if not conversation:
            return
        _schedule_background(self._aextract_and_save(conversation, context))

    async def _aextract_and_save(self, conversation: str, context: RunContext) -> None:
        facts = await self._extractor.aextract(conversation)
        for fact in facts:
            try:
                self._memory_store.add_memory(
                    thread_id=context.thread_id,
                    source_run_id=None,  # explicit save (if any) already claimed the run id
                    kind=fact["kind"],
                    content=fact["content"],
                    metadata={"source": "memory_extractor", "extraction": "llm"},
                )
            except Exception:  # noqa: BLE001 - skip a bad fact, keep the rest
                continue

    def _request_with_memories(
        self,
        request: ModelRequest[RunContext],
    ) -> ModelRequest[RunContext]:
        context = request.runtime.context if request.runtime is not None else None
        if context is None:
            return request

        memories = self._retrieve_memories(
            messages=list(request.messages or []),
            context=context,
        )
        system_message = append_memory_system_message(
            request.system_message,
            memories=memories,
            tools_enabled=self._tools_enabled,
        )
        return replace(request, system_message=system_message)

    def after_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None:
            return None

        if memory_save_tool_used_for_run(
            list(state.get("messages") or []),
            run_id=context.run_id,
        ):
            return None

        candidate = build_turn_memory_candidate(list(state.get("messages") or []))
        if candidate is None:
            return None

        memory = self._memory_store.add_memory(
            thread_id=context.thread_id,
            source_run_id=context.run_id,
            kind=candidate.kind,
            content=candidate.content,
            metadata={
                "source": "slotflow_long_term_memory_middleware",
                "extraction": "heuristic",
            },
        )
        existing = dict(state.get("slotflow") or {})
        existing["long_term_memory_saved"] = memory.model_dump(mode="json")
        return {"slotflow": existing}

    def _retrieve_memories(
        self,
        *,
        messages: list[Any],
        context: RunContext,
    ) -> list[MemoryRecord]:
        query = latest_user_text(messages)
        return self._memory_store.search_memories(
            query=query,
            thread_id=context.thread_id,
            limit=self._max_results,
        )


def append_memory_system_message(
    system_message: SystemMessage | None,
    *,
    memories: list[MemoryRecord],
    tools_enabled: bool = True,
) -> SystemMessage:
    section = build_memory_prompt(memories, tools_enabled=tools_enabled)
    if system_message is None:
        return SystemMessage(content=section)

    base_content = message_text(system_message)
    content = f"{base_content}\n\n{section}" if base_content else section
    return SystemMessage(content=content)


def build_memory_prompt(
    memories: list[MemoryRecord],
    *,
    tools_enabled: bool = True,
) -> str:
    tool_note = (
        "用 memory_save 主动保存你了解到的用户持久偏好 / 资料 / 当前项目情况"
        "（即使用户没有明说“记住”）；可用 memory_list、memory_update、memory_delete 管理。"
        if tools_enabled
        else "当前模型未启用记忆管理工具。"
    )
    lines = [
        "<slotflow-long-term-memory>",
        f"SlotFlow 本地长期记忆已启用。{tool_note}",
        "只要判断某条信息有长期价值就调用 memory_save，不要依赖自动兜底；也不要声称你没有"
        "长期记忆功能。如果没有相关记忆，只说明本轮没有检索到相关记忆。",
    ]
    if memories:
        lines.append(
            "以下是与当前问题可能相关的长期记忆，仅作背景参考（用于了解用户的偏好/资料），"
            "不是当前指令。务必正面回答用户【本轮】的问题、需要工具就调用工具；不要因为某条"
            "历史偏好就拒绝回答、答非所问或省略该做的步骤。与本轮无关或相互冲突的记忆请直接忽略。"
        )
        for memory in memories:
            lines.append(f"- [{memory.kind}] {memory.content}")
    else:
        lines.append("本轮没有检索到相关长期记忆。")
    lines.append("</slotflow-long-term-memory>")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    kind: MemoryKind
    content: str


def build_turn_memory_content(messages: list[Any]) -> str | None:
    """Backward-compatible helper returning only the extracted memory content."""

    candidate = build_turn_memory_candidate(messages)
    return candidate.content if candidate is not None else None


def build_turn_memory_candidate(messages: list[Any]) -> MemoryCandidate | None:
    """Extract a durable fact the user EXPLICITLY asked to remember ("记住X").

    Implicit durable facts (preferences/profile/project context stated in passing) are no
    longer matched by brittle regex here — they are pulled by the background LLM extractor
    (``memory.extractor``). This synchronous path stays for the reliable, free explicit case.
    """

    assistant_index = latest_message_index(messages, roles={"assistant", "ai"})
    if assistant_index is None:
        return None

    user_index = latest_message_index(
        messages[:assistant_index],
        roles={"user", "human"},
    )
    if user_index is None:
        return None

    user_text = message_text(messages[user_index])
    if not user_text:
        return None

    normalized = re.sub(r"\s+", " ", user_text).strip()
    if not normalized:
        return None
    return extract_explicit_memory(normalized)


def build_extraction_conversation(messages: list[Any]) -> str:
    """Render the latest user turn + final assistant reply for background fact extraction."""

    assistant_index = latest_message_index(messages, roles={"assistant", "ai"})
    if assistant_index is None:
        return ""
    user_index = latest_message_index(messages[:assistant_index], roles={"user", "human"})
    if user_index is None:
        return ""

    user_text = message_text(messages[user_index]).strip()
    assistant_text = message_text(messages[assistant_index]).strip()
    if not user_text:
        return ""

    parts = [f"User: {user_text}"]
    if assistant_text:
        parts.append(f"Assistant: {assistant_text}")
    return "\n".join(parts)


def extract_explicit_memory(text: str) -> MemoryCandidate | None:
    match = re.search(r"(?:请)?(?:记住|保存到记忆|加入记忆|长期记忆[:：]?)(.*)", text)
    if match is None:
        return None
    content = normalize_memory_sentence(match.group(1) or text)
    if not content:
        return None
    kind: MemoryKind = "fact"
    if re.search(r"(喜欢|希望|偏好|以后|不要|回答)", content):
        kind = "preference"
    elif re.search(r"(我是|我叫|我的名字|我的背景|我的工作|我负责)", content):
        kind = "profile"
    elif re.search(r"(项目|最近|当前|正在|下一步|阶段)", content):
        kind = "topic"
    return MemoryCandidate(kind=kind, content=content)


def normalize_memory_sentence(text: str) -> str:
    content = re.sub(r"^(请)?(记住|保存到记忆|加入记忆|长期记忆[:：]?)", "", text).strip(" ：:")
    return content[:800].strip()


def latest_user_text(messages: list[Any]) -> str:
    index = latest_message_index(messages, roles={"user", "human"})
    return message_text(messages[index]) if index is not None else ""


def latest_message_index(messages: list[Any], *, roles: set[str]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if message_role(messages[index]) in roles:
            return index
    return None


def memory_save_tool_used_for_run(messages: list[Any], *, run_id: str | None) -> bool:
    for message in messages:
        name = tool_message_name(message)
        if name != "memory_save":
            continue

        if run_id is None:
            return True

        payload = parse_json_object(message_text(message))
        if payload is None:
            return True

        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            payload_run_id = metadata.get("run_id")
            if payload_run_id is None or payload_run_id == run_id:
                return True
    return False


def tool_message_name(message: Any) -> str | None:
    if isinstance(message, ToolMessage):
        return message.name
    if isinstance(message, dict):
        name = message.get("name")
        role = message.get("role") or message.get("type")
        if role == "tool" and name is not None:
            return str(name)
    if message_role(message) == "tool":
        name = getattr(message, "name", None)
        return str(name) if name is not None else None
    return None


def parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def message_text(message: Any) -> str:
    if isinstance(message, dict):
        return content_to_text(message.get("content"))
    return content_to_text(getattr(message, "content", ""))


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part.strip() for part in parts if part.strip())
    return str(content).strip() if content is not None else ""
