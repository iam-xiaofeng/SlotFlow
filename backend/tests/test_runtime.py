"""模块七测试：SlotFlow 本地 runtime 装配层。

这一层不直接依赖 DeerFlow 包，而是把 SlotFlow 自己需要的最小运行时装配收拢出来：

- 选择当前 agent 模式（static / deepseek）
- 显式挂接 checkpointer
- 保持 AgentAdapter / AgentEvent 外部契约不变
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from app.chat.agent_adapter import collect_agent_events
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import (
    RuntimeBackedAgentAdapter,
    SlotFlowRuntimeConfig,
    create_checkpointer,
    load_runtime_config_from_env,
)
from app.harness.mcp import SlotFlowMcpConfig, SlotFlowMcpServerConfig
from app.harness.middleware import SlotFlowMiddlewareConfig


def _bundle(
    *,
    thread_id: str = "thread_runtime",
    run_id: str = "run_runtime",
    request: ChatStreamRequest | None = None,
):
    return build_run_config(
        thread_id=thread_id,
        run_id=run_id,
        request=request or ChatStreamRequest(message="解释 runtime"),
    )


def test_load_runtime_config_from_env_uses_small_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认仍然走 static，避免本地开发和测试强依赖 API key。"""

    monkeypatch.delenv("SLOTFLOW_AGENT_MODE", raising=False)
    monkeypatch.delenv("SLOTFLOW_CHECKPOINTER_BACKEND", raising=False)
    monkeypatch.delenv("SLOTFLOW_DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("SLOTFLOW_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("SLOTFLOW_SKILLS_ROOT", raising=False)
    monkeypatch.delenv("SLOTFLOW_ENABLED_SKILLS", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_MCP_SERVERS", raising=False)
    monkeypatch.delenv("SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_TOOL_SAFETY_MIDDLEWARE", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", raising=False)
    monkeypatch.delenv("SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES", raising=False)

    config = load_runtime_config_from_env()

    assert config == SlotFlowRuntimeConfig(
        adapter_mode="static",
        model_name="deepseek-v4-flash",
        checkpointer_backend="memory",
    )


def test_load_runtime_config_from_env_reads_mcp_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime only reads MCP config; actual tool loading remains in harness."""

    monkeypatch.setenv("SLOTFLOW_MCP_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_MCP_SERVERS", "filesystem, search")

    config = load_runtime_config_from_env()

    assert config.mcp_config == SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(name="filesystem"),
            SlotFlowMcpServerConfig(name="search"),
        ),
    )


def test_load_runtime_config_from_env_reads_middleware_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime reads middleware switches but does not instantiate middleware."""

    monkeypatch.setenv("SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE", "false")
    monkeypatch.setenv("SLOTFLOW_TOOL_SAFETY_MIDDLEWARE", "false")

    config = load_runtime_config_from_env()

    assert config.middleware_config == SlotFlowMiddlewareConfig(
        runtime_summary_enabled=False,
        tool_safety_enabled=False,
    )


def test_create_checkpointer_supports_none_and_memory() -> None:
    """第一版 runtime 只保留最小的 checkpointer 选择。"""

    assert create_checkpointer(
        SlotFlowRuntimeConfig(adapter_mode="static", checkpointer_backend="none")
    ) is None
    assert isinstance(
        create_checkpointer(
            SlotFlowRuntimeConfig(adapter_mode="deepseek", checkpointer_backend="memory")
        ),
        InMemorySaver,
    )


@pytest.mark.asyncio
async def test_runtime_backed_adapter_static_mode_keeps_agent_boundary() -> None:
    """static 模式只是本地 runtime 的一种装配结果，对外仍然流出 AgentEvent。"""

    adapter = RuntimeBackedAgentAdapter(
        SlotFlowRuntimeConfig(adapter_mode="static"),
    )
    request = ChatStreamRequest(message="解释 static runtime", files=["upload_1"])
    bundle = _bundle(request=request)

    events = await collect_agent_events(adapter.stream_events(request=request, bundle=bundle))

    assert events[0].event == "run.prepared"
    assert "message.delta" in [event.event for event in events]
    assert events[-2].event == "state.snapshot"
    assert events[-1].event == "run.finished"


@pytest.mark.asyncio
async def test_runtime_backed_adapter_deepseek_mode_uses_request_model_and_keeps_thread_state() -> None:
    """deepseek 模式下，每次 run 可动态选模型，并通过共享 checkpointer 保留多轮状态。"""

    calls: list[str] = []
    responses = iter(["first answer", "second answer"])

    def model_factory(model_name: str) -> FakeListChatModel:
        calls.append(model_name)
        return FakeListChatModel(responses=[next(responses)])

    adapter = RuntimeBackedAgentAdapter(
        SlotFlowRuntimeConfig(adapter_mode="deepseek", checkpointer_backend="memory"),
        model_factory=model_factory,
    )

    first_request = ChatStreamRequest(message="first question", model_name="fake-one")
    first_events = await collect_agent_events(
        adapter.stream_events(
            request=first_request,
            bundle=_bundle(
                thread_id="thread_same",
                run_id="run_one",
                request=first_request,
            ),
        )
    )
    first_snapshot = next(event.data for event in first_events if event.event == "state.snapshot")

    second_request = ChatStreamRequest(message="second question", model_name="fake-two")
    second_events = await collect_agent_events(
        adapter.stream_events(
            request=second_request,
            bundle=_bundle(
                thread_id="thread_same",
                run_id="run_two",
                request=second_request,
            ),
        )
    )
    second_snapshot = next(event.data for event in second_events if event.event == "state.snapshot")

    assert calls == ["fake-one", "fake-two"]
    assert first_snapshot["messages"][-1]["content"] == "first answer"
    assert [message["content"] for message in second_snapshot["messages"]] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ]
