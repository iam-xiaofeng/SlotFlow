"""Step: preflight Skill discovery for specialized user requests.

Extracted from ``SlotFlowSkillsPreflightMiddleware.before_agent``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage

from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.tools.customization import find_relevant_skills

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

SkillFinder = Callable[..., dict[str, Any]]


def skills_preflight_update(
    *,
    state: SlotFlowAgentState,
    sandbox_config: SlotFlowSandboxConfig | None = None,
    skills_root: Any = None,
    skills_config_store: Any = None,
    finder: SkillFinder | None = None,
    uses_default_finder: bool = True,
    max_results: int = 5,
) -> dict[str, Any] | None:
    messages = list(state.get("messages") or [])
    if not messages or not isinstance(messages[-1], HumanMessage):
        return None
    last_message = messages[-1]
    user_text = _message_text(last_message.content)
    if not should_run_preflight(user_text):
        return None
    if SKILLS_PREFLIGHT_BLOCK_START in user_text:
        return None

    if uses_default_finder:
        result = finder(
            user_text,
            max_results,
            sandbox_config,
            skills_root,
            skills_config_store,
        )
    else:
        result = finder(user_text, max_results, sandbox_config)
    preflight_block = format_preflight(result)
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


def default_find_skills(
    query: str,
    max_results: int,
    config: SlotFlowSandboxConfig,
    skills_root: Any = None,
    skills_config_store: Any = None,
) -> dict[str, Any]:
    # Preflight runs in the synchronous prepare node BEFORE the first model token, so it
    # MUST NOT do a network installable-skill search (a ~4s web request would delay every
    # first token). Local-only matching; the model can run the network search itself via
    # skill_match when it actually wants to install a Skill.
    return find_relevant_skills(
        query=query,
        max_results=max_results,
        skills_root=skills_root,
        skills_config_store=skills_config_store,
        config=config,
        local_only=True,
    )


def should_run_preflight(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 8:
        return False
    lowered = stripped.lower()
    if "不要" in stripped and "skill" in lowered:
        return False
    return len(stripped) >= 24 or any(term in lowered for term in _SPECIALIZED_TERMS)


def format_preflight(result: dict[str, Any]) -> str:
    return (
        f"{SKILLS_PREFLIGHT_BLOCK_START}\n"
        "SlotFlow ran a Skills preflight for this specialized request (installed Skills first, "
        "then installable ones). Treat this as a STRONG hint to PREFER a Skill over answering "
        "from general knowledge:\n"
        "- If installed_matches is non-empty: USE those installed Skill(s) for this run.\n"
        "- If installed_matches is empty but an installable candidate looks relevant: install "
        "the best match with skill_install (its package_url + skill_name), or run find-skills / "
        "search_skill_repos (prefer high-star GitHub repos) to confirm, BEFORE doing the work.\n"
        "- Only skip Skills when none are genuinely relevant — say so briefly and proceed with "
        "your best tools.\n"
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
