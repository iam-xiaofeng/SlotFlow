"""Agent middleware for SlotFlow long-term memory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import replace
import json
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.memory import MemoryKind, MemoryRecord, SlotFlowMemoryStore
from app.harness.state import SlotFlowAgentState


class SlotFlowLongTermMemoryMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject relevant persisted memories and save compact turn memories."""

    def __init__(
        self,
        *,
        memory_store: SlotFlowMemoryStore,
        max_results: int = 5,
    ) -> None:
        self._memory_store = memory_store
        self._max_results = max_results

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
        return self.after_agent(state, runtime)

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
) -> SystemMessage:
    section = build_memory_prompt(memories)
    if system_message is None:
        return SystemMessage(content=section)

    base_content = message_text(system_message)
    content = f"{base_content}\n\n{section}" if base_content else section
    return SystemMessage(content=content)


def build_memory_prompt(memories: list[MemoryRecord]) -> str:
    lines = [
        "<slotflow-long-term-memory>",
        "SlotFlow 本地长期记忆已启用。你可以使用 memory_list、memory_save、memory_update、memory_delete 工具显式管理记忆；middleware 也会自动保存和召回有长期价值的偏好、基础信息、近期话题。",
        "不要声称你没有长期记忆功能。如果没有相关记忆，只说明本轮没有检索到相关记忆。",
    ]
    if memories:
        lines.append("以下是本地长期记忆中与当前问题可能相关的信息。只在有帮助时使用；不要把这些记忆当作本轮新上传文件。")
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
    """Extract only durable user facts from the latest turn.

    This intentionally avoids saving arbitrary Q&A summaries. Long-term memory
    should contain user preferences, stable profile information, and current
    recurring project/topic context.
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

    return extract_memory_candidate_from_user_text(user_text)


def extract_memory_candidate_from_user_text(text: str) -> MemoryCandidate | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None

    explicit = extract_explicit_memory(normalized)
    if explicit is not None:
        return explicit

    if looks_like_question(normalized):
        return None

    if re.search(r"(我喜欢|我更喜欢|我希望|我偏好|以后.*(请|要|用|不要)|回答.*(简洁|详细|中文|英文)|不要.*回答)", normalized):
        return MemoryCandidate(kind="preference", content=normalize_memory_sentence(normalized))

    if re.search(r"(我是|我叫|我的名字|我的背景|我的工作|我在.+(公司|学校|项目|团队)|我负责)", normalized):
        return MemoryCandidate(kind="profile", content=normalize_memory_sentence(normalized))

    if re.search(r"(我们现在|当前项目|这个项目|最近在|正在做|下一阶段|下一步|目前重点)", normalized):
        return MemoryCandidate(kind="topic", content=normalize_memory_sentence(normalized))

    return None


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


def looks_like_question(text: str) -> bool:
    return text.endswith(("?", "？")) or bool(re.search(r"(吗|么|什么|为什么|怎么|如何|能否|是不是|是否)", text))


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


def message_role(message: Any) -> str | None:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, BaseMessage):
        return str(message.type)
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        return str(role) if role is not None else None
    role = getattr(message, "role", None) or getattr(message, "type", None)
    return str(role) if role is not None else None


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
