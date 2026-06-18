"""SlotFlow subagent delegation tools."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, tool

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.subagents.config import SlotFlowSubagentConfig, SlotFlowSubagentProfile
from app.harness.utils import message_role, model_supports_tools


@dataclass(frozen=True, slots=True)
class SubagentTaskResult:
    """Structured result returned by the local task delegation tool."""

    status: str
    agent_name: str
    task: str
    context: str
    expected_output: str
    priority: str
    result: str
    source: str = "slotflow_subagent_task_tool"

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "agent_name": self.agent_name,
                "task": self.task,
                "context": self.context,
                "expected_output": self.expected_output,
                "priority": self.priority,
                "result": self.result,
                "source": self.source,
            },
            ensure_ascii=False,
        )


class SubagentTaskRunner:
    """Run a focused task through a real LangChain agent profile."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        run_context: RunContext,
        environment_tools: Sequence[BaseTool] = (),
        config: SlotFlowSubagentConfig | None = None,
    ) -> None:
        self._model = model
        self._run_context = run_context
        self._environment_tools = list(environment_tools)
        self._profiles = {
            profile.name: profile
            for profile in (config or SlotFlowSubagentConfig()).enabled_profiles()
        }

    def has_profiles(self) -> bool:
        """Return whether at least one subagent profile is enabled."""

        return bool(self._profiles)

    def profiles(self) -> list[SlotFlowSubagentProfile]:
        """Return enabled profiles in stable order."""

        return list(self._profiles.values())

    async def arun(
        self,
        *,
        agent_name: str,
        task: str,
        context: str = "",
        expected_output: str = "",
        priority: str = "normal",
    ) -> SubagentTaskResult:
        clean_agent_name = agent_name.strip()
        clean_task = task.strip()
        clean_context = context.strip()
        clean_expected_output = expected_output.strip()
        clean_priority = normalize_priority(priority)

        if not clean_task:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=task,
                context=context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                result="task must not be blank",
            )

        profile = self._profiles.get(clean_agent_name)
        if profile is None:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=clean_task,
                context=clean_context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                result=f"unknown subagent: {clean_agent_name}",
            )

        try:
            graph = create_agent(
                model=self._model,
                tools=usable_tools_for_model(
                    model=self._model,
                    tools=self._environment_tools,
                ),
                system_prompt=build_subagent_system_prompt(
                    profile=profile,
                    run_context=self._run_context,
                ),
            )
            result = await graph.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": build_subagent_user_prompt(
                                task=clean_task,
                                context=clean_context,
                                expected_output=clean_expected_output,
                                priority=clean_priority,
                                run_context=self._run_context,
                            ),
                        }
                    ]
                }
            )
        except Exception as exc:  # noqa: BLE001 - return model-readable tool result
            return SubagentTaskResult(
                status="error",
                agent_name=profile.name,
                task=clean_task,
                context=clean_context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                result=f"subagent execution failed: {exc.__class__.__name__}: {exc}",
            )

        return SubagentTaskResult(
            status="completed",
            agent_name=profile.name,
            task=clean_task,
            context=clean_context,
            expected_output=clean_expected_output,
            priority=clean_priority,
            result=latest_assistant_text(result) or "",
        )


def build_subagent_tools(
    *,
    features: SlotFlowHarnessFeatures,
    config: SlotFlowSubagentConfig | None = None,
    model: str | BaseChatModel | None = None,
    run_context: RunContext | None = None,
    environment_tools: Sequence[BaseTool] = (),
) -> list[BaseTool]:
    """Build subagent tools only when the current run enables subagents."""

    if not features.subagent_enabled:
        return []
    if model is None or run_context is None:
        return []

    runner = SubagentTaskRunner(
        model=model,
        run_context=run_context,
        environment_tools=environment_tools,
        config=config,
    )
    if not runner.has_profiles():
        return []

    @tool("subagent_list")
    async def subagent_list() -> str:
        """List enabled SlotFlow subagent profiles and their capabilities."""

        return json.dumps(
            {
                "source": "slotflow_subagent_list",
                "subagents": [
                    {
                        "name": profile.name,
                        "description": profile.description,
                        "capabilities": list(profile.capabilities),
                        "output_contract": profile.output_contract,
                    }
                    for profile in runner.profiles()
                ],
            },
            ensure_ascii=False,
        )

    @tool("task_tool")
    async def task_tool(
        agent_name: str,
        task: str,
        context: str = "",
        expected_output: str = "",
        priority: str = "normal",
    ) -> str:
        """Delegate a focused task to a named SlotFlow subagent profile."""

        result = await runner.arun(
            agent_name=agent_name,
            task=task,
            context=context,
            expected_output=expected_output,
            priority=priority,
        )
        return result.to_json()

    return [subagent_list, task_tool]


def build_subagent_system_prompt(
    *,
    profile: SlotFlowSubagentProfile,
    run_context: RunContext,
) -> str:
    """Build the system prompt for a real subagent run."""

    sections = [
        profile.system_prompt,
        "",
        "<slotflow-subagent>",
        f"name={profile.name}",
        f"description={profile.description}",
        f"parent_thread_id={run_context.thread_id}",
        f"parent_run_id={run_context.run_id}",
        f"capabilities={', '.join(profile.capabilities) if profile.capabilities else 'general'}",
        f"output_contract={profile.output_contract}",
        "Return a concise, concrete result for the parent agent.",
        "</slotflow-subagent>",
    ]
    if run_context.uploaded_files:
        sections.extend(["", "<slotflow-uploaded-files>"])
        for uploaded_file in run_context.uploaded_files:
            display_name = uploaded_file.original_filename or uploaded_file.filename
            sections.append(
                "- "
                f"path={uploaded_file.workspace_path}; "
                f"filename={display_name}; "
                f"stored_filename={uploaded_file.filename}; "
                f"content_type={uploaded_file.content_type or 'unknown'}; "
                f"size_bytes={uploaded_file.size_bytes}"
            )
        sections.extend(
            [
                "Use workspace_read(path) when the task requires file content.",
                "</slotflow-uploaded-files>",
            ]
        )
    return "\n".join(sections)


def build_subagent_user_prompt(
    *,
    task: str,
    context: str,
    expected_output: str,
    priority: str,
    run_context: RunContext,
) -> str:
    """Build the user message sent to a subagent."""

    sections = [
        f"Task: {task}",
        "",
        f"Priority: {priority}",
        f"Parent mode: {run_context.mode}",
        f"Parent agent: {run_context.agent_name}",
    ]
    if context:
        sections.extend(["", f"Context: {context}"])
    if expected_output:
        sections.extend(["", f"Expected output: {expected_output}"])
    return "\n".join(sections)


def normalize_priority(priority: str) -> str:
    normalized = priority.strip().lower()
    if normalized in {"low", "normal", "high"}:
        return normalized
    return "normal"


def usable_tools_for_model(
    *,
    model: str | BaseChatModel,
    tools: Sequence[BaseTool],
) -> list[BaseTool]:
    """Only bind child tools when the selected model supports tool calling."""

    if not tools:
        return []
    return list(tools) if model_supports_tools(model) else []


def latest_assistant_text(result: Any) -> str | None:
    """Extract the last assistant text from a LangGraph agent result."""

    if not isinstance(result, dict):
        return None

    messages = result.get("messages")
    if not isinstance(messages, list):
        return None

    for message in reversed(messages):
        role = message_role(message)
        if role not in {"assistant", "ai"}:
            continue
        content = message_content(message)
        if content:
            return content
    return None


def message_content(message: Any) -> str:
    if isinstance(message, BaseMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

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
    return "" if content is None else str(content)
