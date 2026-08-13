"""Step: 超长工具结果卸载到工作区文件（上下文只留引用句柄，按需回读）。

Manus「file system as context」的落地：一条工具结果文本超过阈值时，把全文写进工作区一个
隐藏文件，`ToolMessage` 内容替换成 ``{引用路径 + 字符/行数 + 预览 + 回读指引}``。模型要看全文
就用已默认激活的 ``workspace_read('<path>')``；只想定位关键片段就用
``workspace_grep('<关键词>', '<path>')``——从而把一条巨型工具输出从"整段挤爆上下文"变成
"一句引用 + 按需分块读"。

设计约束：
- 只处理 `ToolMessage`；loader 返回的 `Command` 以及非纯文本(多模态图片块等)一律原样放过。
- 工作区自身的读/导航工具(`workspace_read` 等)不卸载——把它们的输出再写回工作区是循环。
- 写盘失败 / 工作区只读 / 超 `max_write_bytes` → 回退为"内联截断 + 预览"，绝不抛异常拖垮工具节点。
- 幂等命名：按 `tool_call_id` 命名，同一次调用只落一个文件。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import ToolMessage

from app.harness.sandbox.workspace import SlotFlowWorkspace

# 存放卸载文件的隐藏目录（相对工作区根）。
_OFFLOAD_DIR = ".slotflow_offload"
# 预览取首尾，够模型判断"要不要回读"，又不至于把全文塞回上下文。
_PREVIEW_HEAD_CHARS = 800
_PREVIEW_TAIL_CHARS = 400
# 这些工具本身就是"读文件/列目录"，把它们的输出再卸载回工作区是循环，跳过。
_OFFLOAD_SKIP_TOOLS = frozenset(
    {
        "workspace_read",
        "workspace_list",
        "workspace_tree",
        "workspace_search",
        "workspace_grep",
        "artifact_list",
    }
)


def _message_text(content: Any) -> str | None:
    """把 ToolMessage.content 归一成纯文本；含结构化(非文本)块则返回 None 表示不卸载。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                # 出现图片等非文本块，保留原多模态载荷，不卸载。
                return None
        return "".join(parts)
    return None


def _safe_stem(tool_name: str, tool_call_id: str) -> str:
    raw = f"{tool_name or 'tool'}-{tool_call_id or 'call'}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:120]


def _preview(text: str) -> str:
    if len(text) <= _PREVIEW_HEAD_CHARS + _PREVIEW_TAIL_CHARS:
        return text
    omitted = len(text) - _PREVIEW_HEAD_CHARS - _PREVIEW_TAIL_CHARS
    return f"{text[:_PREVIEW_HEAD_CHARS]}\n…（此处省略约 {omitted} 字）…\n{text[-_PREVIEW_TAIL_CHARS:]}"


def maybe_offload_tool_message(
    result: Any,
    *,
    workspace: SlotFlowWorkspace,
    max_chars: int,
) -> Any:
    """超阈值的 `ToolMessage` → 写工作区文件并替换为引用句柄；否则原样返回。"""

    if not isinstance(result, ToolMessage):
        return result
    if (result.name or "") in _OFFLOAD_SKIP_TOOLS:
        return result
    text = _message_text(result.content)
    if text is None or len(text) <= max_chars:
        return result

    tool_name = result.name or "tool"
    rel_path = f"{_OFFLOAD_DIR}/{_safe_stem(tool_name, result.tool_call_id)}.txt"
    total_chars = len(text)
    total_lines = text.count("\n") + 1

    saved_path: str | None = None
    try:
        # write_text 自带 max_write_bytes 上限；超限时按字节截断后再写，句柄里注明被截断。
        payload = text
        encoded = payload.encode("utf-8")
        if len(encoded) > workspace.config.max_write_bytes:
            payload = encoded[: workspace.config.max_write_bytes].decode("utf-8", errors="ignore")
        target = workspace.write_text(rel_path, payload)
        saved_path = target.relative_to(workspace.root).as_posix()
    except Exception:  # noqa: BLE001 - 写盘失败一律回退内联，绝不抛出
        saved_path = None

    handle: dict[str, Any] = {
        "slotflow_tool_output_offloaded": True,
        "tool_name": tool_name,
        "chars": total_chars,
        "lines": total_lines,
        "preview": _preview(text),
    }
    if saved_path is not None:
        handle["path"] = saved_path
        handle["how_to_read"] = (
            f"完整结果已保存到工作区文件 '{saved_path}'。需要全文用 "
            f"workspace_read('{saved_path}')；只找关键片段用 "
            f"workspace_grep('<关键词>', '{saved_path}')。"
        )
        handle["note"] = "上下文只保留预览以节省 token；不要凭预览臆断，需要细节请回读该文件。"
    else:
        handle["path"] = None
        handle["note"] = (
            "工作区不可写，完整结果无法落盘；以下仅为截断预览。"
            "如需完整信息请缩小该工具的输出范围后重试。"
        )

    return ToolMessage(
        content=json.dumps(handle, ensure_ascii=False),
        name=result.name,
        tool_call_id=result.tool_call_id,
        status=result.status,
    )
