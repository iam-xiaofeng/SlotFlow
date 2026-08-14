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
    """Collect bounded local metrics for every model call in one SlotFlow run.

    ⚠️ 一次 run 里的模型调用**不止主 agent 一个**:压缩节点会调一次模型做摘要,`task_tool`
    子代理在工具内部整跑一张子图(它复用父 run 的 callbacks,所以它的每次模型调用也落进这里)。
    这些调用的 prompt 大小和"当前会话上下文占用"没有关系——摘要看到的是被裁剪过的历史,子代理
    看到的是它自己的小上下文。所以 `context_tokens` 必须**只认主 agent 节点、且不在任何工具
    内部**的那次调用,否则一轮里只要末尾跑了子代理或触发了压缩,前端的"已用 token"就会瞬间跌到
    一个毫不相干的小数字。

    两个判据都来自 callback 本身,不需要额外埋点:
    - `metadata["langgraph_node"] == "agent"` 排除压缩节点(它叫 SlotFlowSummarizationMiddleware);
    - `on_tool_start/on_tool_end` 维护的嵌套深度排除子代理(子图的节点也叫 `agent`,单看节点名
      分不出来,但它必然是在某个工具调用**内部**开始的)。
    """

    MAIN_AGENT_NODE = "agent"

    def __init__(self, *, model_name: str, provider: str | None) -> None:
        self.model_name = model_name
        self.provider = provider
        self._lock = Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._calls: list[dict[str, Any]] = []
        self._tool_depth = 0

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> None:
        del serialized, input_str, run_id, kwargs
        with self._lock:
            self._tool_depth += 1

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        del output, run_id, kwargs
        with self._lock:
            self._tool_depth = max(0, self._tool_depth - 1)

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        del error, run_id, kwargs
        with self._lock:
            self._tool_depth = max(0, self._tool_depth - 1)

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        invocation = kwargs.get("invocation_params")
        invocation = invocation if isinstance(invocation, dict) else {}
        tools = invocation.get("tools")
        tools = tools if isinstance(tools, list) else []
        metadata = kwargs.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        node = metadata.get("langgraph_node")
        with self._lock:
            self._active[str(run_id)] = {
                "started": time.monotonic(),
                "first_token": None,
                "message_count": len(messages[0]) if messages and isinstance(messages[0], list) else 0,
                "tool_count": len(tools),
                "tool_schema_chars": len(str(tools)),
                "node": node if isinstance(node, str) else None,
                "nested_in_tool": self._tool_depth > 0,
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
            active = self._active.pop(
                run_id,
                {
                    "started": ended,
                    "first_token": None,
                    "message_count": 0,
                    "tool_count": 0,
                    "tool_schema_chars": 0,
                    "node": None,
                    "nested_in_tool": False,
                },
            )
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
                "node": active.get("node"),
                "nested_in_tool": bool(active.get("nested_in_tool")),
                "cache_status": "unknown" if cached is None else ("hit" if cached > 0 else "miss"),
                **normalized,
            }
            self._calls.append(call)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            calls = [dict(call) for call in self._calls]
        observable = [call for call in calls if call["cache_status"] != "unknown"]
        # 上下文占用 = **主 agent 最近一次成功调用**的 prompt tokens:那才是这一刻真正送进模型的
        # 完整上下文。不能用"最近一次成功调用",因为一轮里最后跑的很可能是压缩节点或 task_tool
        # 子代理(见类注释),它们的 prompt 与会话窗口占用无关,会让前端的进度条突然跌下去。
        context_tokens = _main_agent_context_tokens(calls)
        return {
            "llm_requests": len(calls),
            "llm_successes": sum(call["status"] == "success" for call in calls),
            "llm_failures": sum(call["status"] == "error" for call in calls),
            "input_tokens": sum(call["input_tokens"] or 0 for call in calls),
            "output_tokens": sum(call["output_tokens"] or 0 for call in calls),
            "total_tokens": sum(call["total_tokens"] or 0 for call in calls),
            "context_tokens": context_tokens,
            "cached_input_tokens": sum(call["cached_input_tokens"] or 0 for call in observable) if observable else None,
            "cache_creation_input_tokens": sum(call["cache_creation_input_tokens"] or 0 for call in calls if call["cache_creation_input_tokens"] is not None) or None,
            "cache_observable_requests": len(observable),
            "cache_hit_requests": sum(call["cache_status"] == "hit" for call in observable),
            "calls": calls,
        }


def _main_agent_context_tokens(calls: list[dict[str, Any]]) -> int | None:
    """取主 agent 最近一次成功调用的 prompt tokens 作为当前上下文占用。

    没有任何主 agent 调用时(非 graph 场景、纯单测桩)退回"最近一次成功调用",保持旧行为,
    而不是返回 None 让前端的仪表直接消失。
    """

    def _usable(call: dict[str, Any]) -> bool:
        return call["status"] == "success" and call["input_tokens"] is not None

    for call in reversed(calls):
        if (
            _usable(call)
            and call.get("node") == RunUsageCollector.MAIN_AGENT_NODE
            and not call.get("nested_in_tool")
        ):
            return call["input_tokens"]
    for call in reversed(calls):
        if _usable(call) and call.get("node") is None and not call.get("nested_in_tool"):
            return call["input_tokens"]
    return None
