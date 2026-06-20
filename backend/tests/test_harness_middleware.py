"""Module 14 tests: SlotFlow harness middleware registry."""

from __future__ import annotations

import base64
import json

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.runtime import Runtime

from app.chat.models import ChatStreamRequest, UploadedFileContext
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.middleware import (
    SlotFlowArtifactDiscoveryMiddleware,
    SlotFlowDanglingToolCallMiddleware,
    SlotFlowMiddlewareConfig,
    SlotFlowRuntimeSummaryMiddleware,
    SlotFlowSkillsPreflightMiddleware,
    SlotFlowSummarizationMiddleware,
    SlotFlowTodoMiddleware,
    SlotFlowToolSafetyMiddleware,
    SlotFlowUploadsMiddleware,
    build_harness_middleware,
)
from app.harness.clarification import build_clarification_payload
from app.harness.middleware.dangling_tool_call_middleware import (
    repair_dangling_tool_calls,
)
from app.harness.sandbox import SlotFlowSandboxConfig


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """测试用 fake model：允许 LangChain agent 绑定工具。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


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
                    "model_name": "deepseek-v4-pro",
                "mode": "ultra",
                "agent_name": "default",
                "thinking_enabled": True,
                "plan_enabled": True,
                "subagent_enabled": True,
                "files_count": 1,
                "uploaded_files": [],
            },
        }
    }


def test_build_harness_middleware_adds_runtime_summary_by_default() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        model=FakeListChatModel(responses=["ok"]),
    )

    assert [item.name for item in middleware] == [
        "SlotFlowDanglingToolCallMiddleware",
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSummarizationMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowSubagentLimitMiddleware",
        "SlotFlowArtifactDiscoveryMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]
    assert isinstance(middleware[0], SlotFlowDanglingToolCallMiddleware)


def test_build_harness_middleware_can_disable_runtime_summary() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        model=FakeListChatModel(responses=["ok"]),
        config=SlotFlowMiddlewareConfig(runtime_summary_enabled=False),
    )

    assert [item.name for item in middleware] == [
        "SlotFlowDanglingToolCallMiddleware",
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSummarizationMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowSubagentLimitMiddleware",
        "SlotFlowArtifactDiscoveryMiddleware",
    ]


def test_build_harness_middleware_can_disable_tool_safety() -> None:
    middleware = build_harness_middleware(
        features=features_from_run_context(_bundle().context),
        model=FakeListChatModel(responses=["ok"]),
        config=SlotFlowMiddlewareConfig(tool_safety_enabled=False),
    )

    assert [item.name for item in middleware] == [
        "SlotFlowDanglingToolCallMiddleware",
        "SlotFlowSummarizationMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowSubagentLimitMiddleware",
        "SlotFlowArtifactDiscoveryMiddleware",
        "SlotFlowRuntimeSummaryMiddleware",
    ]


def test_build_harness_middleware_dedupes_by_name() -> None:
    features = features_from_run_context(_bundle().context)
    replacement = SlotFlowRuntimeSummaryMiddleware(features=features)

    middleware = build_harness_middleware(
        features=features,
        model=FakeListChatModel(responses=["ok"]),
        extra_middleware=[replacement],
    )

    assert [item.name for item in middleware] == [
        "SlotFlowRuntimeSummaryMiddleware",
        "SlotFlowDanglingToolCallMiddleware",
        "SlotFlowToolSafetyMiddleware",
        "SlotFlowSummarizationMiddleware",
        "SlotFlowSkillsPreflightMiddleware",
        "SlotFlowUploadsMiddleware",
        "SlotFlowTodoMiddleware",
        "SlotFlowSubagentLimitMiddleware",
        "SlotFlowArtifactDiscoveryMiddleware",
    ]
    assert middleware[0] is replacement


def test_skills_preflight_middleware_injects_find_skills_result() -> None:
    calls: list[tuple[str, int]] = []

    def fake_finder(query, max_results, config):
        _ = config
        calls.append((query, max_results))
        return {
            "query": query,
            "results": [{"title": "chart skill"}],
            "tool": "find-skills",
        }

    middleware = SlotFlowSkillsPreflightMiddleware(finder=fake_finder)

    update = middleware.before_agent(
        {"messages": [HumanMessage(content="请分析股票数据并生成图表报告")]},
        Runtime(context=_bundle().context),
    )

    assert calls == [("请分析股票数据并生成图表报告", 5)]
    assert update is not None
    assert update["slotflow"]["skills_preflight"]["tool"] == "find-skills"
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert "<slotflow-skills-preflight>" in str(message.content)
    assert str(message.content).endswith("请分析股票数据并生成图表报告")


def test_skills_preflight_middleware_prefers_installed_skill_matches(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "macro-research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: macro-research
description: Research and report on China macroeconomic data, GDP, PMI, CPI and finance.
---

# Macro Research
""",
        encoding="utf-8",
    )
    middleware = SlotFlowSkillsPreflightMiddleware(skills_root=tmp_path / "skills")

    update = middleware.before_agent(
        {"messages": [HumanMessage(content="使用专业的skills分析中国近期经济数据并生成报告")]},
        Runtime(context=_bundle().context),
    )

    assert update is not None
    preflight = update["slotflow"]["skills_preflight"]
    assert preflight["tool"] == "skill_match"
    assert preflight["next_action"] == "use_installed_skills"
    assert preflight["installed_matches"][0]["name"] == "macro-research"
    message = update["messages"][0]
    assert "installed_matches" in str(message.content)


def test_skills_preflight_middleware_skips_simple_chat() -> None:
    calls: list[str] = []
    middleware = SlotFlowSkillsPreflightMiddleware(
        finder=lambda query, max_results, config: calls.append(query) or {}
    )

    update = middleware.before_agent(
        {"messages": [HumanMessage(content="你好")]},
        Runtime(context=_bundle().context),
    )

    assert update is None
    assert calls == []


def test_clarification_payload_normalizes_dict_options() -> None:
    payload = build_clarification_payload(
        {
            "name": "ask_clarification",
            "args": {
                "question": "选一个方向",
                "clarification_type": "approach_choice",
                "options": [{"label": "先做后端"}, {"text": "先做前端"}],
            },
        }
    )

    assert payload["id"].startswith("clarification:")
    assert payload["options"] == [
        {"id": "A", "label": "先做后端"},
        {"id": "B", "label": "先做前端"},
        {"id": "C", "label": "其他（自己输入）"},  # free-text escape always appended last
    ]


def test_clarification_payload_does_not_duplicate_existing_freeform_option() -> None:
    payload = build_clarification_payload(
        {
            "name": "ask_clarification",
            "args": {"question": "选一个", "options": ["方案甲", "其他（请说明）"]},
        }
    )
    labels = [opt["label"] for opt in payload["options"]]
    assert labels == ["方案甲", "其他（请说明）"]  # model already provided a freeform option


def test_todo_middleware_exposes_write_todos_tool_and_prompt() -> None:
    middleware = SlotFlowTodoMiddleware()

    assert [tool.name for tool in middleware.tools] == ["write_todos"]
    assert "SlotFlow todo list" in middleware.system_prompt
    assert "Update the list immediately" in middleware.system_prompt


def test_todo_middleware_reminds_model_when_todos_leave_context() -> None:
    middleware = SlotFlowTodoMiddleware()

    update = middleware.before_model(
        {
            "messages": [HumanMessage(content="继续执行")],
            "todos": [
                {"content": "读取文件", "status": "completed"},
                {"content": "生成报告", "status": "in_progress"},
            ],
        },
        Runtime(context=_bundle().context),
    )

    assert update is not None
    reminder = update["messages"][0]
    assert isinstance(reminder, HumanMessage)
    assert reminder.name == "slotflow_todo_reminder"
    assert "[in_progress] 生成报告" in str(reminder.content)


def test_uploads_middleware_injects_uploaded_files_into_latest_user_message() -> None:
    context = _bundle().context.model_copy(
        update={
            "uploaded_files": [
                UploadedFileContext(
                    id="file_123",
                    filename="report.md",
                    original_filename="用户报告.md",
                    content_type="text/markdown",
                    size_bytes=128,
                    workspace_path="uploads/run_middleware/report.md",
                )
            ]
        }
    )
    middleware = SlotFlowUploadsMiddleware()

    update = middleware.before_agent(
        {
            "messages": [HumanMessage(content="请分析这个文件")],
            "slotflow": {"existing": "kept"},
        },
        Runtime(context=context),
    )

    assert update is not None
    assert update["slotflow"]["existing"] == "kept"
    assert update["slotflow"]["uploads"]["count"] == 1
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert "<slotflow-uploaded-files>" in str(message.content)
    assert "path=uploads/run_middleware/report.md" in str(message.content)
    assert str(message.content).endswith("请分析这个文件")


def test_uploads_middleware_injects_image_content_blocks(tmp_path) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nimage"
    root = tmp_path / "workspace"
    image_path = root / "uploads" / "run_middleware" / "photo.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(image_bytes)
    context = _bundle().context.model_copy(
        update={
            "uploaded_files": [
                UploadedFileContext(
                    id="file_image",
                    filename="photo.png",
                    original_filename="截图.png",
                    content_type="image/png",
                    size_bytes=len(image_bytes),
                    workspace_path="uploads/run_middleware/photo.png",
                )
            ]
        }
    )
    middleware = SlotFlowUploadsMiddleware(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=root)
    )

    update = middleware.before_agent(
        {"messages": [HumanMessage(content="这张图是什么？")]},
        Runtime(context=context),
    )

    assert update is not None
    content = update["messages"][0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "path=uploads/run_middleware/photo.png" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image_bytes


def test_artifact_discovery_middleware_records_new_artifacts(tmp_path) -> None:
    root = tmp_path / "workspace"
    artifacts_root = root / "artifacts"
    artifacts_root.mkdir(parents=True)
    (artifacts_root / "old.md").write_text("old", encoding="utf-8")

    middleware = SlotFlowArtifactDiscoveryMiddleware(
        sandbox_config=SlotFlowSandboxConfig(workspace_root=root)
    )
    runtime = Runtime(context=_bundle().context)
    before = middleware.before_agent({"messages": [], "slotflow": {}}, runtime)
    assert before is None

    (artifacts_root / "report.md").write_text("new", encoding="utf-8")

    after = middleware.after_agent(
        {"messages": [], "slotflow": {"existing": "kept"}},
        runtime,
    )

    artifacts = after["slotflow"]["artifacts"]
    assert after["slotflow"]["existing"] == "kept"
    assert "artifact_discovery" not in after["slotflow"]
    assert artifacts["source"] == "slotflow_artifact_discovery"
    assert [entry["path"] for entry in artifacts["new_entries"]] == [
        "artifacts/report.md"
    ]
    assert {entry["path"] for entry in artifacts["entries"]} == {
        "artifacts/old.md",
        "artifacts/report.md",
    }


def test_summarization_middleware_compresses_old_messages() -> None:
    middleware = SlotFlowSummarizationMiddleware(
        model=FakeListChatModel(responses=["compressed context"]),
        trigger_tokens=1,
        keep_messages=1,
        trim_tokens_to_summarize=100,
    )

    update = middleware.before_model(
        {
            "messages": [
                HumanMessage(content="old context"),
                HumanMessage(content="latest request"),
            ]
        },
        Runtime(context=_bundle().context),
    )

    assert update is not None
    assert "compressed context" in str(update["messages"][1].content)
    assert update["messages"][-1].content == "latest request"


def test_tool_safety_middleware_converts_tool_exception_to_error_message() -> None:
    @tool("boom")
    def boom_tool() -> str:
        """Tool placeholder used to test middleware exception handling."""

        return "unused"

    middleware = SlotFlowToolSafetyMiddleware()
    request = ToolCallRequest(
        tool_call={"name": "boom", "args": {}, "id": "call_boom"},
        tool=boom_tool,
        state={},
        runtime=None,
    )

    def handler(_: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("boom failed")

    message = middleware.wrap_tool_call(request, handler)
    payload = json.loads(str(message.content))

    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.name == "boom"
    assert message.tool_call_id == "call_boom"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["exception_type"] == "RuntimeError"
    assert payload["error"]["source"] == "slotflow_tool_safety"


def test_tool_safety_middleware_handles_unknown_tool_without_calling_handler() -> None:
    middleware = SlotFlowToolSafetyMiddleware()
    calls: list[str] = []
    request = ToolCallRequest(
        tool_call={"name": "missing_tool", "args": {}, "id": "call_missing"},
        tool=None,
        state={},
        runtime=None,
    )

    def handler(_: ToolCallRequest) -> ToolMessage:
        calls.append("called")
        return ToolMessage(content="unused", tool_call_id="call_missing")

    message = middleware.wrap_tool_call(request, handler)
    payload = json.loads(str(message.content))

    assert calls == []
    assert message.status == "error"
    assert message.name == "missing_tool"
    assert payload["error"]["type"] == "unknown_tool"


def test_repair_dangling_tool_calls_inserts_error_before_next_model_message() -> None:
    repaired = repair_dangling_tool_calls(
        [
            HumanMessage(content="first"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {"path": "missing.txt"},
                        "id": "call_read",
                    }
                ],
            ),
            HumanMessage(content="next"),
        ]
    )

    assert len(repaired) == 4
    assert isinstance(repaired[2], ToolMessage)
    assert repaired[2].status == "error"
    assert repaired[2].tool_call_id == "call_read"
    payload = json.loads(str(repaired[2].content))
    assert payload["error"]["type"] == "dangling_tool_call"
    assert payload["error"]["source"] == "slotflow_dangling_tool_call"


def test_repair_dangling_tool_calls_reads_raw_openai_tool_call_shape() -> None:
    repaired = repair_dangling_tool_calls(
        [
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "call_raw",
                            "type": "function",
                            "function": {"name": "workspace_tree", "arguments": "{}"},
                        }
                    ]
                },
            )
        ]
    )

    assert len(repaired) == 2
    assert isinstance(repaired[1], ToolMessage)
    assert repaired[1].name == "workspace_tree"
    assert repaired[1].tool_call_id == "call_raw"


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


@pytest.mark.asyncio
async def test_harness_graph_turns_tool_exception_into_error_tool_message(tmp_path) -> None:
    bundle = _bundle()
    graph = build_slotflow_harness_graph(
        model=ToolAwareFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "workspace_read",
                            "args": {"path": "../outside.txt"},
                            "id": "call_bad_path",
                        }
                    ],
                ),
                AIMessage(content="工具错误已收到。"),
            ]
        ),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试 tool safety 的助手。",
            sandbox_config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace"),
            middleware_config=SlotFlowMiddlewareConfig(clarify_gate_enabled=False),
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取非法路径"}]},
        config=bundle.config,
        context=bundle.context,
    )
    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]
    payload = json.loads(str(tool_messages[0].content))

    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert tool_messages[0].name == "workspace_read"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["source"] == "slotflow_tool_safety"
    assert result["messages"][-1].content == "工具错误已收到。"
