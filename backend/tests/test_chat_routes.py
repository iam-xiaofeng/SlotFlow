"""FastAPI 聊天路由完整后端链路测试。

这组测试把 HTTP 入口、仓库、run 配置、agent adapter 和 SSE 输出串起来：

HTTP 请求
-> 内存仓库
-> run 配置
-> agent adapter
-> SSE frame
-> 仓库最终状态

仍然不调用真实模型。真实 DeepSeek 会放在单独 smoke test 里，避免日常测试受网络影响。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.agent_adapter import AgentEvent
from app.chat.models import ChatStreamRequest, RunConfigBundle
from app.chat.repository import ChatRepository, InMemoryChatRepository, SQLiteChatRepository
from app.harness.sandbox import SlotFlowSandboxConfig
from app.main import create_app
from app.uploads import SlotFlowUploadStore


class BrokenAgentAdapter:
    """测试用 adapter：先流出一段文本，再模拟 agent 崩溃。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": f"{bundle.context.run_id}:assistant",
                "role": "assistant",
                "delta": "出错前的片段",
                "index": 0,
            },
        )
        raise RuntimeError("boom from test adapter")


class CompletedAgentAdapter:
    """测试用 adapter：产出一轮完整成功事件，不进入真实模型。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        message_id = f"{bundle.context.run_id}:assistant"
        answer = f"测试回答：{request.message}"
        if bundle.context.files:
            answer += f"，收到 {len(bundle.context.files)} 个文件"

        yield AgentEvent(
            event="run.prepared",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
                "model_name": bundle.context.model_name,
                "mode": bundle.context.mode,
                "agent_name": bundle.context.agent_name,
            },
        )
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": answer,
                "index": 0,
            },
        )
        yield AgentEvent(
            event="state.snapshot",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
                "messages": [
                    {
                        "id": message_id,
                        "role": "assistant",
                        "content": answer,
                    }
                ],
                "state": {
                    "messages": [
                        {
                            "id": message_id,
                            "role": "assistant",
                            "content": answer,
                        }
                    ],
                    "uploaded_files": [
                        uploaded_file.model_dump(mode="json")
                        for uploaded_file in bundle.context.uploaded_files
                    ],
                },
            },
        )
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


class ReasoningAgentAdapter:
    """测试用 adapter：模拟 DeepSeek thinking mode 的双通道输出。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        _ = request
        message_id = f"{bundle.context.run_id}:assistant"
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "先检索，再交叉验证。",
                "channel": "reasoning",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "最终报告正文。",
                "channel": "content",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


class ShortSnapshotReasoningAgentAdapter:
    """测试用 adapter：模拟 snapshot 只保留最后一段 reasoning。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        _ = request
        message_id = f"{bundle.context.run_id}:assistant"
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "第一段较长的分析。",
                "channel": "reasoning",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "第二段继续推理。",
                "channel": "reasoning",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "最终报告正文。",
                "channel": "content",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="state.snapshot",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
                "messages": [
                    {
                        "id": message_id,
                        "role": "assistant",
                        "content": "最终报告正文。",
                        "reasoning_content": "第二段继续推理。",
                    }
                ],
            },
        )
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


class CompressingAgentAdapter:
    """测试用 adapter：模拟上下文压缩状态，然后正常回答。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        _ = request
        message_id = f"{bundle.context.run_id}:assistant"
        yield AgentEvent(
            event="context.compressing",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": "压缩后继续回答。",
                "channel": "content",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


class ReplacedContentAgentAdapter:
    """测试用 adapter：模拟 state snapshot 替换之前流出的 content。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        _ = request
        message_id = f"{bundle.context.run_id}:assistant"
        transient_content = "这段会被 snapshot 替换。"
        final_answer = "最终报告正文。"
        yield AgentEvent(
            event="message.delta",
            data={
                "message_id": message_id,
                "role": "assistant",
                "delta": transient_content,
                "channel": "content",
                "index": 0,
            },
        )
        yield AgentEvent(
            event="state.snapshot",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
                "messages": [
                    {
                        "id": message_id,
                        "role": "assistant",
                        "content": final_answer,
                    }
                ],
            },
        )
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


class ClarificationAgentAdapter:
    """测试用 adapter：请求用户澄清并结束本轮。"""

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        _ = request
        payload = {
            "type": "clarification",
            "id": "clarification:call_route",
            "question": "你想分析哪个币种？",
            "clarification_type": "ambiguous_requirement",
            "context": "昨天的记忆里有 BTC 和 ETH。",
            "options": [
                {"id": "A", "label": "BTC"},
                {"id": "B", "label": "ETH"},
            ],
            "source": "slotflow_clarification",
            "thread_id": bundle.context.thread_id,
            "run_id": bundle.context.run_id,
        }
        yield AgentEvent(event="clarification.requested", data=payload)
        yield AgentEvent(
            event="run.finished",
            data={
                "thread_id": bundle.context.thread_id,
                "run_id": bundle.context.run_id,
            },
        )


def _client(
    repo: ChatRepository,
    adapter=None,
    upload_store: SlotFlowUploadStore | None = None,
) -> TestClient:
    """创建带测试仓库和测试 adapter 的 TestClient。"""

    return TestClient(
        create_app(
            chat_repo=repo,
            agent_adapter=adapter or CompletedAgentAdapter(),
            upload_store=upload_store,
        )
    )


def test_create_app_can_use_sqlite_repository_from_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """应用启动入口可以通过环境变量切到 SQLite 仓库。"""

    db_path = tmp_path / "chat.sqlite3"
    monkeypatch.setenv("SLOTFLOW_CHAT_REPOSITORY_BACKEND", "sqlite")
    monkeypatch.setenv("SLOTFLOW_CHAT_SQLITE_PATH", str(db_path))

    app = create_app(agent_adapter=CompletedAgentAdapter())
    client = TestClient(app)
    repo = app.state.chat_repo
    persisted_repo: SQLiteChatRepository | None = None

    try:
        response = client.post("/api/chat/threads", json={"title": "SQLite 会话"})
        persisted_repo = SQLiteChatRepository(db_path)

        assert isinstance(repo, SQLiteChatRepository)
        assert response.status_code == 200
        assert persisted_repo.list_threads()[0].title == "SQLite 会话"
    finally:
        if persisted_repo is not None:
            persisted_repo.close()
        if isinstance(repo, SQLiteChatRepository):
            repo.close()


def _parse_sse(text: str) -> list[dict]:
    """把 TestClient 收到的 SSE 文本解析成事件列表。"""

    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        item: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                item["event"] = line.removeprefix("event: ")
            if line.startswith("data: "):
                item["data"] = json.loads(line.removeprefix("data: "))
        if item:
            events.append(item)
    return events


def test_thread_routes_create_list_get_and_list_messages() -> None:
    """thread 的基础 HTTP 接口应该能创建、读取、列出消息。"""

    repo = InMemoryChatRepository()
    client = _client(repo)

    create_response = client.post("/api/chat/threads", json={"title": "  测试会话  "})
    thread = create_response.json()

    assert create_response.status_code == 200
    assert thread["title"] == "测试会话"

    list_response = client.get("/api/chat/threads")
    get_response = client.get(f"/api/chat/threads/{thread['id']}")
    messages_response = client.get(f"/api/chat/threads/{thread['id']}/messages")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [thread["id"]]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == thread["id"]
    assert messages_response.status_code == 200
    assert messages_response.json() == []

    delete_response = client.delete(f"/api/chat/threads/{thread['id']}")

    assert delete_response.status_code == 204
    assert client.get(f"/api/chat/threads/{thread['id']}").status_code == 404


def test_search_threads_matches_stored_messages() -> None:
    repo = InMemoryChatRepository()
    client = _client(repo)
    thread = client.post("/api/chat/threads", json={"title": "普通标题"}).json()
    repo.add_message(thread["id"], role="user", content="LangChain middleware 怎么配置")
    repo.add_message(thread["id"], role="assistant", content="可以在模型调用前后处理消息")

    response = client.get("/api/chat/search", params={"q": "模型调用前后"})

    assert response.status_code == 200
    results = response.json()
    assert results[0]["thread"]["id"] == thread["id"]
    assert results[0]["match_type"] == "message"
    assert results[0]["message"]["role"] == "assistant"
    assert "模型调用前后" in results[0]["snippet"]


def test_missing_thread_routes_return_404() -> None:
    """不存在的 thread 要返回 404，而不是在仓库里悄悄创建。"""

    repo = InMemoryChatRepository()
    client = _client(repo)

    assert client.get("/api/chat/threads/thread_missing").status_code == 404
    assert client.get("/api/chat/threads/thread_missing/messages").status_code == 404
    assert (
        client.post(
            "/api/chat/threads/thread_missing/runs/stream",
            json={"message": "hello"},
        ).status_code
        == 404
    )


def test_stream_run_emits_sse_and_persists_messages_and_completed_run(
    tmp_path: Path,
) -> None:
    """前端发送 stream 请求后，后端应该返回 SSE，并保存用户和 assistant 消息。"""

    repo = InMemoryChatRepository()
    upload_store = SlotFlowUploadStore(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )
    client = _client(repo, upload_store=upload_store)
    thread = client.post("/api/chat/threads", json={"title": "链路测试"}).json()
    uploaded = client.post(
        "/api/uploads",
        files={"file": ("report.md", b"# report", "text/markdown")},
    ).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={
            "message": "解释完整链路",
            "model_name": "deepseek-v4-pro",
            "mode": "pro",
            "files": [uploaded["id"]],
        },
    )
    events = _parse_sse(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events[0]["event"] == "run.prepared"
    assert "message.delta" in [event["event"] for event in events]
    assert events[-2]["event"] == "state.snapshot"
    assert events[-1]["event"] == "run.finished"

    messages = repo.list_messages(thread["id"])
    runs = repo.list_runs(thread["id"])

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "解释完整链路"
    assert messages[0].metadata["files"] == [uploaded["id"]]
    assert messages[0].metadata["request_metadata"] == {}
    assert messages[0].metadata["uploaded_files"][0]["id"] == uploaded["id"]
    assert messages[0].metadata["uploaded_files"][0]["workspace_path"] == (
        f"uploads/{runs[0].id}/report.md"
    )
    assert upload_store.workspace.resolve_path(
        messages[0].metadata["uploaded_files"][0]["workspace_path"]
    ).read_text(encoding="utf-8") == "# report"
    assert "解释完整链路" in messages[1].content
    assert messages[1].run_id == runs[0].id
    assert messages[1].metadata["thinking_enabled"] is True
    assert runs[0].status == "completed"
    assert runs[0].model_name == "deepseek-v4-pro"
    assert runs[0].mode == "pro"
    assert repo.get_thread(thread["id"]).title == "解释完整链路"


def test_stream_run_persists_reasoning_content_in_assistant_metadata() -> None:
    repo = InMemoryChatRepository()
    client = _client(repo, adapter=ReasoningAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "思考链路"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "需要深度分析"},
    )
    events = _parse_sse(response.text)
    messages = repo.list_messages(thread["id"])

    assert response.status_code == 200
    assert events[0]["data"]["channel"] == "reasoning"
    assert events[1]["data"]["channel"] == "content"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content == "最终报告正文。"
    assert messages[1].metadata == {
        "source": "agent",
        "thinking_enabled": True,
        "reasoning_content": "先检索，再交叉验证。",
    }


def test_stream_run_keeps_longer_streamed_reasoning_over_short_snapshot() -> None:
    repo = InMemoryChatRepository()
    client = _client(repo, adapter=ShortSnapshotReasoningAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "思考快照"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "需要深度分析"},
    )
    messages = repo.list_messages(thread["id"])

    assert response.status_code == 200
    assert messages[1].content == "最终报告正文。"
    assert messages[1].metadata["reasoning_content"] == (
        "第一段较长的分析。第二段继续推理。"
    )


def test_stream_run_exposes_context_compressing_without_persisting_it() -> None:
    repo = InMemoryChatRepository()
    client = _client(repo, adapter=CompressingAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "压缩链路"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "长上下文继续"},
    )
    events = _parse_sse(response.text)
    messages = repo.list_messages(thread["id"])

    assert response.status_code == 200
    assert [event["event"] for event in events] == [
        "context.compressing",
        "message.delta",
        "run.finished",
    ]
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content == "压缩后继续回答。"
    assert "compressing" not in messages[1].metadata


def test_stream_run_does_not_guess_replaced_content_delta_as_reasoning_metadata() -> None:
    repo = InMemoryChatRepository()
    client = _client(repo, adapter=ReplacedContentAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "替换正文"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "需要深度分析"},
    )
    messages = repo.list_messages(thread["id"])

    assert response.status_code == 200
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content == "最终报告正文。"
    assert messages[1].metadata == {
        "source": "agent",
        "thinking_enabled": True,
    }


def test_stream_run_can_reuse_user_message_for_edit_or_retry() -> None:
    """编辑/重试最后一轮时，应覆盖 user message 并替换它后面的 assistant。"""

    repo = InMemoryChatRepository()
    client = _client(repo)
    thread = client.post("/api/chat/threads", json={"title": "编辑测试"}).json()

    first_response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "旧问题"},
    )
    original_user = repo.list_messages(thread["id"])[0]

    second_response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={
            "message": "新问题",
            "reuse_user_message_id": original_user.id,
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    messages = repo.list_messages(thread["id"])
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].id == original_user.id
    assert messages[0].content == "新问题"
    assert "新问题" in messages[1].content
    assert "旧问题" not in messages[1].content


def test_stream_run_rejects_unknown_uploaded_file_without_persisting(
    tmp_path: Path,
) -> None:
    """stream 请求带不存在的 file_id 时，要 404，且不能创建消息或 run。"""

    repo = InMemoryChatRepository()
    upload_store = SlotFlowUploadStore(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )
    client = _client(repo, upload_store=upload_store)
    thread = client.post("/api/chat/threads", json={"title": "缺失文件"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={
            "message": "分析缺失文件",
            "files": ["file_missing"],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "upload not found"
    assert repo.list_messages(thread["id"]) == []
    assert repo.list_runs(thread["id"]) == []


def test_stream_run_error_event_marks_run_failed() -> None:
    """agent adapter 崩溃时，SSE 要返回 run.error，仓库里的 run 要变成 failed。"""

    repo = InMemoryChatRepository()
    client = _client(repo, adapter=BrokenAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "失败链路"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "触发错误"},
    )
    events = _parse_sse(response.text)
    runs = repo.list_runs(thread["id"])
    messages = repo.list_messages(thread["id"])

    assert response.status_code == 200
    assert [event["event"] for event in events] == ["message.delta", "run.error"]
    assert events[-1]["data"] == {
        "name": "RuntimeError",
        "message": "boom from test adapter",
    }
    assert runs[0].status == "failed"
    assert runs[0].error == "boom from test adapter"
    assert [message.role for message in messages] == ["user"]


def test_stream_run_persists_clarification_request() -> None:
    """clarification.requested 要保存成可重载的 assistant 消息。"""

    repo = InMemoryChatRepository()
    client = _client(repo, adapter=ClarificationAgentAdapter())
    thread = client.post("/api/chat/threads", json={"title": "澄清链路"}).json()

    response = client.post(
        f"/api/chat/threads/{thread['id']}/runs/stream",
        json={"message": "分析我昨天说的虚拟货币"},
    )
    events = _parse_sse(response.text)
    messages = repo.list_messages(thread["id"])
    runs = repo.list_runs(thread["id"])

    assert response.status_code == 200
    assert [event["event"] for event in events] == [
        "clarification.requested",
        "run.finished",
    ]
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content.startswith("昨天的记忆里有 BTC 和 ETH。")
    assert messages[1].metadata["source"] == "clarification"
    assert messages[1].metadata["clarification"]["options"][0] == {
        "id": "A",
        "label": "BTC",
    }
    assert runs[0].status == "completed"
