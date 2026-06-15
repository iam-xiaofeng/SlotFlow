"""Preflight Skill discovery for specialized user requests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.tools.customization import find_installable_skills


SKILLS_PREFLIGHT_BLOCK_START = "<slotflow-skills-preflight>"
SKILLS_PREFLIGHT_BLOCK_END = "</slotflow-skills-preflight>"

_SPECIALIZED_TERMS = (
    "分析",
    "研究",
    "报告",
    "图表",
    "可视化",
    "论文",
    "专利",
    "金融",
    "股票",
    "数据",
    "代码",
    "调试",
    "设计",
    "医学",
    "法律",
    "会计",
    "工程",
    "专业",
    "domain",
    "professional",
    "research",
    "analysis",
    "report",
    "chart",
    "visualization",
    "finance",
    "stock",
    "patent",
    "legal",
    "medical",
    "debug",
    "code",
)


SkillFinder = Callable[[str, int, SlotFlowSandboxConfig], dict[str, Any]]


class SlotFlowSkillsPreflightMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject find-skills results before the model handles specialized work."""

    name = "SlotFlowSkillsPreflightMiddleware"

    def __init__(
        self,
        *,
        sandbox_config: SlotFlowSandboxConfig | None = None,
        finder: SkillFinder | None = None,
        max_results: int = 5,
    ) -> None:
        self._sandbox_config = sandbox_config or SlotFlowSandboxConfig()
        self._finder = finder or _default_find_skills
        self._max_results = max_results

    @override
    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        _ = runtime
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None

        last_message = messages[-1]
        user_text = _message_text(last_message.content)
        if not _should_run_preflight(user_text):
            return None
        if SKILLS_PREFLIGHT_BLOCK_START in user_text:
            return None

        result = self._finder(user_text, self._max_results, self._sandbox_config)
        preflight_block = _format_preflight(result)
        messages[-1] = HumanMessage(
            content=_prepend_text(last_message.content, f"{preflight_block}\n\n"),
            id=last_message.id,
            name=last_message.name,
            additional_kwargs=last_message.additional_kwargs,
            response_metadata=last_message.response_metadata,
        )

        slotflow = dict(state.get("slotflow") or {})
        slotflow["skills_preflight"] = result
        return {"messages": messages, "slotflow": slotflow}


def _default_find_skills(
    query: str,
    max_results: int,
    config: SlotFlowSandboxConfig,
) -> dict[str, Any]:
    return find_installable_skills(query=query, max_results=max_results, config=config)


def _should_run_preflight(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 8:
        return False
    lowered = stripped.lower()
    if "不要" in stripped and "skill" in lowered:
        return False
    return len(stripped) >= 24 or any(term in lowered for term in _SPECIALIZED_TERMS)


def _format_preflight(result: dict[str, Any]) -> str:
    return (
        f"{SKILLS_PREFLIGHT_BLOCK_START}\n"
        "Backend preflight already ran find-skills for this specialized request.\n"
        "Review these results before answering. Install a Skill only when a concrete "
        "package_url and skill_name are available and relevant.\n"
        f"{json.dumps(result, ensure_ascii=False)}\n"
        f"{SKILLS_PREFLIGHT_BLOCK_END}"
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _prepend_text(content: Any, prefix: str) -> Any:
    if isinstance(content, str):
        return f"{prefix}{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}, *content]
    return content
