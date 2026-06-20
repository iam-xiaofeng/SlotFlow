"""把前端请求整理成 agent 运行配置的测试。

这个测试文件只盯一个边界：字段到底应该放进 `config` 还是 `context`。

如果 `thread_id` 放错位置，多轮记忆可能会静默失效，所以这里把规则写成测试。
"""

from __future__ import annotations

import pytest

from app.chat.models import ChatStreamRequest, UploadedFileContext
from app.chat.run_config import build_run_config, mode_to_feature_flags


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "flash",
            {
                "thinking_enabled": False,
                "is_plan_mode": False,
                "subagent_enabled": False,
            },
        ),
        (
            "pro",
            {
                "thinking_enabled": True,
                "is_plan_mode": True,
                "subagent_enabled": False,
            },
        ),
        (
            "ultra",
            {
                "thinking_enabled": True,
                "is_plan_mode": True,
                "subagent_enabled": True,
            },
        ),
    ],
)
def test_mode_to_feature_flags(mode, expected) -> None:
    """三档模式要稳定翻译成后端功能开关。"""

    assert mode_to_feature_flags(mode) == expected


def test_build_run_config_puts_thread_id_in_configurable() -> None:
    """thread_id 必须在 configurable 里，checkpointer 会靠它找多轮状态。"""

    request = ChatStreamRequest(message="解释 thread_id")

    bundle = build_run_config(
        thread_id="thread_abc",
        run_id="run_abc",
        request=request,
    )

    assert bundle.config == {
        "configurable": {
            "thread_id": "thread_abc",
        },
    }


def test_build_run_config_puts_business_fields_in_context() -> None:
    """业务字段进入 context，而不是混进 config 里。"""

    request = ChatStreamRequest(
        message="开始分析",
        model_name="gpt-analysis",
        mode="ultra",
        agent_name="researcher",
        files=["upload_1", "upload_2"],
    )

    bundle = build_run_config(
        thread_id="thread_123",
        run_id="run_456",
        request=request,
    )

    assert bundle.context.thread_id == "thread_123"
    assert bundle.context.run_id == "run_456"
    assert bundle.context.model_name == "gpt-analysis"
    assert bundle.context.mode == "ultra"
    assert bundle.context.agent_name == "researcher"
    assert bundle.context.files == ["upload_1", "upload_2"]
    assert bundle.context.uploaded_files == []
    assert bundle.context.thinking_enabled is True
    assert bundle.context.is_plan_mode is True
    assert bundle.context.subagent_enabled is True
    assert "run_id" not in bundle.config["configurable"]
    assert "model_name" not in bundle.config["configurable"]


def test_build_run_config_copies_files_from_request() -> None:
    """files 进入 context 时要复制一份，避免后续修改请求对象影响运行上下文。"""

    request = ChatStreamRequest(message="分析文件", files=["upload_1"])

    bundle = build_run_config(
        thread_id="thread_abc",
        run_id="run_abc",
        request=request,
    )
    request.files.append("upload_2")

    assert bundle.context.files == ["upload_1"]


def test_build_run_config_copies_resolved_uploaded_files() -> None:
    """解析后的上传文件元数据进入 context 时也要复制，避免外部后续修改。"""

    request = ChatStreamRequest(message="分析上传文件", files=["file_abc123def456"])
    uploaded_file = UploadedFileContext(
        id="file_abc123def456",
        filename="report.md",
        content_type="text/markdown",
        size_bytes=8,
        workspace_path="uploads/file_abc123def456/report.md",
    )

    bundle = build_run_config(
        thread_id="thread_upload",
        run_id="run_upload",
        request=request,
        uploaded_files=[uploaded_file],
    )
    uploaded_file.filename = "changed.md"

    assert bundle.context.files == ["file_abc123def456"]
    assert bundle.context.uploaded_files[0].filename == "report.md"
    assert bundle.context.uploaded_files[0].workspace_path == (
        "uploads/file_abc123def456/report.md"
    )


def test_build_run_config_uses_flash_as_lightweight_mode() -> None:
    """flash 模式是轻量模式，不启用思考、规划和子 agent。"""

    request = ChatStreamRequest(message="快速回答", mode="flash")

    bundle = build_run_config(
        thread_id="thread_fast",
        run_id="run_fast",
        request=request,
    )

    assert bundle.context.thinking_enabled is False
    assert bundle.context.is_plan_mode is False
    assert bundle.context.subagent_enabled is False


def test_build_run_config_respects_explicit_thinking_override() -> None:
    """thinking 是模型原生能力开关，可以独立于 mode 显式关闭。"""

    request = ChatStreamRequest(
        message="复杂分析但不要打开模型原生思考",
        mode="pro",
        thinking_enabled=False,
    )

    bundle = build_run_config(
        thread_id="thread_no_thinking",
        run_id="run_no_thinking",
        request=request,
    )

    assert bundle.context.thinking_enabled is False
    assert bundle.context.is_plan_mode is True
    assert bundle.context.subagent_enabled is False


def test_build_run_config_carries_model_provider_provenance() -> None:
    """前端所选模型的来源 provider 要进入 context，供 runtime 按来源路由（而非按 id 猜）。"""

    request = ChatStreamRequest(
        message="用中转站的 claude",
        model_name="claude-3-5-sonnet",
        provider="custom",
    )

    bundle = build_run_config(thread_id="t", run_id="r", request=request)

    assert bundle.context.model_provider == "custom"


def test_build_run_config_defaults_model_provider_to_none() -> None:
    """老客户端不带 provider 时 model_provider 为 None，runtime 回退到按 id 前缀推断。"""

    bundle = build_run_config(
        thread_id="t",
        run_id="r",
        request=ChatStreamRequest(message="老客户端"),
    )

    assert bundle.context.model_provider is None
