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

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig, RunnableLambda

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from app.chat.litellm_provider import (
    is_retryable_infra_error,
    repair_streamed_tool_call_names,
    sanitize_reasoning_message,
)
from app.chat.models import RunContext
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.sandbox import SlotFlowSandboxConfig, build_slotflow_workspace
from app.harness.sandbox.workspace import SlotFlowWorkspace
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
    run_triage,
    should_skip_triage_model_call,
)
from app.harness.steps.dangling_tool_call import repair_dangling_tool_calls
from app.harness.steps.long_term_memory import (
    aexplicit_save_update,
    aretrieve_memories,
    build_memory_prompt,
    maybe_schedule_extraction,
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
    consume_todo_enforcement,
    route_after_model_has_enforcement,
    todo_enforcement_update,
    todo_parallel_call_guard,
    todo_reminder_update,
)
from app.harness.steps.tool_output_offload import maybe_offload_tool_message
from app.harness.steps.tool_safety import (
    build_error_tool_message,
    build_unknown_tool_error_message,
)
from app.harness.steps.uploads import uploads_update
from app.harness.utils import latest_user_message_text, message_content_text, message_role

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
        initial_tool_names: frozenset[str] | None = None,
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
        self.initial_tool_names = initial_tool_names or frozenset(tool.name for tool in tools)
        self.tool_by_name = {tool.name: tool for tool in tools}


def _message_prefix_signature(messages: list[Any]) -> str:
    digest = hashlib.sha256()
    for message in messages:
        digest.update(message_role(message).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(message_content_text(message).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        message_id = getattr(message, "id", None)
        if message_id is not None:
            digest.update(str(message_id).encode("utf-8", errors="replace"))
        digest.update(b"\n")
    return digest.hexdigest()


def project_with_context_epoch(
    canonical_input: list[Any],
    epoch: Any,
) -> tuple[list[Any], bool]:
    """Project the compacted epoch prefix + appended tail; report whether the epoch was used.

    Returns ``(projected_messages, epoch_used)``. When ``epoch`` is a valid dict whose
    ``source_signature`` still matches ``canonical_input[:source_count]`` (both sides computed
    over the SAME repaired view), the compacted ``epoch.messages`` replaces the prefix and the
    newer messages are appended verbatim — so a compaction happens once and later turns simply
    append. ``epoch_used`` is False only when a present epoch went stale (caller should clear it).
    """

    if not isinstance(epoch, dict):
        return canonical_input, True
    source_count = epoch.get("source_message_count")
    epoch_messages = epoch.get("messages")
    source_signature = epoch.get("source_signature")
    if (
        isinstance(source_count, int)
        and 0 <= source_count <= len(canonical_input)
        and isinstance(epoch_messages, list)
        and source_signature == _message_prefix_signature(canonical_input[:source_count])
    ):
        return [*epoch_messages, *canonical_input[source_count:]], True
    return canonical_input, False


# ---------------------------------------------------------------------------
# prepare node: runs once per turn (all before_agent logic)
# ---------------------------------------------------------------------------


def make_prepare_node(inputs: _GraphInputs):
    flags = inputs.config_flags

    async def prepare(
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
            memories = await aretrieve_memories(
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
        if should_skip_triage_model_call(latest_user_message_text(messages)):
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

        # Per-step control context (todo reminder / enforcement retry). These are
        # SlotFlow-internal instructions to the model and must ride the `system_prompt`
        # string channel, never a message object: the v3 messages projection streams
        # every fresh message object it sees (so a control HumanMessage surfaces as
        # user-visible content), and the `messages` channel is additionally persisted
        # and replayed by the checkpointer. Same boundary rule as skills preflight (§29);
        # this is the root fix for the 2026-07-15 live enforcer-text leak.
        step_control_blocks: list[str] = []
        if flags.todo_enabled and inputs.features.plan_enabled and inputs.tools:
            reminder_text = todo_reminder_update(state=state)
            if reminder_text:
                step_control_blocks.append(reminder_text)
        if flags.todo_enabled and inputs.tools:
            pending_enforcement, enforcement_clear = consume_todo_enforcement(state)
            if pending_enforcement:
                step_control_blocks.append(pending_enforcement)
                updates.update(enforcement_clear)

        # Model-input projection (official pre_model_hook convention): recompute from the
        # canonical `messages` on EVERY step. `llm_input_messages` is a plain last-write
        # channel that `agent` prefers over `messages`; writing it only on some steps
        # left a checkpointed stale snapshot that hid later tool results and user turns.
        canonical_input = repair_dangling_tool_calls(messages)
        epoch = state.get("context_epoch")
        projected_input, epoch_used = project_with_context_epoch(canonical_input, epoch)
        if isinstance(epoch, dict) and not epoch_used:
            updates["context_epoch"] = None
        updates["llm_input_messages"] = projected_input

        # Compose the STABLE system prompt for this step: base + skills preflight only.
        # Volatile per-turn/per-step context (recalled long-term memory + todo control) is
        # intentionally kept OUT of the system prefix and moved to a trailing model-input
        # suffix (below). Rationale: `tools → system → messages` is the provider's cacheable
        # prefix; putting memory/todo in `system` made it change every turn/step and blew
        # away the whole prefix cache. Appending them AFTER all messages keeps the prefix
        # byte-stable and perturbs only the tail. (Manus: keep the prompt prefix stable +
        # append-only.)
        system_sections: list[str] = [inputs.system_prompt]
        slotflow = state.get("slotflow") or {}
        skills_preflight = (
            slotflow.get("skills_preflight")
            if isinstance(slotflow, dict)
            else None
        )
        if isinstance(skills_preflight, dict):
            system_sections.append(format_preflight(skills_preflight))
        composed = "\n\n".join(part for part in system_sections if part)
        # Recompute-per-step, same lesson as `llm_input_messages` above: writing only when it
        # differs from the BASE prompt leaves the previous step's snapshot stale in state.
        updates["system_prompt"] = composed or inputs.system_prompt

        # Trailing model-input suffix (recalled memory + per-step todo control). Rides its own
        # plain string channel like `system_prompt` — never the `messages` channel — so it is
        # neither streamed to the user (v3 messages projection) nor persisted/replayed by the
        # checkpointer (same leak boundary as the 2026-07-15 fix). `agent` appends it as a
        # trailing user-role <system-reminder> after every conversation message; summarization/epoch
        # (which read `llm_input_messages`, already computed above) therefore never fold it in.
        suffix_sections: list[str] = []
        if flags.long_term_memory_enabled and inputs.memory_store is not None:
            memories = state.get("retrieved_memories") or []
            if memories:
                suffix_sections.append(
                    build_memory_prompt(memories, tools_enabled=bool(inputs.tools))
                )
        suffix_sections.extend(step_control_blocks)
        updates["model_input_suffix"] = (
            "\n\n".join(part for part in suffix_sections if part) or None
        )
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
        # Compute the epoch source over the SAME repaired view that `pre_model` re-derives on
        # every later turn (`repair_dangling_tool_calls(messages)`). Using the RAW messages here
        # made the signature mismatch whenever history had a dangling tool call, so the epoch was
        # reset EVERY turn → summarization re-fired every turn → the fixed keep-window slid and
        # older messages (e.g. the user's earlier turn) were dropped. Aligning the views lets the
        # epoch actually be reused, so compaction happens once and new turns simply append.
        canonical_messages = repair_dangling_tool_calls(list(state.get("messages") or []))
        return {
            "llm_input_messages": model_input,
            "context_epoch": {
                "source_message_count": len(canonical_messages),
                "source_signature": _message_prefix_signature(canonical_messages),
                "messages": model_input,
            },
        }

    return summarize


_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "too many tokens",
    "input content is too long",
    "??????",
    "?????",
    "??????",
)


def is_context_overflow_error(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(is_context_overflow_error(item) for item in error.exceptions)
    text = f"{type(error).__name__}: {error}".casefold()
    return any(marker.casefold() in text for marker in _CONTEXT_OVERFLOW_MARKERS)


def emergency_context_projection(messages: list[Any], *, attempt: int) -> list[Any]:
    """Shrink only model input after a provider overflow; canonical state stays intact."""

    if not messages:
        return []
    divisor = max(2, attempt + 1)
    keep = max(4, len(messages) // divisor)
    projected = repair_dangling_tool_calls(messages[-keep:])
    return projected or [messages[-1]]


# ---------------------------------------------------------------------------
# agent node: pure model call
# ---------------------------------------------------------------------------


def _model_input_suffix_message(suffix: str) -> HumanMessage:
    """把易变的尾部上下文(召回记忆 / todo 控制)包成**用户角色**的 <system-reminder> 追加消息。

    用 user 角色而非 system:让送进模型的消息序列**始终以 user/tool 结尾**——这是所有 OpenAI
    兼容 provider 都接受的生成形态(部分较严的中转会拒绝"最后一条是 system")。外层 <system-reminder>
    让模型仍按"带外系统提示"理解,而非用户原话。该消息在 agent 调用时即时构造,绝不写入 `messages`
    通道,因此既不流式泄漏给用户、也不被 checkpointer 回放(与 2026-07-15 泄漏修复同一边界)。
    """

    return HumanMessage(content=f"<system-reminder>\n{suffix}\n</system-reminder>")


def make_agent_node(inputs: _GraphInputs):
    def model_for_state(state: SlotFlowAgentState):
        promoted = set(state.get("promoted_tool_names") or [])
        active_names = inputs.initial_tool_names | promoted
        active_tools = [
            tool for name, tool in inputs.tool_by_name.items() if name in active_names
        ]
        return inputs.model.bind_tools(active_tools) if active_tools else inputs.model

    async def agent(
        state: SlotFlowAgentState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        messages = state.get("llm_input_messages") or state.get("messages") or []
        system_text = state.get("system_prompt") or inputs.system_prompt
        # Volatile context (memory/todo) rides as a trailing user-role <system-reminder>
        # AFTER all conversation messages: keeps the cacheable `system → messages` prefix
        # stable AND leaves the payload ending on a user message (provider-safe).
        suffix = state.get("model_input_suffix")
        suffix_messages = [_model_input_suffix_message(suffix)] if suffix else []
        bound_model = model_for_state(state)
        projected_messages = list(messages)
        retries = max(1, inputs.config_flags.context_overflow_max_retries)
        for attempt in range(retries + 1):
            try:
                response = await bound_model.ainvoke(
                    [SystemMessage(content=system_text), *projected_messages, *suffix_messages],
                    config,
                )
                response.name = "slotflow"
                repair_streamed_tool_call_names(response)
                sanitize_reasoning_message(response, provider=inputs.run_context.model_provider)
                return {"messages": [response]}
            except Exception as exc:
                if not is_context_overflow_error(exc) or attempt >= retries:
                    raise
                projected_messages = emergency_context_projection(
                    list(messages),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(
                    inputs.config_flags.context_overflow_retry_delay_seconds * (attempt + 1)
                )
        raise RuntimeError("context overflow retry loop exhausted")

    def agent_sync(
        state: SlotFlowAgentState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        messages = state.get("llm_input_messages") or state.get("messages") or []
        system_text = state.get("system_prompt") or inputs.system_prompt
        suffix = state.get("model_input_suffix")
        suffix_messages = [_model_input_suffix_message(suffix)] if suffix else []
        bound_model = model_for_state(state)
        response = bound_model.invoke(
            [SystemMessage(content=system_text), *messages, *suffix_messages], config
        )
        response.name = "slotflow"
        repair_streamed_tool_call_names(response)
        sanitize_reasoning_message(response, provider=inputs.run_context.model_provider)
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
    if route_after_model_has_enforcement(state):
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
            extractor = _build_extractor(inputs)
            save_update = await aexplicit_save_update(
                messages=messages,
                context=ctx,
                memory_store=inputs.memory_store,
                extractor=extractor,
            )
            if save_update is not None:
                slotflow.update(save_update.get("slotflow") or {})
            else:
                # 显式保存已发生时跳过后台抽取，避免同一轮产生近似重复记忆。
                maybe_schedule_extraction(
                    messages=messages,
                    context=ctx,
                    extractor=extractor,
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
    except (GraphBubbleUp, asyncio.CancelledError):
        # 两类"控制流,不是错误",绝不能转成 tool_execution_error:
        #  - GraphBubbleUp:工具里的 interrupt()(如 ask_clarification)靠它暂停图做 HITL——吞了图就不暂停、HITL 静默死;
        #  - CancelledError:用户点停止后一路从下往上拆连接(SSE 断 → CancelledError 传播 → 取消模型请求)——吞了停止按钮就失效。
        # CancelledError 是 BaseException、本就能穿过下面的 except Exception;这里显式并列,把"靠语言特性"写成"写明白",
        # 也防日后有人把 except Exception 放宽成 except BaseException 时意外吞掉取消。
        raise
    except Exception as exc:  # noqa: BLE001
        if is_retryable_infra_error(exc):
            # 限流/超时/连接/5xx 是瞬时基础设施抖动(通常来自 task_tool 子代理内部的模型调用):
            # 重抛让整轮干净失败、state 不留假失败,别把它当成永久的"工具失败"污染历史。
            raise
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
    except (GraphBubbleUp, asyncio.CancelledError):
        # 见 _slotflow_tool_safety_wrapper:GraphBubbleUp(HITL)与 CancelledError(用户停止)都是控制流,
        # 必须重抛、绝不能吞成 tool_execution_error,否则图不暂停 / 停止按钮静默失效。
        raise
    except Exception as exc:  # noqa: BLE001
        if is_retryable_infra_error(exc):
            # 见 _slotflow_tool_safety_wrapper:限流/超时/连接/5xx 瞬时错误重抛、不转永久工具失败。
            raise
        return build_error_tool_message(
            request.tool_call,
            error_type="tool_execution_error",
            message=str(exc),
            exception_type=exc.__class__.__name__,
        )


def make_tools_node(
    tools: list[BaseTool],
    *,
    initial_tool_names: frozenset[str] | None = None,
    workspace: SlotFlowWorkspace | None = None,
    offload_max_chars: int = 0,
) -> ToolNode:
    initial = initial_tool_names or frozenset(tool.name for tool in tools)

    def active(request: ToolCallRequest) -> bool:
        state = request.state if isinstance(request.state, dict) else {}
        promoted = set(state.get("promoted_tool_names") or [])
        name = request.tool_call.get("name")
        return isinstance(name, str) and name in (initial | promoted)

    def wrap(request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]) -> Any:
        if not active(request):
            return build_error_tool_message(
                request.tool_call,
                error_type="tool_not_activated",
                message="Activate this tool through its tool-space loader first.",
                exception_type="ToolNotActivated",
            )
        result = _slotflow_tool_safety_wrapper(request, handler)
        if workspace is not None and offload_max_chars > 0:
            result = maybe_offload_tool_message(
                result, workspace=workspace, max_chars=offload_max_chars
            )
        return result

    async def awrap(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if not active(request):
            return build_error_tool_message(
                request.tool_call,
                error_type="tool_not_activated",
                message="Activate this tool through its tool-space loader first.",
                exception_type="ToolNotActivated",
            )
        result = await _slotflow_async_tool_safety_wrapper(request, handler)
        if workspace is not None and offload_max_chars > 0:
            # 卸载做本地文件 IO，放线程池避免阻塞事件循环。
            result = await asyncio.to_thread(
                maybe_offload_tool_message,
                result,
                workspace=workspace,
                max_chars=offload_max_chars,
            )
        return result

    return ToolNode(
        tools,
        name="tools",
        handle_tool_errors=False,
        wrap_tool_call=wrap,
        awrap_tool_call=awrap,
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
    initial_tool_names: frozenset[str] | None = None,
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
        initial_tool_names=initial_tool_names,
    )

    graph = StateGraph(SlotFlowAgentState, context_schema=RunContext)
    graph.add_node("prepare", make_prepare_node(inputs))
    graph.add_node("triage_gate", make_triage_gate_node(inputs))
    graph.add_node("pre_model", make_pre_model_node(inputs))
    graph.add_node(SUMMARIZATION_NODE_NAME, make_summarization_node(inputs))
    agent_async, agent_sync = make_agent_node(inputs)
    graph.add_node("agent", RunnableLambda(agent_sync, afunc=agent_async, name="agent"))
    graph.add_node("post_model", make_post_model_node(inputs))
    # 超长工具结果卸载：仅在开关开启且阈值>0 时建工作区句柄，交给 tools 节点的 wrap/awrap。
    offload_max_chars = (
        int(getattr(inputs.config_flags, "tool_output_offload_max_chars", 0) or 0)
        if getattr(inputs.config_flags, "tool_output_offload_enabled", True)
        else 0
    )
    offload_workspace = (
        build_slotflow_workspace(inputs.sandbox_config) if offload_max_chars > 0 else None
    )
    graph.add_node(
        "tools",
        make_tools_node(
            tools,
            initial_tool_names=inputs.initial_tool_names,
            workspace=offload_workspace,
            offload_max_chars=offload_max_chars,
        ),
    )
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
