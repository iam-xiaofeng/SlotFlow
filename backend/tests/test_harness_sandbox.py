"""模块 15 测试：SlotFlow sandbox / workspace 边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel

import app.chat.runtime as runtime_module
import app.harness.builder as builder_module
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import (
    SlotFlowRuntimeConfig,
    load_positive_int_from_env,
    load_runtime_config_from_env,
)
from app.harness.config import SlotFlowHarnessConfig
from app.harness.sandbox import (
    SlotFlowSandboxConfig,
    SlotFlowWorkspace,
    WorkspaceFileTooLargeError,
    WorkspacePathError,
    WorkspaceWriteDisabledError,
)


class ToolAwareFakeListChatModel(FakeListChatModel):
    """测试用 fake model：普通 fake 文本能力 + 支持 bind_tools 边界。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _run_context():
    request = ChatStreamRequest(message="解释 sandbox", mode="pro")
    return build_run_config(
        thread_id="thread_sandbox",
        run_id="run_sandbox",
        request=request,
    ).context


def test_workspace_resolves_relative_paths_under_root(tmp_path: Path) -> None:
    """合法路径会被解析到 workspace root 内部。"""

    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )

    assert workspace.resolve_path("notes/today.md") == (
        tmp_path / "workspace" / "notes" / "today.md"
    ).resolve(strict=False)
    assert workspace.resolve_path(".") == (tmp_path / "workspace").resolve(strict=False)


def test_workspace_rejects_unsafe_user_paths(tmp_path: Path) -> None:
    """用户输入的路径不能是绝对路径、穿越路径或 Windows drive 风格路径。"""

    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )

    unsafe_paths = [
        "",
        "/etc/passwd",
        "../outside.txt",
        "notes/../../outside.txt",
        "C:/temp/file.txt",
        r"notes\file.txt",
        "bad\x00name.txt",
    ]
    for unsafe_path in unsafe_paths:
        try:
            workspace.resolve_path(unsafe_path)
        except WorkspacePathError:
            continue
        raise AssertionError(f"unsafe path was accepted: {unsafe_path!r}")


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    """即使 workspace 内部有 symlink，也不能借它跳出 root。"""

    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    workspace = SlotFlowWorkspace(SlotFlowSandboxConfig(workspace_root=root))

    try:
        workspace.resolve_path("escape/file.txt")
    except WorkspacePathError:
        pass
    else:
        raise AssertionError("symlink escape was accepted")


def test_workspace_read_and_list_enforce_root_and_read_size(tmp_path: Path) -> None:
    """workspace 可以列目录和读小文件，但会拒绝超限文件。"""

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("ok", encoding="utf-8")
    (root / "big.txt").write_text("too large", encoding="utf-8")
    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(
            workspace_root=root,
            max_read_bytes=4,
        )
    )

    assert workspace.read_text("docs/a.txt") == "ok"
    assert [(entry.path, entry.kind, entry.size_bytes) for entry in workspace.list_entries(".")] == [
        ("big.txt", "file", 9),
        ("docs", "directory", None),
    ]

    try:
        workspace.read_text("big.txt")
    except WorkspaceFileTooLargeError:
        pass
    else:
        raise AssertionError("oversized read was accepted")


def test_workspace_write_can_be_disabled_and_is_byte_limited(tmp_path: Path) -> None:
    """显式关闭写入后拒绝写；打开后仍受 max_write_bytes 约束。"""

    read_only_workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "readonly", writes_enabled=False)
    )
    try:
        read_only_workspace.write_text("a.txt", "hello")
    except WorkspaceWriteDisabledError:
        pass
    else:
        raise AssertionError("write was accepted while disabled")

    writable_workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "writable",
            writes_enabled=True,
            max_write_bytes=5,
        )
    )
    target = writable_workspace.write_text("nested/a.txt", "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    try:
        writable_workspace.write_text("nested/b.txt", "too large")
    except WorkspaceFileTooLargeError:
        pass
    else:
        raise AssertionError("oversized write was accepted")


def test_runtime_loads_sandbox_config_from_env(monkeypatch, tmp_path: Path) -> None:
    """runtime 只解析 sandbox 配置，不创建 workspace 或工具。"""

    monkeypatch.setenv("SLOTFLOW_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_WRITES_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", "123")
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES", "456")

    config = load_runtime_config_from_env()

    assert config.sandbox_config == SlotFlowSandboxConfig(
        workspace_root=tmp_path / "workspace",
        writes_enabled=True,
        max_read_bytes=123,
        max_write_bytes=456,
    )


def test_positive_int_env_validation(monkeypatch) -> None:
    """字节上限必须是正整数。"""

    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", "0")

    try:
        load_positive_int_from_env("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", default=10)
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("non-positive integer was accepted")


def test_runtime_passes_sandbox_config_to_harness(monkeypatch, tmp_path: Path) -> None:
    """runtime 到 harness 的委托不能丢失 sandbox 配置。"""

    captured: dict[str, Any] = {}
    sandbox_config = SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")

    def fake_build_slotflow_harness_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        runtime_module.adapter,
        "build_slotflow_harness_graph",
        fake_build_slotflow_harness_graph,
    )

    runtime_module.create_langgraph_agent_graph(
        model=FakeListChatModel(responses=["ok"]),
        runtime_config=SlotFlowRuntimeConfig(
            system_prompt="base prompt",
            sandbox_config=sandbox_config,
        ),
        run_context=_run_context(),
    )

    assert captured["harness_config"] == SlotFlowHarnessConfig(
        system_prompt="base prompt",
        sandbox_config=sandbox_config,
    )


def test_harness_builder_passes_sandbox_config_to_tool_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """builder 会把 sandbox 配置交给 tools registry，供后续文件工具使用。"""

    captured_tools_kwargs: dict[str, Any] = {}
    sandbox_config = SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")

    def fake_build_harness_tools(**kwargs):
        captured_tools_kwargs.update(kwargs)
        return []

    def fake_build_slotflow_graph(**kwargs):
        return object()

    monkeypatch.setattr(builder_module, "build_harness_tools", fake_build_harness_tools)
    monkeypatch.setattr(builder_module, "build_slotflow_graph", fake_build_slotflow_graph)

    builder_module.build_slotflow_harness_graph(
        model=ToolAwareFakeListChatModel(responses=["ok"]),
        run_context=_run_context(),
        harness_config=SlotFlowHarnessConfig(
            system_prompt="base prompt",
            sandbox_config=sandbox_config,
        ),
    )

    assert captured_tools_kwargs["sandbox_config"] is sandbox_config
