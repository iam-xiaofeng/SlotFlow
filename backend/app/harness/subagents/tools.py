"""SlotFlow subagent delegation tools."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.subagents.config import SlotFlowSubagentConfig, SlotFlowSubagentProfile
from app.harness.subagents.role_catalog import (
    SubagentRoleCatalog,
    SubagentRoleTemplate,
    default_role_catalog,
)
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
    domain: str = ""
    role_name: str = ""
    role_id: str = ""
    role_path: str = ""
    tool_spaces: tuple[str, ...] = ()
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
                "domain": self.domain,
                "role_name": self.role_name,
                "role_id": self.role_id,
                "role_path": self.role_path,
                "tool_spaces": list(self.tool_spaces),
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
        role_catalog: SubagentRoleCatalog | None = None,
    ) -> None:
        self._model = model
        self._run_context = run_context
        self._environment_tools = list(environment_tools)
        self._role_catalog = role_catalog or default_role_catalog()
        resolved_config = config or SlotFlowSubagentConfig()
        self._recursion_limit = resolved_config.recursion_limit
        self._profiles = {
            profile.name: profile
            for profile in resolved_config.enabled_profiles()
        }

    def has_profiles(self) -> bool:
        """Return whether at least one subagent profile is enabled."""

        return bool(self._profiles)

    def profiles(self) -> list[SlotFlowSubagentProfile]:
        """Return enabled profiles in stable order."""

        return list(self._profiles.values())

    def role_domains(self) -> list[dict[str, Any]]:
        """Return compact Layer-2 role-domain summaries."""

        return self._role_catalog.domains()

    def search_roles(
        self,
        *,
        query: str = "",
        domain: str = "",
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        """Return compact Layer-3 role candidates without prompt bodies."""

        return self._role_catalog.search(
            query=query,
            domain=domain,
            max_results=max_results,
        )

    async def arun(
        self,
        *,
        agent_name: str,
        task: str,
        context: str = "",
        expected_output: str = "",
        priority: str = "normal",
        domain: str = "",
        role_name: str = "",
        tool_spaces: list[str] | None = None,
        runnable_config: RunnableConfig | None = None,
    ) -> SubagentTaskResult:
        clean_agent_name = agent_name.strip()
        clean_task = task.strip()
        clean_context = context.strip()
        clean_expected_output = expected_output.strip()
        clean_priority = normalize_priority(priority)
        clean_domain = domain.strip()
        clean_role_name = role_name.strip()
        resolved_tool_spaces, tool_space_error = resolve_subagent_tool_spaces(
            clean_agent_name,
            tool_spaces,
        )
        if tool_space_error:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=clean_task,
                context=clean_context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                domain=clean_domain,
                role_name=clean_role_name,
                tool_spaces=resolved_tool_spaces,
                result=tool_space_error,
            )

        if not clean_task:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=task,
                context=context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                domain=clean_domain,
                role_name=clean_role_name,
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
                domain=clean_domain,
                role_name=clean_role_name,
                result=f"unknown subagent: {clean_agent_name}",
            )

        role_template = self._role_catalog.resolve(
            domain=clean_domain,
            role_name=clean_role_name,
            task=clean_task,
            context=clean_context,
            expected_output=clean_expected_output,
        )
        if clean_role_name and role_template is None:
            return SubagentTaskResult(
                status="error",
                agent_name=clean_agent_name,
                task=clean_task,
                context=clean_context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                domain=clean_domain,
                role_name=clean_role_name,
                result=f"unknown subagent role: {clean_role_name}",
            )

        try:
            from app.harness.graph import build_slotflow_graph

            sub_tools = usable_tools_for_model(
                model=self._model,
                tools=filter_tools_for_spaces(
                    self._environment_tools,
                    resolved_tool_spaces,
                ),
            )
            sub_features = SlotFlowHarnessFeatures(
                thinking_enabled=self._run_context.thinking_enabled,
                plan_enabled=False,
                subagent_enabled=False,
            )
            graph = build_slotflow_graph(
                model=self._model,
                tools=sub_tools,
                system_prompt=build_subagent_system_prompt(
                    profile=profile,
                    run_context=self._run_context,
                    role_template=role_template,
                ),
                run_context=self._run_context,
                features=sub_features,
                sandbox_config=SlotFlowSandboxConfig(),
                memory_store=None,
                skills_root=None,
                skills_config_store=None,
                config_flags=SlotFlowMiddlewareConfig(
                    runtime_summary_enabled=False,
                    artifact_discovery_enabled=False,
                    summarization_enabled=False,
                    long_term_memory_enabled=False,
                    skills_preflight_enabled=False,
                    clarify_gate_enabled=False,
                    uploads_enabled=False,
                    todo_enabled=False,
                    subagent_limit_enabled=False,
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
                                domain=clean_domain,
                                role_template=role_template,
                                run_context=self._run_context,
                            ),
                        }
                    ]
                },
                config={
                    **dict(runnable_config or {}),
                    "recursion_limit": self._recursion_limit,
                },
            )
        except Exception as exc:  # noqa: BLE001 - return model-readable tool result
            return SubagentTaskResult(
                status="error",
                agent_name=profile.name,
                task=clean_task,
                context=clean_context,
                expected_output=clean_expected_output,
                priority=clean_priority,
                domain=clean_domain,
                role_name=clean_role_name,
                role_id=role_template.id if role_template is not None else "",
                role_path=role_template.path if role_template is not None else "",
                result=f"subagent execution failed: {exc.__class__.__name__}: {exc}",
            )

        return SubagentTaskResult(
            status="completed",
            agent_name=profile.name,
            task=clean_task,
            context=clean_context,
            expected_output=clean_expected_output,
            priority=clean_priority,
            domain=clean_domain,
            role_name=role_template.name if role_template is not None else clean_role_name,
            role_id=role_template.id if role_template is not None else "",
            role_path=role_template.path if role_template is not None else "",
            tool_spaces=resolved_tool_spaces,
            result=latest_assistant_text(result) or "",
        )


SUBAGENT_TOOL_SPACES = (
    "workspace",
    "sandbox",
    "browser",
    "network",
    "documents",
    "extensions",
    "memory",
)
DEFAULT_PROFILE_TOOL_SPACES = {
    "researcher": ("network", "documents", "workspace"),
    "analyst": ("workspace", "sandbox"),
    "planner": ("workspace",),
    "coder": ("workspace", "sandbox"),
    "reviewer": ("workspace", "sandbox"),
    "writer": ("workspace", "documents"),
}


def resolve_subagent_tool_spaces(
    profile_name: str,
    requested: list[str] | None,
) -> tuple[tuple[str, ...], str | None]:
    values = requested if requested is not None else list(DEFAULT_PROFILE_TOOL_SPACES.get(profile_name, ("workspace",)))
    cleaned = tuple(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
    if any(value in {"all", "*"} for value in cleaned):
        return (), "tool_spaces does not allow all or wildcard"
    unknown = [value for value in cleaned if value not in SUBAGENT_TOOL_SPACES]
    if unknown:
        return (), f"unknown tool spaces: {', '.join(unknown)}"
    if len(cleaned) > 3:
        return (), "at most three tool spaces may be delegated to one subagent"
    return cleaned, None


def tool_space_for_name(name: str) -> str | None:
    if name.startswith(("workspace_", "artifact_", "context_archive_")):
        return "workspace"
    if name.startswith(("sandbox_", "docker_")):
        return "sandbox"
    if name.startswith("browser_"):
        return "browser"
    if name.startswith(("web_", "agent_reach_")):
        return "network"
    if name.startswith(("convert_", "markitdown_", "view_image")):
        return "documents"
    if name.startswith(("skill_", "find_skills", "search_skill", "mcp_")):
        return "extensions"
    if name.startswith("memory_"):
        return "memory"
    return None


def filter_tools_for_spaces(
    tools: Sequence[BaseTool],
    spaces: tuple[str, ...],
) -> list[BaseTool]:
    allowed = set(spaces)
    return [tool for tool in tools if tool_space_for_name(tool.name) in allowed]


def build_subagent_tools(
    *,
    features: SlotFlowHarnessFeatures,
    config: SlotFlowSubagentConfig | None = None,
    model: str | BaseChatModel | None = None,
    run_context: RunContext | None = None,
    environment_tools: Sequence[BaseTool] = (),
    role_catalog: SubagentRoleCatalog | None = None,
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
        role_catalog=role_catalog,
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
                "role_domains": runner.role_domains(),
                "usage": (
                    "Use agent_name for the Layer-1 functional role. Pass a Layer-2 "
                    "domain to task_tool when domain guidance is enough. When a precise "
                    "Layer-3 role matters, call subagent_role_search(query, domain) for "
                    "a short candidate list, then pass role_name to task_tool."
                ),
            },
            ensure_ascii=False,
        )

    @tool("subagent_role_search")
    async def subagent_role_search(
        query: str = "",
        domain: str = "",
        max_results: int = 8,
    ) -> str:
        """Search the file-backed Layer-3 subagent role catalog.

        Returns compact role metadata only, never full role prompts. Use this after
        subagent_list when a delegated task needs a precise professional role_name.
        """

        return json.dumps(
            {
                "source": "slotflow_subagent_role_search",
                "query": query,
                "domain": domain,
                "roles": runner.search_roles(
                    query=query,
                    domain=domain,
                    max_results=max_results,
                ),
                "usage": "Pass a returned role name or id as task_tool.role_name.",
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
        domain: str = "",
        role_name: str = "",
        tool_spaces: list[str] | None = None,
        config: RunnableConfig | None = None,
    ) -> str:
        """Delegate a focused task to a named SlotFlow subagent profile.

        `agent_name` selects the Layer-1 functional subagent. `domain` optionally selects
        a Layer-2 role category, and `role_name` optionally selects one concrete Layer-3
        agency role template to inject into only this child run.
        """

        result = await runner.arun(
            agent_name=agent_name,
            task=task,
            context=context,
            expected_output=expected_output,
            priority=priority,
            domain=domain,
            role_name=role_name,
            tool_spaces=tool_spaces,
            runnable_config=config,
        )
        return result.to_json()

    return [subagent_list, subagent_role_search, task_tool]


def build_subagent_system_prompt(
    *,
    profile: SlotFlowSubagentProfile,
    run_context: RunContext,
    role_template: SubagentRoleTemplate | None = None,
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
    if role_template is not None:
        sections.extend(
            [
                "",
                "<slotflow-agency-role>",
                f"id={role_template.id}",
                f"name={role_template.name}",
                f"domain={role_template.domain}",
                f"division={role_template.division}",
                f"path={role_template.path}",
                f"description={role_template.description}",
                "The following role template is adapted from the local agency-agents role library. Use it as domain operating guidance for this child task only; obey SlotFlow tool/safety rules above it.",
                "",
                role_template.prompt,
                "</slotflow-agency-role>",
            ]
        )
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
    domain: str = "",
    role_template: SubagentRoleTemplate | None = None,
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
    if domain:
        sections.append(f"Requested domain: {domain}")
    if role_template is not None:
        sections.append(f"Selected role: {role_template.name} ({role_template.id})")
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
