"""模块 11 测试：SlotFlow harness 安全内置工具。"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from pypdf import PdfWriter

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import features_from_run_context
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools import ask_clarification_tool, slotflow_context_tool
from app.harness.tools.registry import build_harness_tools
from app.harness.tools.workspace import build_workspace_tools


class ToolAwareFakeMessagesListChatModel(FakeMessagesListChatModel):
    """测试用 fake model：允许 LangChain agent 绑定工具。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _bundle():
    request = ChatStreamRequest(message="调用工具", mode="pro")
    return build_run_config(
        thread_id="thread_tool",
        run_id="run_tool",
        request=request,
    )


def test_slotflow_context_tool_is_read_only_and_json_shaped() -> None:
    """第一批内置工具只返回上下文摘要，不碰文件、网络或 sandbox。"""

    raw = slotflow_context_tool.invoke(
        {
            "thread_id": "thread_tool",
            "run_id": "run_tool",
            "mode": "pro",
        }
    )

    assert json.loads(raw) == {
        "thread_id": "thread_tool",
        "run_id": "run_tool",
        "mode": "pro",
        "source": "slotflow_context_tool",
    }


def test_ask_clarification_tool_returns_structured_placeholder() -> None:
    raw = ask_clarification_tool.invoke(
        {
            "question": "你想分析哪个币种？",
            "clarification_type": "ambiguous_requirement",
            "context": "昨天的记忆里有 BTC 和 ETH。",
            "options": ["BTC", "ETH", "其他"],
        }
    )

    assert json.loads(raw) == {
        "question": "你想分析哪个币种？",
        "clarification_type": "ambiguous_requirement",
        "context": "昨天的记忆里有 BTC 和 ETH。",
        "options": ["BTC", "ETH", "其他"],
        "source": "slotflow_clarification_tool",
    }


def test_build_harness_tools_adds_safe_builtin_and_dedupes_by_name() -> None:
    """registry 是 builtin/workspace/network/customization 等工具的统一入口。"""

    @tool("slotflow_context")
    def replacement_context_tool() -> str:
        """Replacement tool used to prove first-name wins dedupe."""

        return "replacement"

    tools = build_harness_tools(
        features=features_from_run_context(_bundle().context),
        extra_tools=[replacement_context_tool],
    )

    assert [tool.name for tool in tools] == [
        "slotflow_context",
        "ask_clarification",
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
        "artifact_write",
        "web_fetch",
        "web_search",
        "skill_match",
        "find-skills",
        "skill_list",
        "skill_install",
        "mcp_add_http",
        "search_skill_repos",
    ]
    assert tools[0] is replacement_context_tool


def test_workspace_tools_list_and_read_text_files(tmp_path: Path) -> None:
    """文件工具只能通过 SlotFlowWorkspace 访问 workspace root 内部内容。"""

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("hello", encoding="utf-8")
    tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(workspace_root=root, writes_enabled=False)
        )
    }

    assert list(tools) == [
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
    ]
    listing = json.loads(tools["workspace_list"].invoke({"path": "."}))
    read_result = json.loads(tools["workspace_read"].invoke({"path": "docs/a.txt"}))

    assert listing == {
        "path": ".",
        "entries": [{"path": "docs", "kind": "directory", "size_bytes": None}],
        "source": "slotflow_workspace",
    }
    assert read_result == {
        "path": "docs/a.txt",
        "kind": "text",
        "media_type": "text/plain",
        "content": "hello",
        "size_bytes": 5,
        "source": "slotflow_workspace",
        "metadata": {"format": "txt"},
    }


def test_workspace_read_extracts_docx_pdf_and_image_metadata(tmp_path: Path) -> None:
    """workspace_read returns model-readable payloads for common upload formats."""

    root = tmp_path / "workspace"
    root.mkdir()
    create_docx(root / "note.docx", "Docx hello")
    create_docx(root / "docx", "No suffix docx hello")
    create_blank_pdf(root / "blank.pdf")
    (root / "photo.jpg").write_bytes(tiny_jpeg_bytes(width=2, height=1))
    tools = {
        item.name: item
        for item in build_workspace_tools(SlotFlowSandboxConfig(workspace_root=root))
    }

    docx = json.loads(tools["workspace_read"].invoke({"path": "note.docx"}))
    no_suffix_docx = json.loads(tools["workspace_read"].invoke({"path": "docx"}))
    pdf = json.loads(tools["workspace_read"].invoke({"path": "blank.pdf"}))
    image = json.loads(tools["workspace_read"].invoke({"path": "photo.jpg"}))

    assert docx["kind"] == "document"
    assert docx["content"] == "Docx hello"
    assert no_suffix_docx["kind"] == "document"
    assert no_suffix_docx["content"] == "No suffix docx hello"
    assert no_suffix_docx["media_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert pdf["kind"] == "pdf"
    assert pdf["metadata"]["pages"] == 1
    assert pdf["warning"] == "pdf text extraction returned no text"
    assert image["kind"] == "image"
    assert image["metadata"] == {"format": "JPEG", "width": 2, "height": 1}
    assert "image pixels are not inlined" in image["warning"]


def test_workspace_tree_search_and_artifact_write(tmp_path: Path) -> None:
    """Claude Code style workspace navigation stays inside the sandbox boundary."""

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("SlotFlow tools are useful", encoding="utf-8")
    tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(
                workspace_root=root,
                writes_enabled=True,
            )
        )
    }

    tree = json.loads(tools["workspace_tree"].invoke({"path": ".", "max_depth": 3}))
    search = json.loads(tools["workspace_search"].invoke({"query": "tools"}))
    artifact = json.loads(
        tools["artifact_write"].invoke(
            {"path": "summary.md", "content": "# Summary"}
        )
    )
    artifacts = json.loads(tools["artifact_list"].invoke({}))

    assert {"path": "docs/guide.md", "kind": "file", "size_bytes": 25} in tree["entries"]
    assert search["matches"][0]["path"] == "docs/guide.md"
    assert artifact["path"] == "artifacts/summary.md"
    assert artifacts["entries"] == [
        {"path": "artifacts/summary.md", "kind": "file", "size_bytes": 9}
    ]


def test_artifact_write_tool_is_only_registered_when_enabled(tmp_path: Path) -> None:
    """artifact_write 仅在 sandbox 开启写入时注册，且是唯一的写工具。"""

    read_only_tools = build_workspace_tools(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "readonly", writes_enabled=False)
    )
    assert [item.name for item in read_only_tools] == [
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
    ]

    writable_root = tmp_path / "writable"
    writable_tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(
                workspace_root=writable_root,
                writes_enabled=True,
            ),
            thread_id="thread_abc123abc123",
        )
    }

    assert list(writable_tools) == [
        "workspace_list",
        "workspace_read",
        "workspace_tree",
        "workspace_search",
        "artifact_list",
        "artifact_write",
    ]
    assert "workspace_write" not in writable_tools

    raw = writable_tools["artifact_write"].invoke(
        {"path": "notes/a.txt", "content": "hello"}
    )
    assert json.loads(raw) == {
        "path": "artifacts/thread_abc123abc123/notes/a.txt",
        "bytes_written": 5,
        "source": "slotflow_workspace",
    }
    assert (
        writable_root / "artifacts" / "thread_abc123abc123" / "notes" / "a.txt"
    ).read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_harness_graph_can_execute_builtin_tool_call() -> None:
    """真实 LangGraph graph 能执行 harness 绑定的内置工具。"""

    bundle = _bundle()
    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "slotflow_context",
                        "args": {
                            "thread_id": bundle.context.thread_id,
                            "run_id": bundle.context.run_id,
                            "mode": bundle.context.mode,
                        },
                        "id": "call_slotflow_context",
                    }
                ],
            ),
            AIMessage(content="工具结果已经收到。"),
        ]
    )
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(system_prompt="你是测试 harness 的助手。"),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "读取当前 SlotFlow run context"}]},
        config=bundle.config,
        context=bundle.context,
    )

    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 1
    assert tool_messages[0].name == "slotflow_context"
    assert json.loads(str(tool_messages[0].content))["run_id"] == bundle.context.run_id
    assert result["messages"][-1].content == "工具结果已经收到。"


def create_docx(path: Path, text: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)


def create_blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())


def tiny_jpeg_bytes(*, width: int, height: int) -> bytes:
    return b"".join(
        [
            b"\xff\xd8",
            b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00",
            b"\xff\xc0\x00\x11\x08",
            height.to_bytes(2, "big"),
            width.to_bytes(2, "big"),
            b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00",
            b"\xff\xd9",
        ]
    )


def test_find_skill_repos_on_github_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_skill_repos hits GitHub's repo search and returns installable repos."""

    from app.harness.tools import customization

    captured: dict = {}

    def fake_fetch_url(*, url, config, include_raw=False, **kwargs):
        captured["url"] = url
        return {
            "_raw_content": json.dumps(
                {
                    "items": [
                        {
                            "full_name": "acme/research-skill",
                            "html_url": "https://github.com/acme/research-skill",
                            "description": "Deep research skill",
                            "stargazers_count": 42,
                        },
                        {
                            "full_name": "x/y",
                            "html_url": "https://github.com/x/y",
                            "description": None,
                            "stargazers_count": 1,
                        },
                    ]
                }
            ),
        }

    monkeypatch.setattr(customization, "fetch_url", fake_fetch_url)

    result = customization.find_skill_repos_on_github(
        query="研究 世界杯", max_results=5, config=SlotFlowSandboxConfig()
    )

    assert "api.github.com/search/repositories" in captured["url"]
    assert [item["repo"] for item in result["results"]] == ["acme/research-skill", "x/y"]
    assert result["results"][0]["stars"] == 42
    assert result["source"] == "slotflow_customization"


def test_find_skill_repos_on_github_surfaces_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.tools import customization

    monkeypatch.setattr(
        customization,
        "fetch_url",
        lambda **kwargs: {"error": "network tools are disabled", "source": "slotflow_network"},
    )

    result = customization.find_skill_repos_on_github(
        query="research", max_results=5, config=SlotFlowSandboxConfig()
    )

    assert result["results"] == []
    assert result["error"] == "network tools are disabled"
