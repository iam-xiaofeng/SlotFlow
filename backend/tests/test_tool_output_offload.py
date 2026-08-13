"""Unit tests：超长工具结果卸载到工作区（``tool_output_offload`` step）。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.sandbox.workspace import SlotFlowWorkspace
from app.harness.steps.tool_output_offload import maybe_offload_tool_message


def _workspace(tmp_path, *, writes_enabled: bool = True) -> SlotFlowWorkspace:
    return SlotFlowWorkspace(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "ws",
            writes_enabled=writes_enabled,
        )
    )


def _tool_message(content, *, name: str = "web_search", status: str = "success") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="call_1", status=status)


def test_small_tool_output_is_left_inline(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    msg = _tool_message("短结果")
    assert maybe_offload_tool_message(msg, workspace=workspace, max_chars=100) is msg


def test_large_tool_output_is_offloaded_and_round_trips(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    big = "数据行\n" * 5000  # 远超阈值
    out = maybe_offload_tool_message(_tool_message(big), workspace=workspace, max_chars=100)

    assert isinstance(out, ToolMessage)
    assert out.name == "web_search"
    assert out.tool_call_id == "call_1"
    assert out.status == "success"
    payload = json.loads(str(out.content))
    assert payload["slotflow_tool_output_offloaded"] is True
    assert payload["chars"] == len(big)
    assert payload["path"], "应有落盘引用路径"
    assert "workspace_read" in payload["how_to_read"]
    # 上下文确实变小了，且落盘全文可被工作区完整回读
    assert len(str(out.content)) < len(big)
    assert workspace.read_text(payload["path"]) == big


def test_workspace_reader_output_is_not_offloaded(tmp_path) -> None:
    """读文件/列目录类工具的输出再写回工作区是循环，跳过卸载。"""

    workspace = _workspace(tmp_path)
    msg = _tool_message("x" * 10000, name="workspace_read")
    assert maybe_offload_tool_message(msg, workspace=workspace, max_chars=100) is msg


def test_offload_falls_back_inline_when_writes_disabled(tmp_path) -> None:
    workspace = _workspace(tmp_path, writes_enabled=False)
    out = maybe_offload_tool_message(
        _tool_message("y" * 10000), workspace=workspace, max_chars=100
    )
    payload = json.loads(str(out.content))
    assert payload["slotflow_tool_output_offloaded"] is True
    assert payload["path"] is None
    assert payload["preview"]
    assert "不可写" in payload["note"]


def test_non_tool_message_passes_through(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    ai = AIMessage(content="x" * 10000)
    assert maybe_offload_tool_message(ai, workspace=workspace, max_chars=100) is ai


def test_multimodal_tool_output_is_not_offloaded(tmp_path) -> None:
    """含非文本块（图片等）的工具结果保留原多模态载荷，不卸载。"""

    workspace = _workspace(tmp_path)
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}, "文字"]
    msg = _tool_message(content)
    assert maybe_offload_tool_message(msg, workspace=workspace, max_chars=1) is msg
