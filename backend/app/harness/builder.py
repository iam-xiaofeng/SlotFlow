"""SlotFlow harness graph builder。

这里是 LangGraph agent graph 的唯一组装入口。`chat.runtime` 负责准备 model、
checkpointer、RunContext，再把这些运行时依赖显式传给这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel

from app.chat.models import RunContext
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.middleware import build_harness_middleware
from app.harness.skills import build_skills_prompt, load_enabled_skills
from app.harness.state import SlotFlowAgentState
from app.harness.tools import build_harness_tools
from app.harness.utils import model_supports_tools

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.types import Checkpointer


def build_slotflow_harness_graph(
    *,
    model: str | BaseChatModel,
    run_context: RunContext,
    harness_config: SlotFlowHarnessConfig,
    checkpointer: Checkpointer | None = None,
    tools: list[BaseTool] | None = None,
    middleware: list[AgentMiddleware] | None = None,
):
    """创建 SlotFlow 本地 harness graph。

    模块 10 只落下边界，不急着引入真实 tools/skills/MCP/middleware。后续模块会逐步把
    `tools` 和 `middleware` 从外部测试替身替换成 harness 内部 registry 的输出。
    """

    features = features_from_run_context(run_context)
    tools_supported = model_supports_tools(model)
    built_tools = build_harness_tools(
        features=features,
        model=model,
        run_context=run_context,
        extra_tools=tools,
        mcp_config=harness_config.mcp_config,
        mcp_tool_provider=harness_config.mcp_tool_provider,
        mcp_config_store=harness_config.mcp_config_store,
        skills_root=harness_config.skills_root,
        skills_config_store=harness_config.skills_config_store,
        sandbox_config=harness_config.sandbox_config,
        subagent_config=harness_config.subagent_config,
    )
    selected_tools = built_tools if tools_supported else []

    selected_middleware = build_harness_middleware(
        features=features,
        model=model,
        run_context=run_context,
        config=harness_config.middleware_config,
        memory_store=harness_config.memory_store,
        sandbox_config=harness_config.sandbox_config,
        skills_root=harness_config.skills_root,
        skills_config_store=harness_config.skills_config_store,
        extra_middleware=middleware,
        tools_enabled=tools_supported,
    )

    return _create_agent_graph(
        model=model,
        tools=selected_tools,
        middleware=selected_middleware,
        system_prompt=build_system_prompt(
            harness_config=harness_config,
            features=features,
            run_context=run_context,
        ),
        checkpointer=checkpointer,
    )


def build_system_prompt(
    *,
    harness_config: SlotFlowHarnessConfig,
    features: SlotFlowHarnessFeatures,
    run_context: RunContext,
) -> str:
    """构建第一版 harness system prompt。

    这里先只追加一个很小的 feature 摘要，目的是让测试能证明 `RunContext -> features`
    确实进入了 harness builder。正式 skills prompt 会在模块 12 接入。
    """

    enabled_skills = load_enabled_skills(
        skills_root=harness_config.skills_root,
        enabled_names=harness_config.enabled_skills,
    )
    sections = [
        harness_config.system_prompt,
        "",
        "<slotflow-runtime>",
        f"thinking_enabled={features.thinking_enabled}",
        f"plan_enabled={features.plan_enabled}",
        f"subagent_enabled={features.subagent_enabled}",
        "</slotflow-runtime>",
    ]
    sections.extend(
        [
            "",
            "<slotflow-long-term-memory-status>",
            f"enabled={harness_config.memory_store is not None}",
            "When enabled, long-term memory instructions and tools are owned by SlotFlowLongTermMemoryMiddleware.",
            "</slotflow-long-term-memory-status>",
        ]
    )
    sections.extend(
        [
            "",
            "<slotflow-extension-tools>",
            "Use web_search/web_fetch for public web access when current information is needed.",
            "Do not put internal reasoning, durable context summaries, todo updates, or tool-call transcripts in the final answer body. Thinking-mode output belongs in reasoning_content; final answer content should contain only user-facing results.",
            "When public web or fetched data supports a claim, include a Markdown source link next to the relevant sentence or data point, for example [来源](https://example.com/page).",
            "Use skill_match to check installed Skills first; it falls back to find-skills only when no local match exists.",
            "Use find-skills to search installable Skills when skill_match finds no relevant installed Skill. find-skills is a callable tool, not only a prompt skill.",
            "Query find-skills (and search_skill_repos) by CAPABILITY or task type in English — e.g. 'research', 'data analysis', 'web scraping', 'pdf', 'slides', 'stock'/'finance' — NOT by the literal topic words of the request (do not search '世界杯' or '股市'). Skills are organized by what they DO, not by subject. If the first query returns nothing, try synonyms and broader capability terms, and use search_skill_repos / web_search to look on GitHub before concluding that no Skill exists. Do not give up after one literal-keyword search.",
            "Use search_skill_repos to find installable Skills hosted on GitHub when the curated find-skills registry has no match; it returns repositories you can then install with skill_install.",
            "When the user asks about a domain, profession, specialized task, or expert workflow, call skill_match before doing the work so you can discover whether a matching installed or installable Skill exists.",
            "For specialized requests, SlotFlow also injects a backend skills preflight into the latest user message when possible; review installed_matches before deciding whether to search, install, or use a Skill.",
            "Use skill_install only when a concrete package_url and skill_name are known or the user explicitly asks for that exact install.",
            "After installing a relevant Skill, use it for the corresponding work as soon as it is available; if it only becomes available on the next run, say that plainly and continue with the best current tools.",
            "Use mcp_add_http only when the user provides a concrete streamable HTTP MCP endpoint or explicitly asks to register it.",
            "When uploaded files are present, their workspace paths are injected into the latest user message; call workspace_read(path) before answering file-content questions.",
            "When you need input from the user before you can proceed — an ambiguous or underspecified request, a required preference, or a risky/irreversible action — you MUST call ask_clarification with 2-4 concise options. It renders an interactive picker (with a free-text 'other' option) that the user clicks; do NOT instead write your questions as plain message text and wait for a reply. If several things are unknown, ask the single most blocking question via ask_clarification first rather than a long plain-text questionnaire. Still skip it when a reasonable default is obvious — don't over-ask.",
            "Every user-visible file MUST be produced with artifact_write — reports, charts, HTML/Markdown pages, visualizations, comparison tables, interactive demos, code previews. It is the only way a file appears in the artifact panel; files live in this conversation's artifact folder next to the user's uploads. Do NOT create user-facing deliverables with the filesystem MCP server or any other write path — files written that way will NOT appear in the artifact panel. Never claim you saved a file unless you actually called artifact_write for it.",
            "When the answer includes a chart, report, visualization, flowchart, comparison table, interactive demo, or code preview, create an artifact by default unless the user explicitly asks for text only.",
            "Installed skills or MCP servers may become reliably available on the next run after runtime refresh.",
            "</slotflow-extension-tools>",
        ]
    )
    sections.extend(build_mcp_status_prompt(harness_config.mcp_config))
    skills_prompt = build_skills_prompt(enabled_skills)
    if skills_prompt:
        sections.extend(["", skills_prompt])
    return "\n".join(sections)


def build_mcp_status_prompt(mcp_config) -> list[str]:
    """Describe configured MCP servers in the system prompt."""

    sections = [
        "",
        "<slotflow-mcp-status>",
        f"enabled={mcp_config.enabled}",
    ]
    if not mcp_config.servers:
        sections.append("servers=[]")
    else:
        sections.append("servers:")
        for server in mcp_config.servers:
            sections.append(
                "- "
                f"name={server.name}; "
                f"enabled={server.enabled}; "
                f"transport={server.config.get('transport', 'unknown') if server.config else 'unknown'}"
            )
    sections.extend(
        [
            "Enabled MCP servers are loaded as tools when their connection succeeds.",
            "</slotflow-mcp-status>",
        ]
    )
    return sections


def _create_agent_graph(
    *,
    model: str | BaseChatModel,
    tools: list[BaseTool],
    middleware: list[AgentMiddleware],
    system_prompt: str,
    checkpointer: Checkpointer | None,
):
    """薄封装 LangChain `create_agent`，方便模块测试 monkeypatch 边界参数。"""

    return create_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        system_prompt=system_prompt,
        state_schema=SlotFlowAgentState,
        context_schema=RunContext,
        checkpointer=checkpointer,
    )
