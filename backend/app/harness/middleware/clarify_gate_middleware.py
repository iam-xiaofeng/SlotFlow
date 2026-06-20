"""Deterministic pre-answer constraints for pro/ultra runs.

Soft prompting cannot stop a model from one-shot-guessing an underspecified request,
nor reliably make it discover Skills or plan before acting. This middleware moves the
enforcement into the graph itself, on the FIRST model step of a fresh user turn:

1. **Clarify gate (pro + ultra)** — a cheap structured triage decides whether the request
   is actionable. If not, ``before_model`` ends the run with a synthesized
   ``ask_clarification`` AIMessage + matching clarification ToolMessage (built by
   ``build_clarification_payload``), so the projection layer surfaces the picker exactly
   like the real tool would. The model never runs, so it cannot fabricate — and there is no
   second model call (which is what broke DeepSeek thinking-mode's reasoning round-trip).
2. **Skill-first / plan-first (ultra)** — when the request IS actionable, the triage result
   is stashed and ``wrap_model_call`` injects a strong system directive: if the preflight
   matched an installed Skill, the model's first action must be ``skill_match``; else for a
   non-trivial task it must be ``write_todos``. We inject a directive (not a forced
   ``tool_choice``) because DeepSeek thinking-mode rejects forced tool choices.

Only the first step is constrained; it never gates twice in a thread (anti-loop) and fails
OPEN on any triage error.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.middleware.clarification_middleware import build_clarification_payload
from app.harness.state import SlotFlowAgentState


TriageFn = Callable[[str], dict[str, Any] | None]

_CLARIFY_MODES = {"pro", "ultra"}
_TRIAGE_STATE_KEY = "clarify_gate_triage"

_TRIAGE_SYSTEM = (
    "You are a routing classifier for an AI agent — NOT the agent. Given the user's latest "
    "request, decide whether the agent can proceed or must first ask ONE clarifying question. "
    "A request is NOT actionable when a blocking ambiguity, a missing required input, or an "
    "unstated key preference would force the agent to GUESS a materially different result. "
    "Prefer actionable=true whenever a reasonable default exists — do NOT over-ask. When you DO "
    "ask, the `options` MUST be your 2-4 BEST-GUESS concrete directions the user can one-click "
    "(the UI also gives the user a free-text box for 'none of these'). Also judge: needs_plan "
    "(non-trivial, multi-step), needs_subagent (the task has 2+ INDEPENDENT parts that could run "
    "in parallel), specialized (a domain / professional / expert task a Skill could help with). "
    "Respond with ONLY a compact JSON object, no prose, no markdown fences:\n"
    '{"actionable": bool, "clarification_type": '
    '"missing_info|ambiguous_requirement|approach_choice|risk_confirmation|suggestion", '
    '"question": "the single most blocking question", "options": ["2-4 best-guess directions"], '
    '"needs_plan": bool, "needs_subagent": bool, "specialized": bool}'
)


class SlotFlowClarifyGateMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Force clarification (pro+ultra) / skill-first / plan-first (ultra) on the first step."""

    name = "SlotFlowClarifyGateMiddleware"

    def __init__(
        self,
        *,
        model: Any = None,
        triage: TriageFn | None = None,
    ) -> None:
        self._model = model
        self._triage_fn = triage

    # --- clarify gate + triage (runs before the model, can end the run) ---------------

    @hook_config(can_jump_to=["end"])
    def before_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        # Sync runs are not used in production (SlotFlow streams via astream_events); the gate
        # only runs on the async path so a sync invocation degrades to no-op.
        return None

    @override
    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        try:
            return await self._gate(state, runtime)
        except Exception:  # noqa: BLE001 - never let the gate break a real run
            return None

    async def _gate(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        ctx = getattr(runtime, "context", None)
        mode = getattr(ctx, "mode", None)
        if mode not in _CLARIFY_MODES:
            return None
        messages = list(state.get("messages") or [])
        if not _is_fresh_user_turn(messages):
            return None

        triage = await self._triage(messages)
        if triage is None:
            return None

        if not triage.get("actionable", True) and not _already_clarified(messages):
            return _clarification_update(triage, ctx)

        # Actionable: stash triage so wrap_model_call can apply the ultra directive without a
        # second triage call. Merge to avoid clobbering skills_preflight in the slotflow dict.
        slotflow = dict(state.get("slotflow") or {})
        slotflow[_TRIAGE_STATE_KEY] = triage
        return {"slotflow": slotflow}

    # --- ultra skill-first / plan-first directive (modifies the model request) --------

    def wrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        try:
            directive = self._ultra_directive(request)
        except Exception:  # noqa: BLE001
            directive = None
        if directive is None:
            return await handler(request)
        base = request.system_message
        base_text = base.content if base is not None else ""
        new_system = SystemMessage(content=f"{base_text}\n\n{directive}".strip())
        return await handler(request.override(system_message=new_system))

    def _ultra_directive(self, request: ModelRequest[RunContext]) -> str | None:
        if _run_mode(request) != "ultra":
            return None
        if not _is_fresh_user_turn(list(request.messages or [])):
            return None
        triage = _slotflow(request.state).get(_TRIAGE_STATE_KEY) or {}

        lines: list[str] = []
        # The skills preflight only runs for specialized requests (it detects domain terms),
        # so its presence is a more reliable "specialized" signal than the conservative triage.
        specialized = (
            triage.get("specialized")
            or _has_installed_skill_match(request.state)
            or _skills_preflight_ran(request.state)
        )
        if specialized and _tool_available(request, "skill_match"):
            lines.append(
                "- Call skill_match FIRST to check for a relevant INSTALLED Skill. If none is "
                "installed, use find-skills and search_skill_repos (prefer high-star GitHub "
                "repos) to look for an installable one before doing the work — do not answer "
                "from memory before checking."
            )
        if (
            triage.get("needs_plan")
            and not _has_todos(request.state)
            and _tool_available(request, "write_todos")
        ):
            lines.append(
                "- Call write_todos with a concise 3-7 step plan before doing the work, then "
                "work the list."
            )
        if triage.get("needs_subagent") and _tool_available(request, "task_tool"):
            lines.append(
                "- This task has INDEPENDENT parts: delegate each independent part to a "
                "sub-agent via task_tool and run them in parallel, then synthesize the results "
                "yourself — do NOT do every part sequentially in one thread."
            )
        if not lines:
            return None
        body = "\n".join(lines)
        return (
            f"<slotflow-ultra-enforcement>\nBefore doing the work, you MUST:\n{body}\n"
            "</slotflow-ultra-enforcement>"
        )

    async def _triage(self, messages: list[Any]) -> dict[str, Any] | None:
        user_text = _latest_user_text(messages)
        if not user_text:
            return None
        if self._triage_fn is not None:
            return self._triage_fn(user_text)
        model = self._model
        if model is None or not hasattr(model, "ainvoke"):
            return None
        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content=_TRIAGE_SYSTEM),
                    HumanMessage(content=user_text[:4000]),
                ],
                # Detach from the parent run so this internal classification call is NOT
                # captured by astream_events (otherwise its tokens pollute the user stream).
                config={"callbacks": []},
            )
        except Exception:  # noqa: BLE001 - triage failure must fail open
            return None
        return _parse_triage(_message_text(getattr(response, "content", "")))


def _clarification_update(triage: dict[str, Any], ctx: RunContext | None) -> dict[str, Any]:
    question = _clean(triage.get("question")) or "请补充需要确认的信息,我才能继续。"
    clarification_type = _clean(triage.get("clarification_type")) or "missing_info"
    options = [opt for opt in (_clean(item) for item in _as_list(triage.get("options"))) if opt][:4]

    seed = f"{getattr(ctx, 'run_id', '')}:{question}"
    call_id = f"clarifygate-{sha256(seed.encode('utf-8')).hexdigest()[:12]}"

    tool_args = {"question": question, "clarification_type": clarification_type, "options": options}
    tool_call = {"name": "ask_clarification", "args": tool_args, "id": call_id, "type": "tool_call"}
    payload = build_clarification_payload(tool_call, run_context=ctx)

    # AIMessage carries reasoning_content="" so thinking-mode history stays valid; the matching
    # ToolMessage is what the projection layer turns into clarification.requested. jump_to=end
    # ends the run with no model call (the wrong mechanism would break DeepSeek's reasoning
    # round-trip — see git history).
    ai_message = AIMessage(
        content="",
        tool_calls=[tool_call],
        additional_kwargs={"reasoning_content": ""},
    )
    tool_message = ToolMessage(
        id=payload["id"],
        content=json.dumps(payload, ensure_ascii=False),
        name="ask_clarification",
        tool_call_id=call_id,
    )
    return {"jump_to": "end", "messages": [ai_message, tool_message]}


def _is_fresh_user_turn(messages: list[Any]) -> bool:
    """The gate only constrains the FIRST model step, i.e. the user just spoke."""

    if not messages:
        return False
    return _is_human(messages[-1])


def _already_clarified(messages: list[Any]) -> bool:
    """Anti-loop: if a clarification was already asked in this thread, never re-gate."""

    for message in messages:
        if isinstance(message, ToolMessage) and getattr(message, "name", None) == "ask_clarification":
            return True
        if isinstance(message, dict) and message.get("name") == "ask_clarification":
            return True
    return False


def _has_installed_skill_match(state: Any) -> bool:
    preflight = _slotflow(state).get("skills_preflight")
    if not isinstance(preflight, dict):
        return False
    return bool(preflight.get("installed_matches"))


def _skills_preflight_ran(state: Any) -> bool:
    """The preflight only runs for specialized requests, so its presence == specialized."""

    return isinstance(_slotflow(state).get("skills_preflight"), dict)


def _has_todos(state: Any) -> bool:
    if isinstance(state, dict):
        return bool(state.get("todos"))
    return bool(getattr(state, "todos", None))


def _tool_available(request: ModelRequest[RunContext], name: str) -> bool:
    for tool in request.tools or []:
        tool_name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        if tool_name == name:
            return True
    return False


def _slotflow(state: Any) -> dict[str, Any]:
    if isinstance(state, dict):
        value = state.get("slotflow")
    else:
        value = getattr(state, "slotflow", None)
    return value if isinstance(value, dict) else {}


def _run_context(request: ModelRequest[RunContext]) -> RunContext | None:
    runtime = getattr(request, "runtime", None)
    return getattr(runtime, "context", None)


def _run_mode(request: ModelRequest[RunContext]) -> str | None:
    ctx = _run_context(request)
    return getattr(ctx, "mode", None)


def _is_human(message: Any) -> bool:
    if isinstance(message, HumanMessage):
        return True
    return isinstance(message, dict) and message.get("role") in ("user", "human")


def _latest_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if _is_human(message):
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            return _message_text(content)
    return ""


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


def _parse_triage(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return _clean(value.get("label") or value.get("text") or value.get("value"))
    return " ".join(str(value).split())
