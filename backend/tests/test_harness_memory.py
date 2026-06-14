"""Tests for SlotFlow long-term memory."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import SlotFlowRuntimeConfig
from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.memory import SlotFlowMemoryStore
from app.harness.middleware import SlotFlowLongTermMemoryMiddleware
from app.harness.middleware.long_term_memory import build_turn_memory_content
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


def test_memory_middleware_saves_latest_turn(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    middleware = SlotFlowLongTermMemoryMiddleware(memory_store=store)

    update = middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="记住我喜欢中文"),
                AIMessage(content="我会记住。"),
            ]
        },
        Runtime(context=_context()),
    )

    saved = update["slotflow"]["long_term_memory_saved"]
    assert saved["thread_id"] == "thread_memory"
    assert saved["kind"] == "preference"
    assert saved["content"] == "我喜欢中文"
    assert store.list_memories(thread_id="thread_memory")[0].id == saved["id"]


def test_memory_middleware_injects_relevant_memories_into_model_request(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    store.add_memory(
        thread_id="other_thread",
        source_run_id="run_old",
        content="用户喜欢简洁回答",
    )
    middleware = SlotFlowLongTermMemoryMiddleware(memory_store=store)
    captured: dict[str, ModelRequest] = {}
    base_request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="请简洁解释 MCP")],
        system_message=SystemMessage(content="base system"),
        runtime=Runtime(context=_context()),
    )

    def handler(request: ModelRequest) -> ModelResponse:
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(base_request, handler)

    system_content = captured["request"].system_message.content
    assert "base system" in system_content
    assert "<slotflow-long-term-memory>" in system_content
    assert "用户喜欢简洁回答" in system_content


def test_memory_middleware_injects_capability_prompt_without_matches(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    middleware = SlotFlowLongTermMemoryMiddleware(memory_store=store)
    captured: dict[str, ModelRequest] = {}
    base_request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="你有没有长期记忆？")],
        system_message=SystemMessage(content="base system"),
        runtime=Runtime(context=_context()),
    )

    def handler(request: ModelRequest) -> ModelResponse:
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(base_request, handler)

    system_content = captured["request"].system_message.content
    assert "SlotFlow 本地长期记忆已启用" in system_content
    assert "本轮没有检索到相关长期记忆" in system_content


@pytest.mark.asyncio
async def test_memory_middleware_injects_relevant_memories_in_async_model_request(
    tmp_path: Path,
) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    store.add_memory(
        thread_id="other_thread",
        source_run_id="run_old",
        content="用户喜欢简洁回答",
    )
    middleware = SlotFlowLongTermMemoryMiddleware(memory_store=store)
    captured: dict[str, ModelRequest] = {}
    base_request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="请简洁解释 MCP")],
        system_message=SystemMessage(content="base system"),
        runtime=Runtime(context=_context()),
    )

    async def handler(request: ModelRequest) -> ModelResponse:
        captured["request"] = request
        return ModelResponse(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(base_request, handler)

    system_content = captured["request"].system_message.content
    assert "base system" in system_content
    assert "<slotflow-long-term-memory>" in system_content
    assert "用户喜欢简洁回答" in system_content


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


def test_build_turn_memory_content_extracts_preference() -> None:
    content = build_turn_memory_content(
        [
            HumanMessage(content="我希望以后回答更简洁"),
            AIMessage(content="好的。"),
        ]
    )

    assert content == "我希望以后回答更简洁"


def test_memory_tools_save_and_list_memories(tmp_path: Path) -> None:
    store = SlotFlowMemoryStore(tmp_path / "memory.sqlite3")
    tools = {
        item.name: item
        for item in build_memory_tools(memory_store=store, run_context=_context())
    }

    save_result = tools["memory_save"].invoke({"content": "用户喜欢中文回答"})
    list_result = tools["memory_list"].invoke({"query": "中文", "limit": 5})

    assert "用户喜欢中文回答" in save_result
    assert "用户喜欢中文回答" in list_result


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
    assert update_response.json()["content"] == "用户喜欢更简洁的中文回答"
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
        request=ChatStreamRequest(message="我希望以后回答更简洁"),
    )
    graph = build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["memory ok"]),
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt="你是测试长期记忆的助手。",
            memory_store=store,
        ),
    )

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "我希望以后回答更简洁"}]},
        config=bundle.config,
        context=bundle.context,
    )

    saved = result["slotflow"]["long_term_memory_saved"]
    assert saved["source_run_id"] == "run_graph_memory_save"
    assert saved["kind"] == "preference"
