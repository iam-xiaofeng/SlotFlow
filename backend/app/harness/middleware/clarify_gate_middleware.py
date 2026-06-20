"""Deterministic clarify gate for pro/ultra runs.

Soft prompting cannot stop a model from one-shot-guessing an underspecified request.
This middleware moves *only the clarify decision* into the graph, on the FIRST model step
of a fresh user turn: a cheap structured triage decides whether the request is actionable.
If not, ``abefore_model`` calls LangGraph native ``interrupt()`` with a clarification payload
(built by ``build_clarification_payload``) so the agent adapter surfaces the picker exactly
like the real ``ask_clarification`` tool would. The model never runs before the user answers,
so it cannot fabricate.

When the user answers, the graph resumes with ``Command(resume=<answer>)``; ``interrupt()``
returns that answer and the gate injects it as a HumanMessage so the model can proceed. The
answer is therefore part of the conversation directly — there is no "rewrite the answered tool
message" step. NOTE: on resume LangGraph REPLAYS ``before_model`` from the top, so the cheap
triage call runs a second time before ``interrupt()`` returns the buffered answer; this is
benign (callbacks are detached, fail-open) and is the price of interrupting inside a hook.

Skill-first / plan-first / delegate guidance lives in the ``<slotflow-operating-procedure>``
system prompt (``harness/builder.py``), NOT here: on DeepSeek thinking-mode such directives
are necessarily soft (no forced ``tool_choice``), so duplicating them as a per-turn injection
added cost and prompt conflict without changing behaviour. This middleware does ONE thing.

Only the first step is constrained; it never gates twice in a thread (anti-loop) and fails
OPEN on any triage error.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.chat.models import RunContext
from app.harness.clarification import (
    build_clarification_payload,
    clarification_answer_text,
)
from app.harness.state import SlotFlowAgentState


TriageFn = Callable[[str], dict[str, Any] | None]

_CLARIFY_MODES = {"pro", "ultra"}

_TRIAGE_SYSTEM = (
    "You are a routing classifier for an AI agent — NOT the agent. Given the user's latest "
    "request, decide whether the agent can proceed or must first ask ONE clarifying question. "
    "A request is NOT actionable when a blocking ambiguity, a missing required input, or an "
    "unstated key preference would force the agent to GUESS a materially different result. "
    "Prefer actionable=true whenever a reasonable default exists — do NOT over-ask. When you DO "
    "ask, the `options` MUST be your 2-4 BEST-GUESS concrete directions the user can one-click "
    "(the UI also gives the user a free-text box for 'none of these'). "
    "Respond with ONLY a compact JSON object, no prose, no markdown fences:\n"
    '{"actionable": bool, "clarification_type": '
    '"missing_info|ambiguous_requirement|approach_choice|risk_confirmation|suggestion", '
    '"question": "the single most blocking question", "options": ["2-4 best-guess directions"]}'
)


class SlotFlowClarifyGateMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Force clarification (pro+ultra) on the first model step of a fresh user turn."""

    name = "SlotFlowClarifyGateMiddleware"

    def __init__(
        self,
        *,
        model: Any = None,
        triage: TriageFn | None = None,
    ) -> None:
        self._model = model
        self._triage_fn = triage

    # --- clarify gate + triage (runs before the model, can pause via interrupt) --------

    def before_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        # Sync runs are not used in production (SlotFlow streams via astream_events); the gate
        # only runs on the async path so a sync invocation degrades to no-op.
        return None

    @override
    async def abefore_model(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        try:
            return await self._gate(state, runtime)
        except GraphBubbleUp:
            # interrupt() signals a pause by raising a GraphBubbleUp; let it propagate so the
            # graph checkpoints and surfaces the clarification instead of being swallowed.
            raise
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
        if _already_clarified(messages):
            return None

        triage = await self._triage(messages)
        if triage is None:
            return None

        if not triage.get("actionable", True):
            return self._clarify_via_interrupt(triage, ctx)
        return None

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


    def _clarify_via_interrupt(
        self, triage: dict[str, Any], ctx: RunContext | None
    ) -> dict[str, Any]:
        """Pause the graph to ask the user, then inject their answer as a HumanMessage.

        ``interrupt()`` raises on first run (the graph checkpoints and surfaces the payload);
        on resume it returns the user's answer. We inject the answer AS the user's own message —
        verbatim, not wrapped in a meta-frame — because the model treats the latest HumanMessage
        as the user speaking, and a meta-framed wrapper ("the user's answer to Q is …") makes the
        model echo that wrapper at the start of its reply. The original request is still in
        history, so the bare answer is enough context. NOTE: on resume the whole hook replays, so
        the triage above ran a second time; that is benign (see module docstring).
        """

        question = _clean(triage.get("question")) or "请补充需要确认的信息,我才能继续。"
        clarification_type = _clean(triage.get("clarification_type")) or "missing_info"
        options = [opt for opt in (_clean(item) for item in _as_list(triage.get("options"))) if opt][:4]

        seed = f"{getattr(ctx, 'run_id', '')}:{question}"
        call_id = f"clarifygate-{sha256(seed.encode('utf-8')).hexdigest()[:12]}"
        tool_call = {
            "name": "ask_clarification",
            "args": {"question": question, "clarification_type": clarification_type, "options": options},
            "id": call_id,
            "type": "tool_call",
        }
        payload = build_clarification_payload(tool_call, run_context=ctx)

        answer = interrupt(payload)
        return {"messages": [HumanMessage(content=clarification_answer_text(answer))]}


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
