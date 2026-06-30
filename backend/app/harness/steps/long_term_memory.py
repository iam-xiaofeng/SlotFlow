"""Step: long-term memory retrieval / injection / explicit-save / background extraction.

Extracted from ``SlotFlowLongTermMemoryMiddleware``. These pure functions implement the
retrieval prompt building, explicit ``请记住X`` synchronous save, and the background LLM
extraction schedule. The thin middleware keeps the ``wrap_model_call`` surface (it needs the
ModelRequest handler signature) but delegates the data work here.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage

from app.chat.models import RunContext
from app.harness.memory import MemoryKind, MemoryRecord, SlotFlowMemoryStore
from app.harness.memory.extractor import SlotFlowMemoryExtractor
from app.harness.utils import message_role

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def schedule_background(coro) -> bool:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return False
    task = loop.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return True


def retrieve_memories(
    *,
    messages: list[Any],
    context: RunContext,
    memory_store: SlotFlowMemoryStore,
    max_results: int = 5,
) -> list[MemoryRecord]:
    query = latest_user_text(messages)
    return memory_store.search_memories(
        query=query,
        thread_id=context.thread_id,
        limit=max_results,
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


def build_turn_memory_candidate(messages: list[Any]) -> MemoryCandidate | None:
    assistant_index = latest_message_index(messages, roles={"assistant", "ai"})
    if assistant_index is None:
        return None
    user_index = latest_message_index(messages[:assistant_index], roles={"user", "human"})
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




def build_turn_memory_content(messages: list[Any]) -> str | None:
    """Backward-compatible helper returning only the extracted memory content."""

    candidate = build_turn_memory_candidate(messages)
    return candidate.content if candidate is not None else None

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


def maybe_schedule_extraction(
    *,
    messages: list[Any],
    context: RunContext,
    extractor: SlotFlowMemoryExtractor,
    memory_store: SlotFlowMemoryStore,
    proactive_extraction_enabled: bool,
) -> bool:
    if not proactive_extraction_enabled or not extractor.available:
        return False
    if context is None:
        return False
    if memory_save_tool_used_for_run(messages, run_id=context.run_id):
        return False
    conversation = build_extraction_conversation(messages)
    if not conversation:
        return False
    return schedule_background(
        aextract_and_save(
            conversation=conversation,
            context=context,
            extractor=extractor,
            memory_store=memory_store,
        )
    )


async def aextract_and_save(
    *,
    conversation: str,
    context: RunContext,
    extractor: SlotFlowMemoryExtractor,
    memory_store: SlotFlowMemoryStore,
) -> None:
    facts = await extractor.aextract(conversation)
    for fact in facts:
        try:
            memory_store.add_memory(
                thread_id=context.thread_id,
                source_run_id=None,
                kind=fact["kind"],
                content=fact["content"],
                metadata={"source": "memory_extractor", "extraction": "llm"},
            )
        except Exception:  # noqa: BLE001
            continue


def explicit_save_update(
    *,
    messages: list[Any],
    context: RunContext,
    memory_store: SlotFlowMemoryStore,
) -> dict[str, Any] | None:
    if context is None:
        return None
    if memory_save_tool_used_for_run(messages, run_id=context.run_id):
        return None
    candidate = build_turn_memory_candidate(messages)
    if candidate is None:
        return None
    memory = memory_store.add_memory(
        thread_id=context.thread_id,
        source_run_id=context.run_id,
        kind=candidate.kind,
        content=candidate.content,
        metadata={
            "source": "slotflow_long_term_memory_middleware",
            "extraction": "heuristic",
        },
    )
    return {"slotflow": {"long_term_memory_saved": memory.model_dump(mode="json")}}


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
    import json

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


def latest_user_text(messages: list[Any]) -> str:
    index = latest_message_index(messages, roles={"user", "human"})
    return message_text(messages[index]) if index is not None else ""


def latest_message_index(messages: list[Any], *, roles: set[str]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if message_role(messages[index]) in roles:
            return index
    return None
