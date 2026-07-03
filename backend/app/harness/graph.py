"""SlotFlow harness graph: LangGraph node + edge orchestration.

重构（2026-06-30，分支 refactor/langgraph-node-edge-graph）：把 LangChain
`create_agent` + middleware 单 ReAct 循环改为 LangGraph 原生 `StateGraph`（显式 node +
edge）。链路严格按 `docs/refactor-plan.md` §2 拓扑运行：

    START → prepare → triage_gate → pre_model → agent → post_model
                                                                  ├─ tools → pre_model
                                                                  ├─ pre_model (todo enforcement)
                                                                  └─ finalize → END

中间件逻辑已抽成 `app/harness/steps/*` 的无状态纯函数，节点直接调用，顺序由边显式
保证（不再依赖 middleware registry 的 append 顺序）。HITL 仍用 LangGraph 原生
`interrupt()`/`Command(resume=...)`：强制门在 `triage_gate`，自愿工具在 `tools`。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import RemoveMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph._internal._runnable import RunnableCallable
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.steps.artifact_discovery import (
    artifact_baseline,
    artifact_finalize_update,
)
from app.harness.steps.clarify_gate import (
    already_clarified,
    clarify_mode_enabled,
    clarify_via_interrupt,
    is_fresh_user_turn,
    latest_user_text,
    run_triage,
    should_skip_triage_model_call,
)
from app.harness.steps.dangling_tool_call import repair_dangling_tool_calls
from app.harness.steps.long_term_memory import (
    append_memory_system_message,
    explicit_save_update,
    maybe_schedule_extraction,
    retrieve_memories,
)
from app.harness.steps.runtime_summary import runtime_summary_update
from app.harness.steps.skills_preflight import (
    default_find_skills,
    format_preflight,
    skills_preflight_update,
)
from app.harness.steps.subagent_limit import cap_subagent_calls
from app.harness.steps.summarization import build_summarization_middleware
from app.harness.steps.todo import (
    latest_message_is_todo_enforcer,
    todo_enforcement_update,
    todo_parallel_call_guard,
    todo_reminder_update,
)
from app.harness.steps.tool_safety import (
    build_error_tool_message,
    build_unknown_tool_error_message,
)
from app.harness.steps.uploads import uploads_update

if TYPE_CHECKING:
    from langgraph.types import Checkpointer


# ---------------------------------------------------------------------------
# Shared node factory config (kept on the node closure, not on instance state)
# ---------------------------------------------------------------------------


class _GraphInputs:
    """Bag of dependencies injected into node closures at compile time."""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        tools: list[BaseTool],
        system_prompt: str,
        run_context: RunContext,
        features: SlotFlowHarnessFeatures,
        sandbox_config: SlotFlowSandboxConfig,
        memory_store: Any,
        skills_root: Any,
        skills_config_store: Any,
        config_flags: Any,
        max_results_memories: int,
    ) -> None:
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.run_context = run_context
        self.features = features
        self.sandbox_config = sandbox_config
        self.memory_store = memory_store
        self.skills_root = skills_root
        self.skills_config_store = skills_config_store
        self.config_flags = config_flags
        self.max_results_memories = max_results_memories


# ---------------------------------------------------------------------------
# prepare node: runs once per turn (all before_agent logic)
# ---------------------------------------------------------------------------


def make_prepare_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    def prepare(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        ctx = runtime.context or inputs.run_context
        updates: dict[str, Any] = {}
        slotflow = dict(state.get("slotflow") or {})
        messages = list(state.get("messages") or [])

        # runtime summary
        if flags.runtime_summary_enabled:
            summary = runtime_summary_update(
                state={"slotflow": slotflow},
                context=ctx,
                features=inputs.features,
            )
            if summary is not None:
                slotflow.update(summary["slotflow"])

        # uploads injection
        if flags.uploads_enabled:
            upload_state = {"messages": messages, "slotflow": slotflow}
            upload_update = uploads_update(
                state=upload_state,
                context=ctx,
                sandbox_config=inputs.sandbox_config,
            )
            if upload_update is not None:
                messages = upload_update["messages"]
                slotflow.update(upload_update.get("slotflow") or {})

        # skills preflight
        if flags.skills_preflight_enabled:
            preflight_state = {"messages": messages, "slotflow": slotflow}
            preflight = skills_preflight_update(
                state=preflight_state,
                sandbox_config=inputs.sandbox_config,
                skills_root=inputs.skills_root,
                skills_config_store=inputs.skills_config_store,
                finder=default_find_skills,
            )
            if preflight is not None:
                slotflow.update(preflight.get("slotflow") or {})

        # long-term memory retrieval -> system prompt section (stored for pre_model)
        memories: list[Any] = []
        if flags.long_term_memory_enabled and inputs.memory_store is not None:
            memories = retrieve_memories(
                messages=messages,
                context=ctx,
                memory_store=inputs.memory_store,
                max_results=inputs.max_results_memories,
            )
            if memories:
                slotflow["long_term_memory"] = [
                    memory.model_dump(mode="json") for memory in memories
                ]

        updates["messages"] = messages
        updates["slotflow"] = slotflow
        # Stash baseline artifacts + memories for finalize/pre_model.
        if flags.artifact_discovery_enabled:
            updates["artifacts_baseline"] = artifact_baseline(inputs.sandbox_config)
        if memories:
            updates["retrieved_memories"] = memories
        return updates

    return prepare


# ---------------------------------------------------------------------------
# triage_gate node: first step only, pro/ultra forced clarification
# ---------------------------------------------------------------------------


def make_triage_gate_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    async def triage_gate(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        if not (flags.clarify_gate_enabled and inputs.tools):
            return {}
        ctx = runtime.context or inputs.run_context
        if not clarify_mode_enabled(getattr(ctx, "mode", None)):
            return {}
        messages = list(state.get("messages") or [])
        if not is_fresh_user_turn(messages):
            return {}
        if already_clarified(messages):
            return {}
        if should_skip_triage_model_call(latest_user_text(messages)):
            return {}
        triage = await run_triage(messages=messages, model=inputs.model)
        if triage is None:
            return {}
        if not triage.get("actionable", True):
            return clarify_via_interrupt(triage, ctx)
        return {}

    return triage_gate


# ---------------------------------------------------------------------------
# pre_model node: every step (before_model + wrap_model_call request transforms)
# ---------------------------------------------------------------------------


def make_pre_model_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    async def pre_model(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        updates: dict[str, Any] = {}

        # todo reminder: dynamic state recap only, not a static system-prompt constraint.
        if flags.todo_enabled and inputs.features.plan_enabled and inputs.tools:
            reminder = todo_reminder_update(state=state)
            if reminder is not None:
                updates["messages"] = reminder["messages"]
                # The reminder must reach the model THIS step, so it belongs in the
                # projection below too (the reducer append alone is invisible to `agent`
                # whenever `llm_input_messages` is set).
                messages = messages + list(reminder["messages"])

        # Model-input projection (official pre_model_hook convention): recompute from the
        # canonical `messages` on EVERY step. `llm_input_messages` is a plain last-write
        # channel that `agent` prefers over `messages`; writing it only on some steps
        # left a checkpointed stale snapshot that hid later tool results and user turns.
        updates["llm_input_messages"] = repair_dangling_tool_calls(messages)

        # Compose the final system prompt for this step: base + memory section.
        system_sections: list[str] = [inputs.system_prompt]
        slotflow = state.get("slotflow") or {}
        skills_preflight = (
            slotflow.get("skills_preflight")
            if isinstance(slotflow, dict)
            else None
        )
        if isinstance(skills_preflight, dict):
            system_sections.append(format_preflight(skills_preflight))
        if flags.long_term_memory_enabled and inputs.memory_store is not None:
            memories = state.get("retrieved_memories") or []
            if memories:
                base_system = SystemMessage(content="\n\n".join(system_sections))
                enriched = append_memory_system_message(
                    base_system,
                    memories=memories,
                    tools_enabled=bool(inputs.tools),
                )
                system_sections = [enriched.content]
        composed = "\n\n".join(part for part in system_sections if part)
        if composed and composed != inputs.system_prompt:
            updates["system_prompt"] = composed
        return updates

    return pre_model



# ---------------------------------------------------------------------------
# summarization node: own node so the projection layer filters its stream
# ---------------------------------------------------------------------------

SUMMARIZATION_NODE_NAME = "SlotFlowSummarizationMiddleware"


def make_summarization_node(inputs: _GraphInputs):
    flags = inputs.config_flags
    summarization_mw = (
        build_summarization_middleware(
            inputs.model,
            trigger_tokens=flags.summarization_trigger_tokens,
            keep_messages=flags.summarization_keep_messages,
            trim_tokens_to_summarize=flags.summarization_trim_tokens,
        )
        if flags.summarization_enabled
        else None
    )

    async def summarize(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        if summarization_mw is None:
            return {}
        messages = list(state.get("llm_input_messages") or state.get("messages") or [])
        summary_update = await summarization_mw.abefore_model(
            {"messages": messages},
            runtime,
        )
        if summary_update is None:
            return {}
        summarized = summary_update.get("messages")
        if summarized is None:
            return {}
        # The middleware speaks the add_messages reducer protocol: its list starts with a
        # RemoveMessage(REMOVE_ALL_MESSAGES) sentinel. Only the `messages` channel has that
        # reducer. `llm_input_messages` is a plain channel fed verbatim to the model by
        # `agent`, and provider payload conversion raises TypeError on RemoveMessage
        # (verified on the OpenAI-compatible path, incl. DeepSeek) — so strip sentinels here.
        model_input = [
            message for message in summarized if not isinstance(message, RemoveMessage)
        ]
        return {"messages": summarized, "llm_input_messages": model_input}

    return summarize


# ---------------------------------------------------------------------------
# agent node: pure model call
# ---------------------------------------------------------------------------


def make_agent_node(inputs: _GraphInputs):
    bound_model = inputs.model.bind_tools(inputs.tools) if inputs.tools else inputs.model

    async def agent(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        messages = state.get("llm_input_messages") or state.get("messages") or []
        system_text = state.get("system_prompt") or inputs.system_prompt
        response = await bound_model.ainvoke(
            [SystemMessage(content=system_text), *messages], config
        )
        response.name = "slotflow"
        return {"messages": [response]}

    def agent_sync(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        messages = state.get("llm_input_messages") or state.get("messages") or []
        system_text = state.get("system_prompt") or inputs.system_prompt
        response = bound_model.invoke(
            [SystemMessage(content=system_text), *messages], config
        )
        response.name = "slotflow"
        return {"messages": [response]}

    return agent, agent_sync


# ---------------------------------------------------------------------------
# post_model node: subagent concurrency cap
# ---------------------------------------------------------------------------


def make_post_model_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    async def post_model(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if flags.todo_enabled and inputs.tools:
            guard = todo_parallel_call_guard(state=state)
            if guard is not None:
                updates.update(guard)
            enforcer = todo_enforcement_update(
                state=state,
                plan_enabled=inputs.features.plan_enabled,
            )
            if enforcer is not None:
                updates.update(enforcer)
        if flags.subagent_limit_enabled and inputs.features.subagent_enabled and inputs.tools:
            capped = cap_subagent_calls(
                state=state,
                max_concurrent=flags.subagent_max_concurrent,
            )
            if capped is not None:
                # If the subagent cap trimmed the AIMessage, the parallel-call guard above
                # (computed on the pre-trim message) is stale; prefer the cap result.
                updates = capped
        return updates

    return post_model


# ---------------------------------------------------------------------------
# route: conditional edges after post_model
# ---------------------------------------------------------------------------


def route_after_model(state: SlotFlowAgentState) -> str:
    if latest_message_is_todo_enforcer(state):
        return "pre_model"
    decision = tools_condition(state)
    return "tools" if decision == "tools" else "finalize"


# ---------------------------------------------------------------------------
# finalize node: runs once per turn (all after_agent logic)
# ---------------------------------------------------------------------------


def make_finalize_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    async def finalize(
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        ctx = runtime.context or inputs.run_context
        slotflow = dict(state.get("slotflow") or {})
        updates: dict[str, Any] = {}

        # artifact discovery (after_agent new entries)
        if flags.artifact_discovery_enabled:
            baseline = state.get("artifacts_baseline") or set()
            artifact_update = artifact_finalize_update(
                state={"slotflow": slotflow},
                baseline_paths=baseline,
                sandbox_config=inputs.sandbox_config,
            )
            slotflow.update(artifact_update["slotflow"])

        # long-term memory explicit save + background extraction (after_agent)
        if flags.long_term_memory_enabled and inputs.memory_store is not None:
            messages = list(state.get("messages") or [])
            save_update = explicit_save_update(
                messages=messages,
                context=ctx,
                memory_store=inputs.memory_store,
            )
            if save_update is not None:
                slotflow.update(save_update.get("slotflow") or {})
            maybe_schedule_extraction(
                messages=messages,
                context=ctx,
                extractor=_build_extractor(inputs),
                memory_store=inputs.memory_store,
                proactive_extraction_enabled=flags.proactive_memory_extraction_enabled,
            )

        updates["slotflow"] = slotflow
        return updates

    return finalize


def _build_extractor(inputs: _GraphInputs):
    from app.harness.memory.extractor import SlotFlowMemoryExtractor

    return SlotFlowMemoryExtractor(inputs.model)


# ---------------------------------------------------------------------------
# tools node: ToolNode + SlotFlow tool-safety wrapper
# ---------------------------------------------------------------------------


def _slotflow_tool_safety_wrapper(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Any],
) -> Any:
    if request.tool is None:
        return build_unknown_tool_error_message(request.tool_call)
    try:
        return handler(request)
    except GraphBubbleUp:
        # interrupt() inside a tool (e.g. ask_clarification) raises GraphBubbleUp to pause
        # the graph for HITL. It MUST propagate — never convert it to a tool_execution_error,
        # or the graph never pauses and HITL silently dies. Same rule as triage_gate.
        raise
    except Exception as exc:  # noqa: BLE001
        return build_error_tool_message(
            request.tool_call,
            error_type="tool_execution_error",
            message=str(exc),
            exception_type=exc.__class__.__name__,
        )


async def _slotflow_async_tool_safety_wrapper(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[Any]],
) -> Any:
    if request.tool is None:
        return build_unknown_tool_error_message(request.tool_call)
    try:
        return await handler(request)
    except GraphBubbleUp:
        # See _slotflow_tool_safety_wrapper: interrupt() must propagate, not be swallowed.
        raise
    except Exception as exc:  # noqa: BLE001
        return build_error_tool_message(
            request.tool_call,
            error_type="tool_execution_error",
            message=str(exc),
            exception_type=exc.__class__.__name__,
        )


def make_tools_node(tools: list[BaseTool]) -> ToolNode:
    return ToolNode(
        tools,
        name="tools",
        handle_tool_errors=False,
        wrap_tool_call=_slotflow_tool_safety_wrapper,
        awrap_tool_call=_slotflow_async_tool_safety_wrapper,
    )


# ---------------------------------------------------------------------------
# graph assembly
# ---------------------------------------------------------------------------


def build_slotflow_graph(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    run_context: RunContext,
    features: SlotFlowHarnessFeatures,
    sandbox_config: SlotFlowSandboxConfig,
    memory_store: Any,
    skills_root: Any,
    skills_config_store: Any,
    config_flags: Any,
    checkpointer: "Checkpointer | None" = None,
):
    inputs = _GraphInputs(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        run_context=run_context,
        features=features,
        sandbox_config=sandbox_config,
        memory_store=memory_store,
        skills_root=skills_root,
        skills_config_store=skills_config_store,
        config_flags=config_flags,
        max_results_memories=5,
    )

    graph = StateGraph(SlotFlowAgentState, context_schema=RunContext)
    graph.add_node("prepare", make_prepare_node(inputs))
    graph.add_node("triage_gate", make_triage_gate_node(inputs))
    graph.add_node("pre_model", make_pre_model_node(inputs))
    graph.add_node(SUMMARIZATION_NODE_NAME, make_summarization_node(inputs))
    agent_async, agent_sync = make_agent_node(inputs)
    graph.add_node("agent", RunnableCallable(agent_sync, agent_async, name="agent"))
    graph.add_node("post_model", make_post_model_node(inputs))
    graph.add_node("tools", make_tools_node(tools))
    graph.add_node("finalize", make_finalize_node(inputs))

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "triage_gate")
    graph.add_edge("triage_gate", "pre_model")
    graph.add_edge("pre_model", SUMMARIZATION_NODE_NAME)
    graph.add_edge(SUMMARIZATION_NODE_NAME, "agent")
    graph.add_edge("agent", "post_model")
    graph.add_conditional_edges(
        "post_model",
        route_after_model,
        {"tools": "tools", "pre_model": "pre_model", "finalize": "finalize"},
    )
    graph.add_edge("tools", "pre_model")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
