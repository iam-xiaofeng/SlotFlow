"""模块 10 测试：SlotFlow harness builder 骨架。"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import tool

import app.chat.runtime as runtime_module
import app.harness.builder as builder_module
from app.chat.models import ChatStreamRequest, UploadedFileContext
from app.chat.run_config import build_run_config
from app.chat.runtime import DEFAULT_DEEPSEEK_SYSTEM_PROMPT, SlotFlowRuntimeConfig
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.mcp import SlotFlowMcpConfig, SlotFlowMcpServerConfig
from app.harness.middleware import SlotFlowMiddlewareConfig


class ToolAwareFakeListChatModel(FakeListChatModel):
    """测试用 fake model：普通 fake 文本能力 + 支持 bind_tools 边界。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _run_context(mode: str = "ultra"):
    request = ChatStreamRequest(message="解释 harness", mode=mode)
    return build_run_config(
        thread_id="thread_harness",
        run_id="run_harness",
        request=request,
    ).context


def test_features_from_run_context_keeps_harness_input_narrow() -> None:
    """harness feature flags 来自 RunContext，但不是把整个 context 原样塞进 builder。"""

    features = features_from_run_context(_run_context(mode="ultra"))

    assert features == SlotFlowHarnessFeatures(
        thinking_enabled=True,
        plan_enabled=True,
        subagent_enabled=True,
    )


def test_harness_builder_passes_graph_boundary_arguments(monkeypatch) -> None:
    """builder 负责组装 graph 边界参数，chat/runtime 不再直接调用 create_agent。"""

    captured: dict[str, Any] = {}
    fake_graph = object()

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return fake_graph

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

    model = ToolAwareFakeListChatModel(responses=["ok"])
    checkpointer = object()
    graph = builder_module.build_slotflow_harness_graph(
        model=model,
        run_context=_run_context(mode="pro"),
        harness_config=SlotFlowHarnessConfig(system_prompt="base prompt"),
        checkpointer=checkpointer,
    )

    assert graph is fake_graph
    assert captured["model"] is model
    assert [tool.name for tool in captured["tools"]] == [
        "slotflow_context",
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
        "web_fetch",
        "web_search",
        "find-skills",
        "skill_list",
        "skill_install",
        "mcp_add_http",
    ]
    assert [item.name for item in captured["middleware"]] == [
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]
    assert captured["checkpointer"] is checkpointer
    assert "base prompt" in captured["system_prompt"]
    assert "thinking_enabled=True" in captured["system_prompt"]
    assert "plan_enabled=True" in captured["system_prompt"]
    assert "subagent_enabled=False" in captured["system_prompt"]
    assert "call find-skills before doing the work" in captured["system_prompt"]
    assert "Backend preflight" not in captured["system_prompt"]
    assert "User-visible generated files must be written with artifact_write" in captured[
        "system_prompt"
    ]
    assert "create an artifact by default" in captured["system_prompt"]


def test_harness_builder_skips_tools_for_models_without_bind_tools(monkeypatch) -> None:
    """普通 fake model 没有 tool binding 能力，builder 不应强行传工具导致运行失败。"""

    captured: dict[str, Any] = {}

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

    builder_module.build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["ok"]),
        run_context=_run_context(mode="pro"),
        harness_config=SlotFlowHarnessConfig(system_prompt="base prompt"),
    )

    assert captured["tools"] == []
    assert [item.name for item in captured["middleware"]] == [
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]


def test_harness_builder_routes_uploaded_files_through_uploads_middleware(monkeypatch) -> None:
    """Uploaded files are injected by middleware, not duplicated in system prompt."""

    captured: dict[str, Any] = {}

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

    request = ChatStreamRequest(message="分析文件", files=["file_abc123abc123"])
    run_context = build_run_config(
        thread_id="thread_harness",
        run_id="run_harness",
        request=request,
        uploaded_files=[
            UploadedFileContext(
                id="file_abc123abc123",
                filename="report.md",
                content_type="text/markdown",
                size_bytes=8,
                workspace_path="uploads/run_harness/report.md",
            )
        ],
    ).context

    builder_module.build_slotflow_harness_graph(
        model=ToolAwareFakeListChatModel(responses=["ok"]),
        run_context=run_context,
        harness_config=SlotFlowHarnessConfig(system_prompt="base prompt"),
    )

    assert [item.name for item in captured["middleware"]] == [
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]
    assert "<slotflow-uploaded-files>" not in captured["system_prompt"]
    assert "path=uploads/run_harness/report.md" not in captured["system_prompt"]
    assert "call workspace_read(path)" in captured["system_prompt"]


def test_harness_builder_passes_mcp_config_to_tool_registry(monkeypatch) -> None:
    """MCP tools stay inside the harness tools registry boundary."""

    @tool("mcp_fake")
    def mcp_fake_tool() -> str:
        """Fake MCP tool for graph-boundary tests."""

        return "ok"

    class FakeMcpToolProvider:
        def load_tools(self, config: SlotFlowMcpConfig):
            assert config.servers == (SlotFlowMcpServerConfig(name="filesystem"),)
            return [mcp_fake_tool]

    captured: dict[str, Any] = {}

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

    builder_module.build_slotflow_harness_graph(
        model=ToolAwareFakeListChatModel(responses=["ok"]),
        run_context=_run_context(mode="pro"),
        harness_config=SlotFlowHarnessConfig(
            system_prompt="base prompt",
            mcp_config=SlotFlowMcpConfig(
                enabled=True,
                servers=(SlotFlowMcpServerConfig(name="filesystem"),),
            ),
            mcp_tool_provider=FakeMcpToolProvider(),
        ),
    )

    assert [tool.name for tool in captured["tools"]] == [
        "slotflow_context",
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
        "web_fetch",
        "web_search",
        "find-skills",
        "skill_list",
        "skill_install",
        "mcp_add_http",
        "mcp_fake",
    ]


def test_harness_builder_can_disable_builtin_middleware(monkeypatch) -> None:
    """middleware registry controls built-ins instead of scattering conditions in builder."""

    captured: dict[str, Any] = {}

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

    builder_module.build_slotflow_harness_graph(
        model=ToolAwareFakeListChatModel(responses=["ok"]),
        run_context=_run_context(mode="pro"),
        harness_config=SlotFlowHarnessConfig(
            system_prompt="base prompt",
            middleware_config=SlotFlowMiddlewareConfig(
                    runtime_summary_enabled=False,
                    tool_safety_enabled=False,
                    skills_preflight_enabled=False,
                    uploads_enabled=False,
                    todo_enabled=False,
            ),
        ),
    )

    assert captured["middleware"] == []


def test_runtime_graph_factory_delegates_to_harness_builder(monkeypatch) -> None:
    """runtime 只选择运行策略，真实 graph 组装委托给 harness builder。"""

    captured: dict[str, Any] = {}
    fake_graph = object()

    def fake_build_slotflow_harness_graph(**kwargs):
        captured.update(kwargs)
        return fake_graph

    monkeypatch.setattr(
        runtime_module,
        "build_slotflow_harness_graph",
        fake_build_slotflow_harness_graph,
    )

    model = FakeListChatModel(responses=["ok"])
    run_context = _run_context(mode="flash")
    checkpointer = object()
    graph = runtime_module.create_langgraph_agent_graph(
        model=model,
        runtime_config=SlotFlowRuntimeConfig(system_prompt=DEFAULT_DEEPSEEK_SYSTEM_PROMPT),
        run_context=run_context,
        checkpointer=checkpointer,
    )

    assert graph is fake_graph
    assert captured["model"] is model
    assert captured["run_context"] is run_context
    assert captured["checkpointer"] is checkpointer
    assert captured["harness_config"] == SlotFlowHarnessConfig(
        system_prompt=DEFAULT_DEEPSEEK_SYSTEM_PROMPT,
    )
