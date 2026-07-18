"""Local, provider-neutral LLM usage and prompt-cache telemetry."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def normalize_usage(value: Any) -> dict[str, int | None]:
    """Normalize OpenAI/LiteLLM/Anthropic usage without inventing cache misses."""
    data = value if isinstance(value, dict) else {}
    input_details = data.get("input_token_details")
    prompt_details = data.get("prompt_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    input_tokens = _as_int(data.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _as_int(data.get("prompt_tokens"))
    output_tokens = _as_int(data.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _as_int(data.get("completion_tokens"))
    total_tokens = _as_int(data.get("total_tokens"))
    cached = _as_int(input_details.get("cache_read"))
    if cached is None:
        cached = _as_int(prompt_details.get("cached_tokens"))
    if cached is None:
        cached = _as_int(data.get("cache_read_input_tokens"))
    if cached is None:
        cached = _as_int(data.get("cached_tokens"))
    created = _as_int(input_details.get("cache_creation"))
    if created is None:
        created = _as_int(data.get("cache_creation_input_tokens"))
    reasoning = data.get("output_token_details")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    reasoning_tokens = _as_int(reasoning.get("reasoning"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached,
        "cache_creation_input_tokens": created,
        "reasoning_tokens": reasoning_tokens,
    }


def _message_usage(response: Any) -> dict[str, Any] | None:
    for generations in getattr(response, "generations", ()) or ():
        for generation in generations or ():
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if isinstance(usage, dict):
                return usage
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        token_usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(token_usage, dict):
            return token_usage
    return None


class RunUsageCollector(BaseCallbackHandler):
    """Collect bounded local metrics for every model call in one SlotFlow run."""

    def __init__(self, *, model_name: str, provider: str | None) -> None:
        self.model_name = model_name
        self.provider = provider
        self._lock = Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._calls: list[dict[str, Any]] = []

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        invocation = kwargs.get("invocation_params")
        invocation = invocation if isinstance(invocation, dict) else {}
        tools = invocation.get("tools")
        tools = tools if isinstance(tools, list) else []
        with self._lock:
            self._active[str(run_id)] = {
                "started": time.monotonic(),
                "first_token": None,
                "message_count": len(messages[0]) if messages and isinstance(messages[0], list) else 0,
                "tool_count": len(tools),
                "tool_schema_chars": len(str(tools)),
            }

    def on_llm_new_token(self, token: str, *, run_id, **kwargs) -> None:
        del token, kwargs
        with self._lock:
            active = self._active.get(str(run_id))
            if active is not None and active["first_token"] is None:
                active["first_token"] = time.monotonic()

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        del kwargs
        self._finish(str(run_id), status="success", usage=_message_usage(response))

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        del kwargs
        self._finish(str(run_id), status="error", usage=None, error_type=type(error).__name__)

    def _finish(self, run_id: str, *, status: str, usage: Any, error_type: str | None = None) -> None:
        ended = time.monotonic()
        with self._lock:
            active = self._active.pop(run_id, {"started": ended, "first_token": None, "message_count": 0, "tool_count": 0, "tool_schema_chars": 0})
            normalized = normalize_usage(usage)
            cached = normalized["cached_input_tokens"]
            call = {
                "call_id": run_id,
                "model": self.model_name,
                "provider": self.provider,
                "status": status,
                "error_type": error_type,
                "latency_ms": round((ended - active["started"]) * 1000),
                "ttft_ms": round((active["first_token"] - active["started"]) * 1000) if active["first_token"] is not None else None,
                "message_count": active["message_count"],
                "tool_count": active["tool_count"],
                "tool_schema_chars": active["tool_schema_chars"],
                "cache_status": "unknown" if cached is None else ("hit" if cached > 0 else "miss"),
                **normalized,
            }
            self._calls.append(call)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            calls = [dict(call) for call in self._calls]
        observable = [call for call in calls if call["cache_status"] != "unknown"]
        return {
            "llm_requests": len(calls),
            "llm_successes": sum(call["status"] == "success" for call in calls),
            "llm_failures": sum(call["status"] == "error" for call in calls),
            "input_tokens": sum(call["input_tokens"] or 0 for call in calls),
            "output_tokens": sum(call["output_tokens"] or 0 for call in calls),
            "total_tokens": sum(call["total_tokens"] or 0 for call in calls),
            "cached_input_tokens": sum(call["cached_input_tokens"] or 0 for call in observable) if observable else None,
            "cache_creation_input_tokens": sum(call["cache_creation_input_tokens"] or 0 for call in calls if call["cache_creation_input_tokens"] is not None) or None,
            "cache_observable_requests": len(observable),
            "cache_hit_requests": sum(call["cache_status"] == "hit" for call in observable),
            "calls": calls,
        }
