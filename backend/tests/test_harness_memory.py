"""Tests for SlotFlow long-term memory."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import SlotFlowRuntimeConfig
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.memory import SlotFlowMemoryStore
from app.harness.memory.extractor import SlotFlowMemoryExtractor, parse_extracted_facts
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.steps.long_term_memory import (
    append_memory_system_message,
    build_extraction_conversation,
    build_turn_memory_content,
    explicit_save_update,
    retrieve_memories,
)
from app.harness.tools.memory import build_memory_tools
from app.main import create_app


def _context():
    return build_run_config(
        thread_id="thread_memory",
        run_id="run_memory",
        request=ChatStreamRequest(message="记住我喜欢简洁回答"),
    ).context


def test_memory_store_adds_lists_searches_and_dedupes_by_run(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")

    first = store.add_memory(
        thread_id="thread_a",
        source_run_id="run_a",
        content="用户喜欢简洁回答",
    )
    duplicate = store.add_memory(
        thread_id="thread_a",
        source_run_id="run_a",
        content="duplicate should not win",
    )

    assert duplicate.id == first.id
    assert [item.id for item in store.list_memories(thread_id="thread_a")] == [first.id]
    assert store.search_memories(query="怎么回答更简洁", thread_id="thread_b")[0].id == first.id


def test_memory_store_canonicalizes_common_user_facts(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")

    profile = store.add_memory(
        kind="profile",
        content="中记住事实:我叫肖峰 控制工程硕士",
    )
    preference = store.add_memory(
        kind="preference",
        content="记住我希望以后回答更简洁",
    )
    birthday = store.add_memory(
        kind="fact",
        content="再记住:农历9月30日是我的生日",
    )

    assert profile.content == "用户的姓名是肖峰。用户的职业是研究生。用户的专业是控制工程。"
    assert preference.content == "用户的偏好是：以后回答更简洁。"
    assert birthday.content == "用户的生日是农历9月30日。"


def test_memory_store_touches_existing_kind_content_instead_of_adding(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")

    first = store.add_memory(
        thread_id="thread_a",
        kind="preference",
        content="记住我喜欢中文",
    )
    duplicate = store.add_memory(
        thread_id="thread_b",
        source_run_id="run_b",
        kind="preference",
        content="我喜欢中文",
        metadata={"source": "memory_save"},
    )

    assert duplicate.id == first.id
    assert duplicate.thread_id == "thread_b"
    assert duplicate.source_run_id == "run_b"
    assert duplicate.metadata["source"] == "memory_save"
    assert [item.id for item in store.list_memories(limit=10)] == [first.id]


def test_explicit_save_update_saves_latest_turn(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")

    update = explicit_save_update(
        messages=[
            HumanMessage(content="记住我喜欢中文"),
            AIMessage(content="我会记住。"),
        ],
        context=_context(),
        memory_store=store,
    )

    saved = update["slotflow"]["long_term_memory_saved"]
    assert saved["thread_id"] == "thread_memory"
    assert saved["kind"] == "preference"
    assert saved["content"] == "用户的偏好是：喜欢中文。"
    assert store.list_memories(thread_id="thread_memory")[0].id == saved["id"]


def test_explicit_save_update_skips_after_memory_save_tool(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    context = _context()
    tools = {item.name: item for item in build_memory_tools(memory_store=store, run_context=context)}
    tool_result = tools["memory_save"].invoke({"content": "我喜欢中文", "kind": "preference"})

    update = explicit_save_update(
        messages=[
            HumanMessage(content="记住我喜欢中文"),
            ToolMessage(content=tool_result, name="memory_save", tool_call_id="call_memory_save"),
            AIMessage(content="已记住。"),
        ],
        context=context,
        memory_store=store,
    )

    assert update is None
    records = store.list_memories(thread_id="thread_memory")
    assert len(records) == 1
    assert records[0].content == "用户的偏好是：喜欢中文。"


def test_append_memory_system_message_injects_relevant_memories(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    store.add_memory(
        thread_id="other_thread",
        source_run_id="run_old",
        content="用户喜欢简洁回答",
    )
    memories = retrieve_memories(
        messages=[HumanMessage(content="请简洁解释 MCP")],
        context=_context(),
        memory_store=store,
    )
    system_message = append_memory_system_message(
        SystemMessage(content="base system"),
        memories=memories,
    )

    system_content = system_message.content
    assert "base system" in system_content
    assert "<slotflow-long-term-memory>" in system_content
    assert "用户记录：用户喜欢简洁回答。" in system_content


def test_append_memory_system_message_injects_capability_prompt_without_matches(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    memories = retrieve_memories(
        messages=[HumanMessage(content="你有没有长期记忆？")],
        context=_context(),
        memory_store=store,
    )
    system_message = append_memory_system_message(
        SystemMessage(content="base system"),
        memories=memories,
    )

    system_content = system_message.content
    assert "SlotFlow 本地长期记忆已启用" in system_content
    assert "本轮没有检索到相关长期记忆" in system_content


def test_build_turn_memory_content_ignores_generic_turns() -> None:
    content = build_turn_memory_content(
        [
            HumanMessage(content="old"),
            AIMessage(content="old answer"),
            HumanMessage(content="new"),
            AIMessage(content="new answer"),
        ]
    )

    assert content is None


def test_build_turn_memory_content_ignores_implicit_preference() -> None:
    # Implicit preferences are now handled by the background LLM extractor, not this sync path.
    content = build_turn_memory_content(
        [
            HumanMessage(content="我希望以后回答更简洁"),
            AIMessage(content="好的。"),
        ]
    )

    assert content is None


def test_build_turn_memory_content_extracts_explicit_remember() -> None:
    content = build_turn_memory_content(
        [
            HumanMessage(content="请记住我希望以后回答更简洁"),
            AIMessage(content="好的。"),
        ]
    )

    assert content == "我希望以后回答更简洁"


def test_build_extraction_conversation_renders_latest_turn() -> None:
    conversation = build_extraction_conversation(
        [
            HumanMessage(content="我在做控制工程方向的研究"),
            AIMessage(content="了解了。"),
        ]
    )

    assert "User: 我在做控制工程方向的研究" in conversation
    assert "Assistant: 了解了。" in conversation


def test_parse_extracted_facts_reads_json_array_and_coerces_kind() -> None:
    facts = parse_extracted_facts(
        '思考...\n[{"kind": "preference", "content": "用户喜欢简洁"}, '
        '{"kind": "weird", "content": "用户在做控制工程"}, {"content": ""}]'
    )

    assert facts == [
        {"kind": "preference", "content": "用户喜欢简洁"},
        {"kind": "fact", "content": "用户在做控制工程"},  # unknown kind -> fact, blank dropped
    ]


def test_parse_extracted_facts_returns_empty_on_junk() -> None:
    assert parse_extracted_facts("no json here") == []
    assert parse_extracted_facts("[]") == []


@pytest.mark.asyncio
async def test_memory_extractor_reads_facts_from_model() -> None:
    model = FakeListChatModel(
        responses=['[{"kind": "topic", "content": "用户正在做 SlotFlow 项目"}]']
    )
    extractor = SlotFlowMemoryExtractor(model)

    facts = await extractor.aextract("User: 我最近在做 SlotFlow\nAssistant: 好的")

    assert facts == [{"kind": "topic", "content": "用户正在做 SlotFlow 项目"}]


@pytest.mark.asyncio
async def test_background_extraction_saves_facts(tmp_path: Path) -> None:
    from app.harness.memory.extractor import SlotFlowMemoryExtractor
    from app.harness.steps.long_term_memory import aextract_and_save

    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    model = FakeListChatModel(
        responses=['[{"kind": "profile", "content": "用户是控制工程硕士"}]']
    )
    extractor = SlotFlowMemoryExtractor(model)

    await aextract_and_save(
        conversation="User: 我是控制工程硕士\nAssistant: 了解",
        context=_context(),
        extractor=extractor,
        memory_store=store,
    )

    records = store.list_memories(thread_id="thread_memory")
    assert len(records) == 1
    assert records[0].kind == "profile"
    assert records[0].metadata["extraction"] == "llm"


def test_memory_tools_save_and_list_memories(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    tools = {item.name: item for item in build_memory_tools(memory_store=store, run_context=_context())}

    save_result = tools["memory_save"].invoke({"content": "用户喜欢中文回答"})
    list_result = tools["memory_list"].invoke({"query": "中文", "limit": 5})

    assert "用户记录：用户喜欢中文回答。" in save_result
    assert "用户记录：用户喜欢中文回答。" in list_result


def test_build_memory_tools_returns_four_tools(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    tools = build_memory_tools(memory_store=store, run_context=_context())
    assert [t.name for t in tools] == ["memory_list", "memory_save", "memory_update", "memory_delete"]


def test_memory_api_create_update_and_delete(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(create_app(runtime_config=SlotFlowRuntimeConfig(memory_store=store)))

    create_response = client.post(
        "/api/memory",
        json={"content": "用户喜欢简洁回答", "kind": "preference"},
    )

    assert create_response.status_code == 200
    assert create_response.json()["kind"] == "preference"
    memory_id = create_response.json()["id"]

    update_response = client.patch(
        f"/api/memory/{memory_id}",
        json={"content": "用户喜欢更简洁的中文回答", "kind": "profile"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["content"] == "用户资料：喜欢更简洁的中文回答。"
    assert update_response.json()["kind"] == "profile"
    assert client.get("/api/memory").json()[0]["id"] == memory_id

    delete_response = client.delete(f"/api/memory/{memory_id}")

    assert delete_response.status_code == 204
    assert client.get("/api/memory").json() == []


@pytest.mark.asyncio
async def test_harness_graph_runs_long_term_memory_middleware_async(
    tmp_path: Path,
) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    store.add_memory(
        thread_id="thread_memory",
        source_run_id="run_old",
        content="用户喜欢简洁回答",
    )
    bundle = build_run_config(
        thread_id="thread_memory",
        run_id="run_graph_memory",
        request=ChatStreamRequest(message="请简洁回答"),
    )
    graph = build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["memory ok"]),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试长期记忆的助手。",
            memory_store=store,
            middleware_config=SlotFlowMiddlewareConfig(
                proactive_memory_extraction_enabled=False,
            ),
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "请简洁回答"}]},
        config=bundle.config,
        context=bundle.context,
    )

    assert result["messages"][-1].content == "memory ok"
    assert "long_term_memory_saved" not in result["slotflow"]


@pytest.mark.asyncio
async def test_harness_graph_saves_durable_memory_when_user_states_preference(
    tmp_path: Path,
) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    bundle = build_run_config(
        thread_id="thread_memory",
        run_id="run_graph_memory_save",
        request=ChatStreamRequest(message="请记住:我希望以后回答更简洁"),
    )
    graph = build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["memory ok"]),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试长期记忆的助手。",
            memory_store=store,
            middleware_config=SlotFlowMiddlewareConfig(
                proactive_memory_extraction_enabled=False,
            ),
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "请记住:我希望以后回答更简洁"}]},
        config=bundle.config,
        context=bundle.context,
    )

    saved = result["slotflow"]["long_term_memory_saved"]
    assert saved["source_run_id"] == "run_graph_memory_save"
    assert saved["kind"] == "preference"
