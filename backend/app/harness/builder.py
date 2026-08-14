"""SlotFlow harness graph builder。

这里是 LangGraph agent graph 的唯一组装入口。`chat.runtime` 负责准备 model、
checkpointer、RunContext，再把这些运行时依赖显式传给这里。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from langchain_core.language_models.chat_models import BaseChatModel

from app.clock import utc_now
from app.chat.models import RunContext
from app.harness.config import SlotFlowHarnessConfig
from app.harness.graph import build_slotflow_graph
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.skills import build_skills_prompt, load_enabled_skills
from app.harness.subagents import build_subagent_catalog_prompt
from app.harness.tools import build_harness_tools
from app.harness.utils import model_supports_tools

if TYPE_CHECKING:
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
):
    """创建 SlotFlow 本地 harness graph（node + edge 版本）。

    重构后改为 LangGraph 原生 `StateGraph`（见 `app.harness.graph`）。中间件逻辑已抽成
    `app/harness/steps/*` 纯函数，由节点直接调用，顺序由边显式保证。
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
        agent_reach_config=harness_config.agent_reach_config,
        markitdown_config=harness_config.markitdown_config,
        subagent_config=harness_config.subagent_config,
    )
    # 工具集恒定:`build_harness_tools` 已经把重工具(浏览器自动化)和数量发散的 MCP 原生
    # schema 挡在主 agent 之外(前者归 browser 子代理、后者收敛成 mcp_docs/mcp_call),
    # 所以这里直接全绑,不再有加载器/激活门。见 `harness/tool_spaces.py` 顶部说明。
    selected_tools = built_tools if tools_supported else []
    return build_slotflow_graph(
        model=model,
        tools=list(selected_tools),
        system_prompt=build_system_prompt(
            harness_config=harness_config,
            features=features,
            run_context=run_context,
            mcp_proxy_available=any(tool.name == "mcp_call" for tool in selected_tools),
        ),
        run_context=run_context,
        features=features,
        sandbox_config=harness_config.sandbox_config,
        memory_store=harness_config.memory_store,
        skills_root=harness_config.skills_root,
        skills_config_store=harness_config.skills_config_store,
        config_flags=(
            replace(
                harness_config.middleware_config,
                summarization_trigger_tokens=min(
                    harness_config.middleware_config.summarization_trigger_tokens,
                    run_context.context_input_budget_tokens,
                ),
            )
            if run_context.context_input_budget_tokens is not None
            else harness_config.middleware_config
        ),
        checkpointer=checkpointer,
    )


def build_system_prompt(
    *,
    harness_config: SlotFlowHarnessConfig,
    features: SlotFlowHarnessFeatures,
    run_context: RunContext,
    mcp_proxy_available: bool = True,
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
        f"current_utc_date={utc_now().date().isoformat()}",
        f"thinking_enabled={features.thinking_enabled}",
        f"plan_enabled={features.plan_enabled}",
        f"subagent_enabled={features.subagent_enabled}",
        "</slotflow-runtime>",
    ]
    sections.extend(
        [
            "",
            "<slotflow-freshness-policy>",
            "Before answering, ground time-sensitive claims on the current date above. For facts that change over time — current events, prices, laws, releases, APIs, model capabilities, rankings, availability, company/product status, schedules, weather, statistics, or anything the user asks as 'latest/current/today/now' — do not rely on training data alone.",
            "Use web_search/web_fetch, workspace files, uploaded files, or another authoritative source before grounding time-sensitive claims. If the answer is stable and unlikely to have changed, you may answer without web search.",
            "When sources disagree materially, say so clearly, cite/describe the conflicting sources, and give the best-supported conclusion with uncertainty instead of hiding the conflict.",
            "</slotflow-freshness-policy>",
        ]
    )
    sections.extend(
        [
            "",
            "<slotflow-long-term-memory-status>",
            f"enabled={harness_config.memory_store is not None}",
            "When enabled, long-term memory retrieval/injection/save is handled by the prepare/pre_model/finalize graph nodes.",
            "This is fully automatic and needs no tool calls from you: relevant memories are retrieved and injected into your context each turn, and durable facts from the conversation are saved for you.",
            "You have NO manual memory tools — do not attempt to call memory_save/memory_list/memory_update/memory_delete. Rely on the injected long-term-memory context; if it is missing or insufficient for the task, say so plainly instead of inventing memories.",
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
            "Skills are two-step: DISCOVER, then READ. <slotflow-skills> below lists only each installed Skill's name and one-line description — never its instructions. When a Skill looks relevant, you MUST call skill_read(name) to load its SKILL.md body as a tool result and then follow it. Do NOT act on a Skill from its description alone, and do NOT guess its procedure from general knowledge.",
            "skill_read also returns the Skill's bundled files (scripts, templates, references). Read one with skill_read(name, path=...) when the body points at it; run its helper scripts with sandbox_exec.",
            "If a compaction summary says a Skill was already loaded but its text is gone, call skill_read(name) again rather than working from the summary; use context_archive_search/context_archive_read to recover the original tool result and other dropped details.",
            "Use skill_match to check installed Skills first; it falls back to find-skills only when no local match exists. It returns candidates to read, not instructions — follow it with skill_read.",
            "Use find-skills to search installable Skills when skill_match finds no relevant installed Skill. find-skills is a callable tool, not only a prompt skill.",
            "Query find-skills (and search_skill_repos) by CAPABILITY or task type in English — e.g. 'research', 'data analysis', 'web scraping', 'pdf', 'slides', 'stock'/'finance' — NOT by the literal topic words of the request (do not search '世界杯' or '股市'). Skills are organized by what they DO, not by subject. If the first query returns nothing, try synonyms and broader capability terms, and use search_skill_repos / web_search to look on GitHub before concluding that no Skill exists. Do not give up after one literal-keyword search.",
            "Use search_skill_repos to find installable Skills hosted on GitHub when the curated find-skills registry has no match; it returns repositories you can then install with skill_install.",
            "When the user asks about a domain, profession, specialized task, or expert workflow, call skill_match before doing the work so you can discover whether a matching installed or installable Skill exists.",
            "Use skill_install only when a concrete package_url and skill_name are known or the user explicitly asks for that exact install.",
            "After installing a relevant Skill, use it for the corresponding work as soon as it is available; if it only becomes available on the next run, say that plainly and continue with the best current tools.",
            "Use mcp_add_http only when the user provides a concrete streamable HTTP MCP endpoint or explicitly asks to register it.",
            "When uploaded files are present, their workspace paths are injected into the latest user message; call workspace_read(path) before answering file-content questions.",
            "Run shell/bash/python/node/npm/pip commands, generated scripts, dependency installs, and Skill helper scripts ONLY with sandbox_exec. Never use host shell/terminal/MCP execution tools for code execution; unsafe host execution tools are intentionally blocked and will return an error telling you to use sandbox_exec. sandbox_exec starts in this conversation's own directory, where `ls` shows work/ (scratch), artifacts/ (user-visible output) and uploads/ (the user's files). If sandbox_exec creates a user-visible file outside artifacts/ (for example in work/ or /tmp), publish that one file with sandbox_artifact_copy so it appears in this conversation's artifact panel. If sandbox_exec reports Docker is unavailable, call docker_engine_setup(action='check') first; it auto-starts an installed-but-stopped daemon and returns host OS/package-manager info from /etc/os-release. Then use docker_engine_setup(action='install', confirm_host_install=true) only after the user explicitly asks SlotFlow to install Docker Engine. If automatic install is disabled or sudo cannot run non-interactively, use docker_engine_setup(action='install_script') and tell the user to run the returned script.",
            "When you need input from the user before you can proceed — an ambiguous or underspecified request, a required preference, or a risky/irreversible action — you MUST call ask_clarification with 2-4 concise options. It renders an interactive picker (with a free-text 'other' option) that the user clicks; do NOT instead write your questions as plain message text and wait for a reply. If several things are unknown, ask the single most blocking question via ask_clarification first rather than a long plain-text questionnaire. Still skip it when a reasonable default is obvious — don't over-ask.",
            "Every user-visible file MUST be produced with artifact_write or, for files generated inside Docker, sandbox_artifact_copy — reports, charts, HTML/Markdown pages, visualizations, comparison tables, interactive demos, code previews. These are the paths that place files in the artifact panel; files live in this conversation's artifact folder next to the user's uploads. Do NOT create user-facing deliverables with the filesystem MCP server or any other write path — files written that way will NOT appear in the artifact panel. Never claim you saved a file unless you actually called artifact_write or sandbox_artifact_copy for it.",
            "Decide when a result deserves an artifact. Create one for SUBSTANTIAL, STANDALONE deliverables the user will keep, open, share, or iterate on — full reports/documents, complete HTML pages or apps, rendered charts/diagrams/visualizations, slide decks, large or structured datasets, and long or multi-file code. Do NOT create an artifact for ordinary conversational answers — a short explanation, a brief comparison or small table, a few bullet points, or a short snippet meant to be read in context; answer those inline in the message. Rule of thumb: use artifact_write when the output is longer than roughly a screenful, is a complete document/page/app, or the user asked to generate/export a file; otherwise reply inline.",
            "For complex planning workflows with human approval steps, create the final approved plan/report as an artifact with artifact_write after the required approval is received. Keep the chat reply short and point to the saved artifact path returned by the tool.",
            "Installed skills or MCP servers may become reliably available on the next run after runtime refresh.",
            "</slotflow-extension-tools>",
        ]
    )
    orchestration_lines: list[str] = [
        "Operating procedure for non-trivial tasks — work through it; do NOT jump straight to a one-shot answer:",
        "- If something blocking is ambiguous or needs the user's choice, call ask_clarification FIRST (the interactive picker), instead of guessing or writing plain-text questions.",
        "- If you ALREADY asked for clarification earlier in this conversation and the user repeats the request or replies without choosing, do NOT ask again and do NOT debate options in your reply. Pick the single most reasonable interpretation (use long-term memory if relevant), state it in ONE short sentence (e.g. '我先按 X 来做'), and go straight to the work.",
        "- Keep the final answer clean: never include meta-commentary about your own indecision, apologies for confusion, or 'let me think about whether to ask'. Decide, state the assumption in one line, deliver.",
        "- For a specialized, domain, or expert task, call skill_match before doing the work to find a relevant Skill.",
    ]
    if features.subagent_enabled:
        orchestration_lines.extend(
            [
                "- When the task splits into INDEPENDENT parts (research multiple items, evaluate multiple options, build multiple components), delegate each to a sub-agent via task_tool and run them in parallel, then synthesize the results yourself — don't do every part sequentially in one thread.",
                "- Delegation is ONE call: task_tool(agent_name=..., task=..., ...). The agent_name catalog is in <slotflow-subagents> below — there is no list/search round-trip to make first.",
                "- Pass role_query when a precise professional role would help. The role library is English-only, so query it in ENGLISH by profession ('penetration tester', 'tax accountant') even when the conversation is in another language; SlotFlow injects at most ONE matching role template into that child run. Leave it empty when the functional agent_name is enough.",
                "- Browser automation is NOT available to you directly: delegate it with task_tool(agent_name='browser', ...) and describe the goal (which site, what to extract or do). The child owns the browser tools and returns only the result, keeping page dumps out of this context.",
                "- task_tool.tool_spaces may grant at most three named spaces (workspace/sandbox/browser/network/documents/extensions/memory). Never use all or wildcard; split cross-domain work instead. Child agents have no todo, clarification/HITL, memory middleware, or recursive task_tool.",
            ]
        )
    sections.extend(
        ["", "<slotflow-operating-procedure>", *orchestration_lines, "</slotflow-operating-procedure>"]
    )
    if features.subagent_enabled:
        # 子代理目录是**静态文本**(6 个功能画像 + 8 个角色领域),不是工具。它随 system 前缀
        # 一起被缓存,模型读一眼就能直接 task_tool,省掉曾经的 subagent_list /
        # subagent_role_search 两次工具往返。角色检索下沉进 task_tool 的 role_query 参数。
        catalog = build_subagent_catalog_prompt(harness_config.subagent_config)
        if catalog:
            sections.extend(["", catalog])
    sections.extend(
        [
            "",
            "<slotflow-context-archive>",
            "Complete canonical message and tool history is retained outside the compact model view.",
            "When a compaction summary lacks exact older details, call context_archive_search and context_archive_read instead of guessing.",
            "Archive tools are read-only and scoped to the current LangGraph thread state.",
            "</slotflow-context-archive>",
        ]
    )
    sections.extend(
        build_mcp_status_prompt(
            harness_config.mcp_config,
            proxy_available=mcp_proxy_available,
        )
    )
    sections.extend(build_agent_reach_status_prompt(harness_config))
    sections.extend(build_markitdown_status_prompt(harness_config))
    skills_prompt = build_skills_prompt(enabled_skills)
    if skills_prompt:
        sections.extend(["", skills_prompt])
    return "\n".join(sections)


def build_markitdown_status_prompt(harness_config: SlotFlowHarnessConfig) -> list[str]:
    """Describe the single workspace-local document conversion boundary."""

    return [
        "",
        "<slotflow-markitdown-status>",
        f"enabled={harness_config.markitdown_config.enabled}",
        f"vision_enabled={harness_config.markitdown_config.vision_enabled}",
        "Use convert_file_to_markdown for PDF, Office, image, audio, HTML/data, EPUB, and archive content when workspace_read is insufficient.",
        "The tool accepts workspace-relative local paths only. Prefer output_path for large results. Vision OCR automatically uses a compatible selected model or dedicated configured client; heed warnings when neither is available.",
        "</slotflow-markitdown-status>",
    ]


def build_agent_reach_status_prompt(harness_config: SlotFlowHarnessConfig) -> list[str]:
    """Describe the read-only Agent Reach host bridge without exposing host execution."""

    enabled = (
        harness_config.agent_reach_config.enabled
        and harness_config.sandbox_config.network_enabled
    )
    return [
        "",
        "<slotflow-agent-reach-status>",
        f"enabled={enabled}",
        "Agent Reach is a fixed read-only host bridge, not a shell and not part of Docker.",
        "When enabled, call agent_reach_status before multi-platform research, then use the narrow agent_reach_* tool matching the source. Never request credentials or claim an unavailable channel worked.",
        "Installation and refresh are user-owned bootstrap actions; model tools cannot install, update, configure, post, comment, or mutate remote state.",
        "</slotflow-agent-reach-status>",
    ]


def build_mcp_status_prompt(mcp_config, *, proxy_available: bool = True) -> list[str]:
    """Describe configured MCP servers and the fixed two-tool MCP boundary.

    `proxy_available` 反映本次运行是否真的绑上了 `mcp_docs`/`mcp_call`（没有可代理的
    MCP 工具时不会绑）。提示词必须和实际工具面一致——描述一个不存在的工具，模型就会去调它。
    """

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
    if proxy_available:
        sections.extend(
            [
                "MCP tools are NOT bound as individual tools. However many servers are enabled, you "
                "always see exactly two: mcp_docs and mcp_call. This keeps your tool schema list "
                "identical from turn 1 to the end of the conversation.",
                "Two-step usage: call mcp_docs(query=..., server=...) first to read the generated "
                "manual for the relevant server (tool names, required arguments, types), then call "
                "mcp_call(server=..., tool=..., arguments={...}) with exactly those argument names.",
                "Never invent an MCP tool name or argument from the server name alone — read "
                "mcp_docs first. mcp_docs with no arguments lists every server and its tool count.",
            ]
        )
    else:
        sections.append(
            "No MCP tool is reachable in this run: either no server connected, or the only "
            "connected server is the browser one handled by the browser sub-agent. Do not call "
            "mcp_docs/mcp_call — they are not bound."
        )
    sections.extend(
        [
            "Browser automation runs through the browser sub-agent (task_tool), not mcp_call.",
            "</slotflow-mcp-status>",
        ]
    )
    return sections
