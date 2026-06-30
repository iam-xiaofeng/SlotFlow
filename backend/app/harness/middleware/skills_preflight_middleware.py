"""Preflight Skill discovery for specialized user requests.

Thin delegate to ``app.harness.steps.skills_preflight``.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.steps.skills_preflight import (
    default_find_skills,
    skills_preflight_update,
)

SKILLS_PREFLIGHT_BLOCK_START = "<slotflow-skills-preflight>"
SKILLS_PREFLIGHT_BLOCK_END = "</slotflow-skills-preflight>"


class SlotFlowSkillsPreflightMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject find-skills results before the model handles specialized work."""

    name = "SlotFlowSkillsPreflightMiddleware"

    def __init__(
        self,
        *,
        sandbox_config: SlotFlowSandboxConfig | None = None,
        skills_root: Any = None,
        skills_config_store: Any = None,
        finder: Any = None,
        max_results: int = 5,
    ) -> None:
        self._sandbox_config = sandbox_config or SlotFlowSandboxConfig()
        self._skills_root = skills_root
        self._skills_config_store = skills_config_store
        self._uses_default_finder = finder is None
        self._finder = finder or default_find_skills
        self._max_results = max_results

    @override
    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        _ = runtime
        return skills_preflight_update(
            state=state,
            sandbox_config=self._sandbox_config,
            skills_root=self._skills_root,
            skills_config_store=self._skills_config_store,
            finder=self._finder,
            uses_default_finder=self._uses_default_finder,
            max_results=self._max_results,
        )
