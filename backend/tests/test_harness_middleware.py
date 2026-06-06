"""Module 14 tests: SlotFlow harness middleware registry."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.runtime import Runtime

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.middleware import (
    SlotFlowMiddlewareConfig,
    SlotFlowRuntimeSummaryMiddleware,
    build_harness_middleware,
)


def _bundle():
    request = ChatStreamRequest(
        message="解释 middleware",
        mode="ultra",
        files=["upload_a"],
    )
    return build_run_config(
        thread_id="thread_middleware",
        run_id="run_middleware",
        request=request,
    )


def test_runtime_summary_middleware_writes_compact_context_snapshot() -> None:
    bundle = _bundle()
    features = features_from_run_context(bundle.context)
    middleware = SlotFlowRuntimeSummaryMiddleware(features=features)

    update = middleware.before_agent(
        {"messages": [], "slotflow": {"existing": "kept"}},
        Runtime(context=bundle.context),
    )

    assert update == {
        "slotflow": {
            "existing": "kept",
            "runtime": {
                "thread_id": "thread_middleware",
                "run_id": "run_middleware",
                "model_name": "deepseek-v4-flash",
                "mode": "ultra",
                "agent_name": "default",
                "thinking_enabled": True,
                "plan_enabled": True,
                "subagent_enabled": True,
                "files_count": 1,
            },
        }
    }


def test_build_harness_middleware_adds_runtime_summary_by_default() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
    )

    assert [item.name for item in middleware] == ["SlotFlowRuntimeSummaryMiddleware"]


def test_build_harness_middleware_can_disable_runtime_summary() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        config=SlotFlowMiddlewareConfig(runtime_summary_enabled=False),
    )

    assert middleware == []


def test_build_harness_middleware_dedupes_by_name() -> None:
    features = features_from_run_context(_bundle().context)
    replacement = SlotFlowRuntimeSummaryMiddleware(features=features)

    middleware = build_harness_middleware(
        features=features,
        extra_middleware=[replacement],
    )

    assert middleware == [replacement]


@pytest.mark.asyncio
async def test_harness_graph_runs_runtime_summary_middleware() -> None:
    bundle = _bundle()
    graph = build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["middleware ok"]),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(system_prompt="你是测试 middleware 的助手。"),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取 runtime 摘要"}]},
        config=bundle.config,
        context=bundle.context,
    )

    assert result["slotflow"]["runtime"]["run_id"] == bundle.context.run_id
    assert result["slotflow"]["runtime"]["subagent_enabled"] is True
