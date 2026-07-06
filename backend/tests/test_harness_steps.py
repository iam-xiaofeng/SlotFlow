"""Tests for SlotFlow harness steps (node-reusable pure functions).

重构后中间件逻辑已抽成 app/harness/steps/* 无状态纯函数。这些测试直接覆盖 steps，
对应原 middleware 单测的行为契约（runtime summary / uploads / skills preflight /
todo / subagent limit / tool safety / dangling / artifact discovery / clarify gate）。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.chat.models import ChatStreamRequest, UploadedFileContext
from app.chat.run_config import build_run_config
from app.harness.features import features_from_run_context
from app.harness.graph import route_after_model
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.steps.artifact_discovery import (
    artifact_baseline,
    artifact_finalize_update,
    list_artifact_entries,
)
from app.harness.steps.clarify_gate import (
    already_clarified,
    clarify_mode_enabled,
    is_fresh_user_turn,
    parse_triage,
)
from app.harness.steps.dangling_tool_call import repair_dangling_tool_calls
from app.harness.steps.runtime_summary import runtime_summary_update
from app.harness.steps.skills_preflight import (
    format_preflight,
    should_run_preflight,
    skills_preflight_update,
)
from app.harness.steps.subagent_limit import cap_subagent_calls
from app.harness.steps.todo import (
    latest_message_is_todo_enforcer,
    todo_enforcement_update,
    todo_parallel_call_guard,
    todo_reminder_update,
    write_todos_tool,
)
from app.harness.steps.tool_safety import (
    build_error_tool_message,
    build_unknown_tool_error_message,
    tool_call_name,
)
from app.harness.steps.uploads import uploads_update


def _bundle():
    request = ChatStreamRequest(message="解释 steps", mode="ultra", files=["upload_a"])
    return build_run_config(thread_id="thread_steps", run_id="run_steps", request=request)


# --- runtime summary --------------------------------------------------------


def test_runtime_summary_writes_compact_context_snapshot() -> None:
    bundle = _bundle()
    features = features_from_run_context(bundle.context)
    update = runtime_summary_update(
        state={"slotflow": {"existing": "kept"}},
        context=bundle.context,
        features=features,
    )
    assert update == {
        "slotflow": {
            "existing": "kept",
            "runtime": {
                "thread_id": "thread_steps",
                "run_id": "run_steps",
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


# --- uploads ---------------------------------------------------------------


def test_uploads_injects_uploaded_files_into_latest_user_message() -> None:
    context = _bundle().context.model_copy(
        update={
            "uploaded_files": [
                UploadedFileContext(
                    id="file_123",
                    filename="report.md",
                    original_filename="用户报告.md",
                    content_type="text/markdown",
                    size_bytes=128,
                    workspace_path="uploads/run_steps/report.md",
                )
            ]
        }
    )
    update = uploads_update(
        state={"messages": [HumanMessage(content="请分析这个文件")], "slotflow": {"existing": "kept"}},
        context=context,
    )
    assert update is not None
    assert update["slotflow"]["existing"] == "kept"
    assert update["slotflow"]["uploads"]["count"] == 1
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert "<slotflow-uploaded-files>" in str(message.content)
    assert "path=uploads/run_steps/report.md" in str(message.content)
    assert str(message.content).endswith("请分析这个文件")


def test_uploads_injects_image_content_blocks(tmp_path: Path) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nimage"
    root = tmp_path / "workspace"
    image_path = root / "uploads" / "run_steps" / "photo.png"
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
                    workspace_path="uploads/run_steps/photo.png",
                )
            ]
        }
    )
    update = uploads_update(
        state={"messages": [HumanMessage(content="这张图是什么？")]},
        context=context,
        sandbox_config=SlotFlowSandboxConfig(workspace_root=root),
    )
    assert update is not None
    content = update["messages"][0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "path=uploads/run_steps/photo.png" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image_bytes


# --- skills preflight ------------------------------------------------------


def test_skills_preflight_stores_result_without_mutating_user_message() -> None:
    calls: list[tuple[str, int]] = []
    message = HumanMessage(content="请分析股票数据并生成图表报告")

    def fake_finder(query, max_results, config, skills_root=None, skills_config_store=None):
        calls.append((query, max_results))
        return {
            "query": query,
            "installed_matches": [{"name": "chart skill"}],
            "tool": "find-skills",
        }

    update = skills_preflight_update(
        state={"messages": [message]},
        finder=fake_finder,
    )
    assert calls == [("请分析股票数据并生成图表报告", 5)]
    assert update is not None
    assert update["slotflow"]["skills_preflight"]["tool"] == "find-skills"
    assert "messages" not in update
    assert message.content == "请分析股票数据并生成图表报告"


def test_skills_preflight_format_is_internal_system_context() -> None:
    preflight = format_preflight(
        {
            "installed_matches": [{"name": "academic-plotting"}],
            "tool": "find-skills",
        }
    )

    assert preflight.startswith("<slotflow-skills-preflight>")
    assert "SlotFlow internal context" in preflight
    assert "academic-plotting" in preflight


def test_should_run_preflight_skips_simple_chat() -> None:
    assert should_run_preflight("你好") is False
    assert should_run_preflight("请分析股票数据并生成图表报告") is True


# --- todo reminder ---------------------------------------------------------


def test_todo_reminder_reminds_when_todos_leave_context() -> None:
    update = todo_reminder_update(
        state={
            "messages": [HumanMessage(content="继续执行")],
            "todos": [
                {"content": "读取文件", "status": "completed"},
                {"content": "生成报告", "status": "in_progress"},
            ],
        }
    )
    assert update is not None
    reminder = update["messages"][0]
    assert isinstance(reminder, HumanMessage)
    assert reminder.name == "slotflow_todo_reminder"
    assert "[in_progress] 生成报告" in str(reminder.content)


def test_write_todos_tool_is_registered_with_command_return() -> None:
    assert write_todos_tool.name == "write_todos"
    assert write_todos_tool.args["todos"]["items"]["$ref"] == "#/$defs/Todo"
    schema = write_todos_tool.args_schema.model_json_schema()
    assert "content" in schema["$defs"]["Todo"]["properties"]
    assert "text" not in schema["$defs"]["Todo"]["properties"]
    # The official tool returns a Command updating todos + a ToolMessage. InjectedToolCallId
    # requires a full model ToolCall shape (as ToolNode provides in the graph).
    tool_call = {
        "name": "write_todos",
        "args": {"todos": [{"text": "step", "status": "in_progress"}]},
        "id": "c1",
        "type": "tool_call",
    }
    cmd = write_todos_tool.invoke(tool_call)
    assert cmd.update["todos"] == [{"content": "step", "status": "in_progress"}]
    assert cmd.update["messages"][0].tool_call_id == "c1"


def test_todo_parallel_call_guard_rejects_multiple_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "write_todos", "args": {"todos": []}, "id": "a", "type": "tool_call"},
            {"name": "write_todos", "args": {"todos": []}, "id": "b", "type": "tool_call"},
        ],
    )
    update = todo_parallel_call_guard(state={"messages": [HumanMessage("go"), msg]})
    assert update is not None
    assert len(update["messages"]) == 2
    assert all(m.status == "error" for m in update["messages"])


def test_todo_parallel_call_guard_allows_single_call() -> None:
    msg = AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {"todos": []}, "id": "a", "type": "tool_call"}])
    assert todo_parallel_call_guard(state={"messages": [HumanMessage("go"), msg]}) is None


def test_todo_enforcement_requests_initial_list_for_planned_work() -> None:
    update = todo_enforcement_update(
        state={"messages": [HumanMessage("修复 todo 链路并补测试"), AIMessage(content="我来处理。")]},
        plan_enabled=True,
    )

    assert update is not None
    enforcer = update["messages"][0]
    assert isinstance(enforcer, HumanMessage)
    assert enforcer.name == "slotflow_todo_enforcer"
    assert "Call `write_todos` now" in str(enforcer.content)
    assert latest_message_is_todo_enforcer({"messages": [enforcer]})
    assert route_after_model({"messages": [HumanMessage("修复 todo 链路"), AIMessage(content="我来处理。"), enforcer]}) == "pre_model"


def test_todo_enforcement_skips_simple_unplanned_answer() -> None:
    update = todo_enforcement_update(
        state={"messages": [HumanMessage("你好"), AIMessage(content="你好。")]},
        plan_enabled=True,
    )

    assert update is None


def test_todo_enforcement_ignores_slotflow_injected_context_for_complexity() -> None:
    injected = (
        "<slotflow-skills-preflight>\n"
        "This injected block is intentionally long and mentions analyze, research, report, "
        "implement, test, verify, and other workflow words that must not make a simple "
        "user request look todo-worthy.\n"
        "</slotflow-skills-preflight>\n\n"
        "读取当前 SlotFlow run context"
    )
    update = todo_enforcement_update(
        state={"messages": [HumanMessage(injected), AIMessage(content="工具结果已经收到。")]},
        plan_enabled=True,
    )

    assert update is None


def test_todo_enforcement_honors_explicit_todo_request_without_plan_mode() -> None:
    update = todo_enforcement_update(
        state={"messages": [HumanMessage("测试 todo 功能"), AIMessage(content="我会展示。")]},
        plan_enabled=False,
    )

    assert update is not None
    assert update["messages"][0].name == "slotflow_todo_enforcer"


def test_todo_enforcement_requests_status_update_for_incomplete_todos() -> None:
    update = todo_enforcement_update(
        state={
            "messages": [HumanMessage("继续"), AIMessage(content="已经完成。")],
            "todos": [{"content": "修复后端", "status": "in_progress"}],
        },
        plan_enabled=True,
    )

    assert update is not None
    assert "The active todo list is not complete" in str(update["messages"][0].content)


def test_todo_enforcement_does_not_loop_after_existing_enforcer() -> None:
    first = HumanMessage("修复 todo 链路")
    enforcer = HumanMessage(name="slotflow_todo_enforcer", content="call write_todos")
    update = todo_enforcement_update(
        state={"messages": [first, AIMessage(content="我来处理。"), enforcer, AIMessage(content="继续解释。")]},
        plan_enabled=True,
    )

    assert update is None


# --- subagent limit --------------------------------------------------------


def _task_call(i: int) -> dict:
    return {"name": "task_tool", "args": {"agent_name": "researcher", "task": f"t{i}"}, "id": f"task{i}", "type": "tool_call"}


def test_cap_subagent_calls_does_not_touch_when_within_limit() -> None:
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2)])
    result = cap_subagent_calls(state={"messages": [HumanMessage("go"), msg]}, max_concurrent=3)
    assert result is None


def test_cap_subagent_calls_truncates_excess_task_calls() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[_task_call(1), _task_call(2), _task_call(3), _task_call(4)],
        additional_kwargs={
            "reasoning_content": "thinking",
            "tool_calls": [{"id": f"task{i}", "function": {"name": "task_tool"}} for i in (1, 2, 3, 4)],
        },
    )
    result = cap_subagent_calls(state={"messages": [HumanMessage("go"), msg]}, max_concurrent=2)
    trimmed = result["messages"][0]
    assert [tc["id"] for tc in trimmed.tool_calls] == ["task1", "task2"]
    assert [tc["id"] for tc in trimmed.additional_kwargs["tool_calls"]] == ["task1", "task2"]
    assert trimmed.additional_kwargs["reasoning_content"] == "thinking"


def test_cap_subagent_calls_keeps_non_task_tool_calls() -> None:
    other = {"name": "web_search", "args": {"query": "x"}, "id": "w1", "type": "tool_call"}
    msg = AIMessage(content="", tool_calls=[_task_call(1), _task_call(2), other])
    result = cap_subagent_calls(state={"messages": [HumanMessage("go"), msg]}, max_concurrent=1)
    ids = [tc["id"] for tc in result["messages"][0].tool_calls]
    assert ids == ["task1", "w1"]


def test_cap_subagent_calls_ignores_non_ai_or_toolless_messages() -> None:
    assert cap_subagent_calls(state={"messages": [HumanMessage("hi")]}, max_concurrent=1) is None
    assert cap_subagent_calls(state={"messages": [AIMessage(content="done")]}, max_concurrent=1) is None


# --- tool safety -----------------------------------------------------------


def test_tool_safety_builds_error_tool_message_for_exception() -> None:
    @tool("boom")
    def boom_tool() -> str:
        """placeholder"""
        return "unused"

    request_tool_call = {"name": "boom", "args": {}, "id": "call_boom"}
    message = build_error_tool_message(
        request_tool_call,
        error_type="tool_execution_error",
        message="boom failed",
        exception_type="RuntimeError",
    )
    payload = json.loads(str(message.content))
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.name == "boom"
    assert message.tool_call_id == "call_boom"
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["exception_type"] == "RuntimeError"
    assert payload["error"]["source"] == "slotflow_tool_safety"


def test_tool_safety_builds_unknown_tool_error() -> None:
    message = build_error_tool_message(
        {"name": "missing_tool", "args": {}, "id": "call_missing"},
        error_type="unknown_tool",
        message="tool is not registered: 'missing_tool'",
    )
    payload = json.loads(str(message.content))
    assert message.status == "error"
    assert message.name == "missing_tool"
    assert payload["error"]["type"] == "unknown_tool"
    assert tool_call_name({"name": "missing_tool"}) == "missing_tool"


def test_tool_safety_redirects_unsafe_unknown_host_tool_to_sandbox() -> None:
    message = build_unknown_tool_error_message(
        {"name": "bash", "args": {"command": "python -V"}, "id": "call_bash"}
    )
    payload = json.loads(str(message.content))

    assert message.status == "error"
    assert message.name == "bash"
    assert payload["error"]["type"] == "unsafe_host_execution_tool"
    assert "sandbox_exec" in payload["error"]["message"]
    assert "docker_engine_setup" in payload["error"]["message"]


# --- dangling tool call repair --------------------------------------------


def test_repair_dangling_tool_calls_inserts_error_before_next_model_message() -> None:
    repaired = repair_dangling_tool_calls(
        [
            HumanMessage(content="first"),
            AIMessage(
                content="",
                tool_calls=[{"name": "workspace_read", "args": {"path": "missing.txt"}, "id": "call_read"}],
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
                        {"id": "call_raw", "type": "function", "function": {"name": "workspace_tree", "arguments": "{}"}}
                    ]
                },
            )
        ]
    )
    assert len(repaired) == 2
    assert isinstance(repaired[1], ToolMessage)
    assert repaired[1].name == "workspace_tree"
    assert repaired[1].tool_call_id == "call_raw"


# --- artifact discovery ----------------------------------------------------


def test_artifact_finalize_records_new_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    artifacts_root = root / "artifacts"
    artifacts_root.mkdir(parents=True)
    (artifacts_root / "old.md").write_text("old", encoding="utf-8")
    sandbox = SlotFlowSandboxConfig(workspace_root=root)
    baseline = artifact_baseline(sandbox)
    assert baseline == {"artifacts/old.md"}

    (artifacts_root / "report.md").write_text("new", encoding="utf-8")
    update = artifact_finalize_update(
        state={"slotflow": {"existing": "kept"}},
        baseline_paths=baseline,
        sandbox_config=sandbox,
    )
    artifacts = update["slotflow"]["artifacts"]
    assert update["slotflow"]["existing"] == "kept"
    assert artifacts["source"] == "slotflow_artifact_discovery"
    assert [entry["path"] for entry in artifacts["new_entries"]] == ["artifacts/report.md"]
    assert {entry["path"] for entry in artifacts["entries"]} == {"artifacts/old.md", "artifacts/report.md"}


def test_list_artifact_entries_empty_when_no_artifacts(tmp_path: Path) -> None:
    assert list_artifact_entries(SlotFlowSandboxConfig(workspace_root=tmp_path)) == []


# --- clarify gate ----------------------------------------------------------


def test_clarify_mode_enabled_only_for_pro_ultra() -> None:
    assert clarify_mode_enabled("pro") is True
    assert clarify_mode_enabled("ultra") is True
    assert clarify_mode_enabled("flash") is False


def test_is_fresh_user_turn_and_already_clarified() -> None:
    assert is_fresh_user_turn([HumanMessage("hi")]) is True
    assert is_fresh_user_turn([AIMessage(content="x")]) is False
    messages = [
        HumanMessage("做个表格"),
        AIMessage(content="", tool_calls=[{"name": "ask_clarification", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="{}", name="ask_clarification", tool_call_id="c1"),
        HumanMessage("CSV"),
    ]
    assert already_clarified(messages) is True
    assert already_clarified([HumanMessage("hi")]) is False


def test_parse_triage_reads_json_object() -> None:
    triage = parse_triage('noise {"actionable": false, "question": "q?"} more')
    assert triage == {"actionable": False, "question": "q?"}
    assert parse_triage("no json") is None
