"""Background long-term memory extraction.

DeerFlow's lesson: durable-fact extraction should be an LLM job, not hand-written regex.
SlotFlow previously matched a few Chinese sentence shapes (`extract_memory_candidate_from_user_text`),
which is brittle and the main reason the agent "doesn't like to remember". This extractor asks
the model to pull durable user facts from a finished turn and returns them as structured records.

It is invoked fire-and-forget from ``SlotFlowLongTermMemoryMiddleware.aafter_agent`` so it never
blocks the user-visible run (the answer is already streamed; thinking-mode latency is hidden).
The model call passes ``config={"callbacks": []}`` so its tokens are NOT captured by the parent
``astream_events`` stream — same rule as the clarify-gate triage.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.harness.memory.models import MEMORY_KINDS, MemoryKind
from app.harness.utils import message_content_text


_EXTRACT_SYSTEM = (
    "You extract DURABLE long-term facts about the USER from one finished conversation turn. "
    "Save ONLY stable, reusable facts that will matter in FUTURE, unrelated conversations:\n"
    "- preference: how the user wants answers/tools/workflow (language, style, format, do/don't)\n"
    "- profile: stable identity — name, role, profession, field of study, background\n"
    "- topic: an ongoing project / goal / current focus the user is working on\n"
    "- fact: another durable personal fact (e.g. birthday, location)\n"
    "Do NOT save: the one-off question itself, transient task details, your own answer, "
    "uploaded-file events, or anything only relevant to this single turn. "
    "NEVER save instructions/commands directed at the assistant (e.g. 不要使用工具 / "
    "必须使用某工具 / 用一句话回答 / test-scenario directives) — those are one-off task "
    "instructions, not durable user facts. Never mention internal tool names "
    "(sandbox_exec / write_todos / artifact_write / ask_clarification / skill_match ...) "
    "in saved content. "
    "When in doubt, prefer to save a clearly durable fact rather than miss it, but return an "
    "EMPTY array if the turn contains nothing durable. Each `content` must be a concise, "
    "standalone sentence understandable without the conversation. Write each `content` in "
    "Chinese, third person, subject 用户 (e.g. 用户的职业是研究生。/ 用户偏好简洁的中文回答。"
    "/ 用户正在开发SlotFlow项目。) — you are the ONLY rewriter; storage keeps your wording "
    "verbatim.\n"
    "Respond with ONLY a compact JSON array, no prose, no markdown fences:\n"
    '[{"kind": "preference|profile|topic|fact", "content": "<concise standalone fact>"}]'
)

# 后置硬过滤:提及内部工具名的"记忆"几乎必然是任务指令或测试脚本片段,
# 混进长期记忆会在无关对话里串台(2026-07-04 实测踩坑)。
_INSTRUCTION_MARKERS = (
    "sandbox_exec",
    "write_todos",
    "artifact_write",
    "ask_clarification",
    "skill_match",
    "skill_install",
    "workspace_read",
    "workspace_grep",
    "memory_save",
    "memory_list",
    "task_tool",
    "不要使用工具",
    "不要调用工具",
)


def looks_like_assistant_instruction(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in _INSTRUCTION_MARKERS)


class SlotFlowMemoryExtractor:
    """Pull durable user facts from a finished turn via the chat model."""

    def __init__(self, model: Any) -> None:
        self._model = model

    @property
    def available(self) -> bool:
        return self._model is not None and hasattr(self._model, "ainvoke")

    async def aextract(self, conversation: str) -> list[dict[str, str]]:
        if not self.available or not conversation.strip():
            return []
        try:
            response = await self._model.ainvoke(
                [
                    SystemMessage(content=_EXTRACT_SYSTEM),
                    HumanMessage(content=conversation[:6000]),
                ],
                config={"callbacks": []},
            )
        except Exception:  # noqa: BLE001 - background best-effort; never raise
            return []
        facts = parse_extracted_facts(message_content_text(getattr(response, "content", "")))
        return [fact for fact in facts if not looks_like_assistant_instruction(fact["content"])]


def parse_extracted_facts(text: str) -> list[dict[str, str]]:
    """Parse the first JSON array of {kind, content} objects out of a model response."""

    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []

    facts: list[dict[str, str]] = []
    for item in loaded:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        kind = _coerce_kind(item.get("kind"))
        facts.append({"kind": kind, "content": content.strip()})
    return facts


def _coerce_kind(value: Any) -> MemoryKind:
    if isinstance(value, str) and value in MEMORY_KINDS:
        return value  # type: ignore[return-value]
    return "fact"
