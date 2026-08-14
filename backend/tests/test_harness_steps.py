"""Tests for SlotFlow harness steps (node-reusable pure functions).

重构后中间件逻辑已抽成 app/harness/steps/* 无状态纯函数。这些测试直接覆盖 steps，
对应原 middleware 单测的行为契约（runtime summary / uploads / todo / subagent limit /
tool safety / dangling / artifact discovery）。
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
from app.harness.steps.dangling_tool_call import repair_dangling_tool_calls
from app.harness.steps.runtime_summary import runtime_summary_update
from app.harness.steps.subagent_limit import cap_subagent_calls
from app.harness.steps.todo import (
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
                "model_name": "deepseek/deepseek-v4-pro",
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


# --- todo reminder ---------------------------------------------------------


def test_todo_reminder_reminds_when_todos_leave_context() -> None:
    reminder_text = todo_reminder_update(
        state={
            "messages": [HumanMessage(content="继续执行")],
            "todos": [
                {"content": "读取文件", "status": "completed"},
                {"content": "生成报告", "status": "in_progress"},
            ],
        }
    )
    # Control text only — it is folded into the step system prompt by pre_model and
    # must never become a message object (streamed by the messages projection).
    assert isinstance(reminder_text, str)
    assert reminder_text.startswith("<slotflow-todo-reminder>")
    assert "[in_progress] 生成报告" in reminder_text


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


def test_route_after_model_ends_the_turn_when_the_model_stops_calling_tools() -> None:
    """模型不再调工具 = 这一轮结束。图不再把已完成的回合拽回去补 todo。

    删掉的 `todo_enforcement_update` 触发条件正是「本次 AI 消息没有 tool_calls」，
    也就是只在模型已经写完最终答案时才可能触发；它唯一能做的就是把完成的回合重新拽开。
    真机上一句「这是什么」被拽了两次，同一个问题答了三遍。见 HARNESS_NOTES §63。
    """

    state = {
        "messages": [
            HumanMessage("修复 todo 链路并补测试"),
            AIMessage(content="我来处理。"),
        ]
    }

    assert route_after_model(state) == "finalize"


def test_route_after_model_still_goes_to_tools_when_the_model_asks_for_one() -> None:
    state = {
        "messages": [
            HumanMessage("go"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_todos", "args": {"todos": []}, "id": "a", "type": "tool_call"}
                ],
            ),
        ]
    }

    assert route_after_model(state) == "tools"


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
    artifacts_root = root / "thread_a" / "artifacts"
    artifacts_root.mkdir(parents=True)
    (artifacts_root / "old.md").write_text("old", encoding="utf-8")
    sandbox = SlotFlowSandboxConfig(workspace_root=root)
    baseline = artifact_baseline(sandbox, thread_id="thread_a")
    assert baseline == {"thread_a/artifacts/old.md"}

    (artifacts_root / "report.md").write_text("new", encoding="utf-8")
    update = artifact_finalize_update(
        state={"slotflow": {"existing": "kept"}},
        baseline_paths=baseline,
        sandbox_config=sandbox,
        thread_id="thread_a",
    )
    artifacts = update["slotflow"]["artifacts"]
    assert update["slotflow"]["existing"] == "kept"
    assert artifacts["source"] == "slotflow_artifact_discovery"
    assert [entry["path"] for entry in artifacts["new_entries"]] == [
        "thread_a/artifacts/report.md"
    ]
    assert {entry["path"] for entry in artifacts["entries"]} == {
        "thread_a/artifacts/old.md",
        "thread_a/artifacts/report.md",
    }


def test_artifact_discovery_ignores_other_conversations(tmp_path: Path) -> None:
    """并发跑两个对话时,B 新写的产物不能算进 A 的 new_entries。

    旧实现扫的是所有对话共用的 ``artifacts/``,A 的"本轮新增"里会混进 B 的文件,
    前端就会弹出一个跟当前提问无关的产物。
    """

    root = tmp_path / "workspace"
    (root / "thread_a" / "artifacts").mkdir(parents=True)
    (root / "thread_b" / "artifacts").mkdir(parents=True)
    sandbox = SlotFlowSandboxConfig(workspace_root=root)

    baseline = artifact_baseline(sandbox, thread_id="thread_a")

    # 对话 B 在 A 这一轮进行期间写了自己的产物
    (root / "thread_b" / "artifacts" / "b.md").write_text("b", encoding="utf-8")
    (root / "thread_a" / "artifacts" / "a.md").write_text("a", encoding="utf-8")

    update = artifact_finalize_update(
        state={"slotflow": {}},
        baseline_paths=baseline,
        sandbox_config=sandbox,
        thread_id="thread_a",
    )
    new_paths = [entry["path"] for entry in update["slotflow"]["artifacts"]["new_entries"]]
    assert new_paths == ["thread_a/artifacts/a.md"]


def test_artifact_discovery_still_sees_legacy_layout(tmp_path: Path) -> None:
    """迁移前写在 ``artifacts/<thread>/`` 的存量产物仍要报给同一个对话。"""

    root = tmp_path / "workspace"
    (root / "artifacts" / "thread_a").mkdir(parents=True)
    (root / "artifacts" / "thread_a" / "legacy.md").write_text("x", encoding="utf-8")
    sandbox = SlotFlowSandboxConfig(workspace_root=root)

    entries = list_artifact_entries(sandbox, thread_id="thread_a")

    assert [entry["path"] for entry in entries] == ["artifacts/thread_a/legacy.md"]


def test_list_artifact_entries_empty_when_no_artifacts(tmp_path: Path) -> None:
    assert list_artifact_entries(SlotFlowSandboxConfig(workspace_root=tmp_path)) == []


