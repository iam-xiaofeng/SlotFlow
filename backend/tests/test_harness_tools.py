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
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools import ask_clarification_tool
from app.harness.tools.registry import build_harness_tools
import app.harness.tools.sandbox as sandbox_tools_module
import app.harness.tools.workspace as workspace_tools_module
from app.harness.tools.workspace import MAX_WORKSPACE_READ_CHARS, build_workspace_tools


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


@pytest.mark.asyncio
async def test_workspace_tool_async_path_uses_threadpool(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "note.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "note.md").write_text("# hello", encoding="utf-8")
    calls: list[str] = []
    original_to_thread = workspace_tools_module.asyncio.to_thread

    async def spy_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(workspace_tools_module.asyncio, "to_thread", spy_to_thread)
    tools = {
        tool.name: tool
        for tool in build_workspace_tools(SlotFlowSandboxConfig(workspace_root=root))
    }

    payload = json.loads(await tools["workspace_read"].ainvoke({"path": "note.md"}))

    assert payload["content"] == "# hello"
    assert "workspace_read" in calls


@pytest.mark.asyncio
async def test_sandbox_tool_async_path_uses_threadpool(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    original_to_thread = sandbox_tools_module.asyncio.to_thread

    async def spy_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", repr(func)))
        return await original_to_thread(func, *args, **kwargs)

    def fake_exec(self, command, timeout_seconds=None):
        return {"ok": True, "command": command, "timeout_seconds": timeout_seconds}

    monkeypatch.setattr(sandbox_tools_module.asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(sandbox_tools_module.LazyDockerSandbox, "exec", fake_exec)
    tools = {
        tool.name: tool
        for tool in sandbox_tools_module.build_sandbox_tools(
            SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
        )
    }

    payload = json.loads(
        await tools["sandbox_exec"].ainvoke(
            {"command": "python -V", "timeout_seconds": 3}
        )
    )

    assert payload == {"ok": True, "command": "python -V", "timeout_seconds": 3}
    assert "sandbox_exec" in calls


def test_workspace_search_caps_candidate_scan(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    for index in range(1001):
        text = "needle" if index == 1000 else "nothing"
        (root / f"file_{index:04d}.txt").write_text(text, encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in build_workspace_tools(SlotFlowSandboxConfig(workspace_root=root))
    }

    payload = json.loads(tools["workspace_search"].invoke({"query": "needle"}))

    assert payload["matches"] == []


@pytest.mark.asyncio
async def test_ask_clarification_interrupts_and_resume_carries_answer() -> None:
    """ask_clarification pauses the graph via interrupt(); the resume value becomes the tool
    result, and once answered there is NO pending interrupt left (so it cannot re-pop)."""

    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {
                            "question": "你想分析哪个币种？",
                            "clarification_type": "ambiguous_requirement",
                            "options": ["BTC", "ETH"],
                        },
                        "id": "call_clarify",
                    }
                ],
            ),
            AIMessage(content="好的，分析 BTC。"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[ask_clarification_tool],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread_clarify"}}

    # Turn 1: the model asks → the graph pauses with the clarification payload.
    await graph.ainvoke({"messages": [{"role": "user", "content": "分析一下"}]}, config=config)
    state = await graph.aget_state(config)
    assert state.interrupts, "ask_clarification should pause the graph"
    payload = state.interrupts[0].value
    assert payload["type"] == "clarification"
    assert payload["source"] == "slotflow_clarification"
    assert payload["question"] == "你想分析哪个币种？"

    # Resume with the user's answer → it becomes the tool result; the run completes.
    result = await graph.ainvoke(Command(resume="BTC"), config=config)
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "ask_clarification"
    assert "BTC" in str(tool_messages[0].content)
    assert result["messages"][-1].content == "好的，分析 BTC。"

    # Root-cause guard for the re-popup bug: an ANSWERED clarification leaves no pending interrupt.
    after = await graph.aget_state(config)
    assert not after.interrupts


def test_build_harness_tools_adds_safe_builtin_and_dedupes_by_name() -> None:
    """registry 是 builtin/workspace/network/customization 等工具的统一入口。"""

    @tool("ask_clarification")
    def replacement_clarification_tool() -> str:
        """Replacement tool used to prove first-name wins dedupe."""

        return "replacement"

    tools = build_harness_tools(
        features=features_from_run_context(_bundle().context),
        extra_tools=[replacement_clarification_tool],
    )

    names = [tool.name for tool in tools]
    # First-name-wins dedupe: the replacement clarification tool wins, no name appears twice.
    assert tools[0] is replacement_clarification_tool
    assert names.count("ask_clarification") == 1
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    # The registry unifies builtin + workspace + network + customization tools.
    assert {
        "ask_clarification",
        "artifact_write",
        "sandbox_exec",
        "sandbox_artifact_copy",
        "web_search",
        "skill_match",
        "search_skill_repos",
        "write_todos",  # plan_enabled (ultra) exposes the todo tool
    } <= set(names)


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
        "workspace_grep",
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
        # 每次读取都带分页信息:没截断时也要说清楚"就这么多",模型才知道不用再翻页。
        "read": {"truncated": False, "total_chars": 5, "offset": 0},
    }


def test_workspace_read_caps_long_files_and_offers_the_next_offset(tmp_path: Path) -> None:
    """大文件必须截断并给出续读位置。

    2026-08-14 真机:`workspace_read` 是整个工具集里唯一没有任何上限的读口(工具结果卸载
    还刻意跳过它),一个 446KB 的上传文件被整段内联成 373K 字符的 ToolMessage(≈166k token),
    之后模型每次都返回空响应,thread 被永久毒化。见 HARNESS_NOTES §63。
    """

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    body = "x" * (MAX_WORKSPACE_READ_CHARS + 500)
    (root / "docs" / "big.txt").write_text(body, encoding="utf-8")
    tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(workspace_root=root, writes_enabled=False)
        )
    }

    first = json.loads(tools["workspace_read"].invoke({"path": "docs/big.txt"}))
    assert len(first["content"]) == MAX_WORKSPACE_READ_CHARS
    assert first["read"]["truncated"] is True
    assert first["read"]["total_chars"] == len(body)
    assert first["read"]["next_offset"] == MAX_WORKSPACE_READ_CHARS

    rest = json.loads(
        tools["workspace_read"].invoke(
            {"path": "docs/big.txt", "offset": first["read"]["next_offset"]}
        )
    )
    assert len(rest["content"]) == 500
    assert rest["read"]["truncated"] is False
    # 两段拼起来必须正好是原文,截断不能丢字符。
    assert first["content"] + rest["content"] == body


def test_workspace_read_extracts_docx_pdf_and_image_metadata(tmp_path: Path) -> None:
    """workspace_read returns model-readable payloads for common upload formats."""

    root = tmp_path / "workspace"
    root.mkdir()
    create_docx(root / "note.docx", "Docx hello")
    create_docx(root / "docx", "No suffix docx hello")
    create_xlsx(root / "table.xlsx")
    create_xlsx(root / "spreadsheet")
    create_pptx(root / "slides.pptx")
    create_blank_pdf(root / "blank.pdf")
    (root / "component.tsx").write_text("export function App() { return <div />; }\n", encoding="utf-8")
    (root / "flow.drawio").write_text(
        '<mxfile><diagram name="Page-1"><mxGraphModel /></diagram></mxfile>',
        encoding="utf-8",
    )
    (root / "legacy.xls").write_bytes(b"\xd0\xcf\x11\xe0legacy spreadsheet")
    (root / "legacy.ppt").write_bytes(b"\xd0\xcf\x11\xe0legacy presentation")
    (root / "photo.jpg").write_bytes(tiny_jpeg_bytes(width=2, height=1))
    tools = {
        item.name: item
        for item in build_workspace_tools(SlotFlowSandboxConfig(workspace_root=root))
    }

    docx = json.loads(tools["workspace_read"].invoke({"path": "note.docx"}))
    no_suffix_docx = json.loads(tools["workspace_read"].invoke({"path": "docx"}))
    xlsx = json.loads(tools["workspace_read"].invoke({"path": "table.xlsx"}))
    no_suffix_xlsx = json.loads(tools["workspace_read"].invoke({"path": "spreadsheet"}))
    pptx = json.loads(tools["workspace_read"].invoke({"path": "slides.pptx"}))
    tsx = json.loads(tools["workspace_read"].invoke({"path": "component.tsx"}))
    drawio = json.loads(tools["workspace_read"].invoke({"path": "flow.drawio"}))
    legacy_xls = json.loads(tools["workspace_read"].invoke({"path": "legacy.xls"}))
    legacy_ppt = json.loads(tools["workspace_read"].invoke({"path": "legacy.ppt"}))
    pdf = json.loads(tools["workspace_read"].invoke({"path": "blank.pdf"}))
    image = json.loads(tools["workspace_read"].invoke({"path": "photo.jpg"}))

    assert docx["kind"] == "document"
    assert docx["content"] == "Docx hello"
    assert no_suffix_docx["kind"] == "document"
    assert no_suffix_docx["content"] == "No suffix docx hello"
    assert no_suffix_docx["media_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert xlsx["kind"] == "spreadsheet"
    assert xlsx["metadata"]["sheet_count"] == 1
    assert xlsx["metadata"]["sheets"] == ["Sheet One"]
    assert "Revenue" in xlsx["content"]
    assert "42" in xlsx["content"]
    assert no_suffix_xlsx["kind"] == "spreadsheet"
    assert no_suffix_xlsx["media_type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert pptx["kind"] == "presentation"
    assert pptx["metadata"]["slides"] == 1
    assert "Quarterly Plan" in pptx["content"]
    assert tsx["kind"] == "text"
    assert tsx["media_type"] == "text/tsx"
    assert "export function App" in tsx["content"]
    assert drawio["kind"] == "diagram"
    assert drawio["media_type"] == "application/vnd.jgraph.mxfile"
    assert "<mxfile>" in drawio["content"]
    assert legacy_xls["kind"] == "binary"
    assert legacy_xls["media_type"] == "application/vnd.ms-excel"
    assert "unsupported binary" in legacy_xls["warning"]
    assert legacy_ppt["kind"] == "binary"
    assert legacy_ppt["media_type"] == "application/vnd.ms-powerpoint"
    assert "unsupported binary" in legacy_ppt["warning"]
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
    grep = json.loads(tools["workspace_grep"].invoke({"pattern": "SlotFlow"}))
    artifact = json.loads(
        tools["artifact_write"].invoke(
            {"path": "summary.md", "content": "# Summary"}
        )
    )
    artifacts = json.loads(tools["artifact_list"].invoke({}))

    assert {"path": "docs/guide.md", "kind": "file", "size_bytes": 25} in tree["entries"]
    assert search["matches"][0]["path"] == "docs/guide.md"
    assert grep["matches"][0]["path"] == "docs/guide.md"
    assert artifact["path"] == "default/artifacts/summary.md"
    assert artifacts["entries"] == [
        {"path": "default/artifacts/summary.md", "kind": "file", "size_bytes": 9}
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
        "workspace_grep",
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
        "workspace_grep",
        "artifact_list",
        "artifact_write",
    ]
    assert "workspace_write" not in writable_tools

    raw = writable_tools["artifact_write"].invoke(
        {"path": "notes/a.txt", "content": "hello"}
    )
    assert json.loads(raw) == {
        "path": "thread_abc123abc123/artifacts/notes/a.txt",
        "bytes_written": 5,
        "source": "slotflow_workspace",
    }
    assert (
        writable_root / "thread_abc123abc123" / "artifacts" / "notes" / "a.txt"
    ).read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_harness_graph_can_execute_builtin_tool_call() -> None:
    """真实 LangGraph graph 能执行 harness 绑定的工具。"""

    bundle = _bundle()

    @tool("echo_context")
    def echo_context_tool(thread_id: str, run_id: str) -> str:
        """Echo back the run context — a read-only tool for graph execution tests."""

        return json.dumps({"thread_id": thread_id, "run_id": run_id}, ensure_ascii=False)

    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_context",
                        "args": {
                            "thread_id": bundle.context.thread_id,
                            "run_id": bundle.context.run_id,
                        },
                        "id": "call_echo_context",
                    }
                ],
            ),
            AIMessage(content="工具结果已经收到。"),
        ]
    )
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试 harness 的助手。",
            middleware_config=SlotFlowMiddlewareConfig(),
        ),
        tools=[echo_context_tool],
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
    assert tool_messages[0].name == "echo_context"
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


def create_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Sheet One" sheetId="1" r:id="rId1"/></sheets>'
                "</workbook>"
            ),
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>Revenue</t></si><si><t>Notes</t></si>"
                "</sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row>'
                '<row r="2"><c r="A2" t="inlineStr"><is><t>Notes</t></is></c></row>'
                "</sheetData>"
                "</worksheet>"
            ),
        )


def create_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            (
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody>"
                "<a:p><a:r><a:t>Quarterly Plan</a:t></a:r></a:p>"
                "<a:p><a:r><a:t>Ship the preview</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld>"
                "</p:sld>"
            ),
        )


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
    # cross-tool SKILL.md ecosystem entry points (Anthropic / Codex / skills.sh) always surfaced
    repos = [item["repo"] for item in result["ecosystem_sources"]]
    assert "anthropics/skills" in repos
    assert any("codex" in repo for repo in repos)


def test_match_installed_skills_caches_and_invalidates(tmp_path, monkeypatch) -> None:
    """Local installed-skill matching is memoized within a short TTL; install clears it."""

    from app.harness.tools import customization

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "research-pro"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research-pro\ndescription: Deep research and analysis workflows.\n---\n# x\n",
        encoding="utf-8",
    )

    customization.invalidate_skill_match_cache()
    calls = {"n": 0}
    real_loader = customization.load_enabled_skills

    def counting_loader(**kwargs):
        calls["n"] += 1
        return real_loader(**kwargs)

    monkeypatch.setattr(customization, "load_enabled_skills", counting_loader)

    first = customization.match_installed_skills(query="research", skills_root=skills_root)
    second = customization.match_installed_skills(query="research", skills_root=skills_root)

    assert first and first[0]["name"] == "research-pro"
    assert second == first
    assert calls["n"] == 1  # second call served from cache

    customization.invalidate_skill_match_cache()
    customization.match_installed_skills(query="research", skills_root=skills_root)
    assert calls["n"] == 2  # cache cleared -> recomputed


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


@pytest.mark.asyncio
async def test_ask_clarification_via_slotflow_tool_node_actually_interrupts() -> None:
    """Regression: the SlotFlow tool-safety wrapper must NOT swallow GraphBubbleUp.

    ask_clarification pauses via interrupt() which raises GraphBubbleUp (an Exception
    subclass). The node+edge graph's ToolNode runs tools through the SlotFlow safety
    wrapper, whose old `except Exception` caught GraphBubbleUp and converted it to a
    tool_execution_error — so voluntary HITL never paused and the model just continued.
    This test drives the real build_slotflow_harness_graph so the wrapper is in the path.
    """

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    model = ToolAwareFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {
                            "question": "用哪种格式？",
                            "clarification_type": "ambiguous_requirement",
                            "options": ["CSV", "HTML"],
                        },
                        "id": "call_reg",
                    }
                ],
            ),
            AIMessage(content="好的，用 CSV。"),
        ]
    )
    request = ChatStreamRequest(message="导出", mode="pro")
    bundle = build_run_config(thread_id="thread_reg", run_id="run_reg", request=request)
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试助手。",
            middleware_config=SlotFlowMiddlewareConfig(),
        ),
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "导出"}]},
        config=bundle.config,
        context=bundle.context,
    )
    state = await graph.aget_state(bundle.config)
    # The graph MUST be paused on the clarification, not have swallowed the interrupt
    # into a tool_execution_error and continued.
    assert state.interrupts, "ask_clarification must pause the graph (wrapper must propagate GraphBubbleUp)"
    assert state.interrupts[0].value["question"] == "用哪种格式？"

    result = await graph.ainvoke(Command(resume="CSV"), config=bundle.config, context=bundle.context)
    assert result["messages"][-1].content == "好的，用 CSV。"
    after = await graph.aget_state(bundle.config)
    assert not after.interrupts


def _stub_tool_request(name: str = "task_tool"):
    """最小 ToolCallRequest 替身:safety wrapper 只读 ``.tool`` 与 ``.tool_call``。"""

    import types

    return types.SimpleNamespace(tool=object(), tool_call={"id": "c1", "name": name})


def test_tool_safety_reraises_retryable_infra_error() -> None:
    """限流/超时/连接/5xx 是瞬时基础设施错误(典型来自 task_tool 子代理内部的模型调用):
    safety wrapper 必须重抛让整轮干净失败,绝不能转成 tool_execution_error——否则模型会把"限流"
    误当成"子任务本身失败"、改写重试或编结论,那条假失败还会永久留在历史里每轮回读。"""

    import litellm

    from app.harness.graph import _slotflow_tool_safety_wrapper

    def handler(_req):
        raise litellm.RateLimitError("rate limited", llm_provider="custom", model="grok-4.5")

    with pytest.raises(litellm.RateLimitError):
        _slotflow_tool_safety_wrapper(_stub_tool_request(), handler)


def test_tool_safety_reraises_cancelled_error() -> None:
    """用户点停止 = CancelledError 从下往上一路拆连接;wrapper 必须重抛,吞了停止按钮就静默失效。"""

    import asyncio

    from app.harness.graph import _slotflow_tool_safety_wrapper

    def handler(_req):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _slotflow_tool_safety_wrapper(_stub_tool_request(), handler)


def test_tool_safety_still_wraps_permanent_error_as_tool_message() -> None:
    """永久错误(参数错/文件不存在/一般异常)仍应转成模型可读的 tool_execution_error,让模型自我纠正。"""

    import json

    from app.harness.graph import _slotflow_tool_safety_wrapper

    def handler(_req):
        raise ValueError("bad argument")

    result = _slotflow_tool_safety_wrapper(_stub_tool_request(), handler)
    assert result.status == "error"
    payload = json.loads(result.content)
    assert payload["error"]["type"] == "tool_execution_error"
    assert payload["error"]["exception_type"] == "ValueError"


@pytest.mark.asyncio
async def test_async_tool_safety_classifies_errors() -> None:
    """异步 wrapper 同源:瞬时基础设施错误与 CancelledError 都重抛,永久错误仍转 ToolMessage。"""

    import asyncio
    import json

    import litellm

    from app.harness.graph import _slotflow_async_tool_safety_wrapper

    async def transient(_req):
        raise litellm.Timeout("timed out", llm_provider="custom", model="grok-4.5")

    async def cancelled(_req):
        raise asyncio.CancelledError

    async def permanent(_req):
        raise FileNotFoundError("missing")

    with pytest.raises(litellm.Timeout):
        await _slotflow_async_tool_safety_wrapper(_stub_tool_request(), transient)
    with pytest.raises(asyncio.CancelledError):
        await _slotflow_async_tool_safety_wrapper(_stub_tool_request(), cancelled)
    msg = await _slotflow_async_tool_safety_wrapper(_stub_tool_request(), permanent)
    assert msg.status == "error"
    assert json.loads(msg.content)["error"]["exception_type"] == "FileNotFoundError"


def test_workspace_read_resolves_thread_relative_paths(tmp_path: Path) -> None:
    """读侧要能理解 `artifacts/x`,不能只认宿主侧的完整前缀。

    2026-08-15 真机:`artifact_write("cybervault/index.html")` 落到
    `<thread>/artifacts/cybervault/index.html`,而 `workspace_read("artifacts/cybervault/index.html")`
    按 workspace root 解析,撞上旧布局遗留的顶层 `artifacts/`,连报三次
    "workspace path is not a file"。父代理把这种路径写进子代理任务描述时必踩。
    """

    root = tmp_path / "workspace"
    thread = "thread_061a306f5d40"
    (root / thread / "artifacts" / "cybervault").mkdir(parents=True)
    (root / thread / "artifacts" / "cybervault" / "index.html").write_text(
        "<h1>real</h1>", encoding="utf-8"
    )
    # 旧布局的顶层 artifacts/:存在,但**不该**盖住本对话刚写的同名产物。
    (root / "artifacts" / "cybervault").mkdir(parents=True)
    (root / "artifacts" / "cybervault" / "index.html").write_text(
        "<h1>stale</h1>", encoding="utf-8"
    )

    tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(workspace_root=root, writes_enabled=True),
            thread_id=thread,
        )
    }

    result = json.loads(
        tools["workspace_read"].invoke({"path": "artifacts/cybervault/index.html"})
    )
    assert result["content"] == "<h1>real</h1>"
    # 回显解析后的路径,模型下一次就会直接用对的那个。
    assert result["path"] == f"{thread}/artifacts/cybervault/index.html"

    listing = json.loads(tools["workspace_list"].invoke({"path": "artifacts/cybervault"}))
    assert listing["path"] == f"{thread}/artifacts/cybervault"

    # 不带参数时只看本对话,不再把别的对话的目录暴露出去。
    tree = json.loads(tools["workspace_tree"].invoke({}))
    assert tree["path"] == thread
    assert all(entry["path"].startswith(thread) for entry in tree["entries"])


def test_workspace_read_still_serves_legacy_toplevel_paths(tmp_path: Path) -> None:
    """本对话目录里没有的,仍按老路径读得到——旧布局的存量文件不能读不了。"""

    root = tmp_path / "workspace"
    thread = "thread_legacy0000"
    (root / thread / "artifacts").mkdir(parents=True)
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "old.md").write_text("legacy", encoding="utf-8")

    tools = {
        item.name: item
        for item in build_workspace_tools(
            SlotFlowSandboxConfig(workspace_root=root, writes_enabled=False),
            thread_id=thread,
        )
    }

    result = json.loads(tools["workspace_read"].invoke({"path": "artifacts/old.md"}))
    assert result["content"] == "legacy"
    assert result["path"] == "artifacts/old.md"
