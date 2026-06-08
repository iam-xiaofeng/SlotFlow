"""SlotFlow subagent delegation tools."""

from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool

from app.harness.features import SlotFlowHarnessFeatures
from app.harness.subagents.config import SlotFlowSubagentConfig, SlotFlowSubagentProfile


@dataclass(frozen=True, slots=True)
class SubagentTaskResult:
    """Structured result returned by the local task delegation tool."""

    status: str
    agent_name: str
    task: str
    context: str
    result: str
    source: str = "slotflow_subagent_task_tool"

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "agent_name": self.agent_name,
                "task": self.task,
                "context": self.context,
                "result": self.result,
                "source": self.source,
            },
            ensure_ascii=False,
        )


class SubagentTaskRunner:
    """First local runner for subagent task delegation.

    This runner does not start another model yet. It validates the requested profile
    and returns a structured handoff that the main agent can reason over.
    """

    def __init__(self, config: SlotFlowSubagentConfig | None = None) -> None:
        self._profiles = {
            profile.name: profile
            for profile in (config or SlotFlowSubagentConfig()).enabled_profiles()
        }

    def has_profiles(self) -> bool:
        """Return whether at least one subagent profile is enabled."""

        return bool(self._profiles)

    def run(self, *, agent_name: str, task: str, context: str = "") -> SubagentTaskResult:
        clean_agent_name = agent_name.strip()
        clean_task = task.strip()
        clean_context = context.strip()

        if not clean_task:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=task,
                context=context,
                result="task must not be blank",
            )

        profile = self._profiles.get(clean_agent_name)
        if profile is None:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=clean_task,
                context=clean_context,
                result=f"unknown subagent: {clean_agent_name}",
            )

        return SubagentTaskResult(
            status="accepted",
            agent_name=profile.name,
            task=clean_task,
            context=clean_context,
            result=render_delegation_result(profile=profile, task=clean_task, context=clean_context),
        )


def build_subagent_tools(
    *,
    features: SlotFlowHarnessFeatures,
    config: SlotFlowSubagentConfig | None = None,
) -> list[BaseTool]:
    """Build subagent tools only when the current run enables subagents."""

    if not features.subagent_enabled:
        return []

    runner = SubagentTaskRunner(config)
    if not runner.has_profiles():
        return []

    @tool("task_tool")
    def task_tool(agent_name: str, task: str, context: str = "") -> str:
        """Delegate a focused task to a named SlotFlow subagent profile."""

        return runner.run(
            agent_name=agent_name,
            task=task,
            context=context,
        ).to_json()

    return [task_tool]


def render_delegation_result(
    *,
    profile: SlotFlowSubagentProfile,
    task: str,
    context: str,
) -> str:
    """Render the first deterministic subagent handoff result."""

    parts = [
        f"Delegated to {profile.name}: {profile.description}",
        f"Task: {task}",
    ]
    if context:
        parts.append(f"Context: {context}")
    parts.append(f"Instruction: {profile.system_prompt}")
    return "\n".join(parts)
