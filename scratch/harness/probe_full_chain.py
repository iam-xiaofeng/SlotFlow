"""SlotFlow 全链路真实模型探针(审计批次7)。

模拟前端的每一步:建线程 → SSE 流式 run → 逐事件校验 → 落库核对 → 多轮 →
标题生成 → 显式记忆(LLM rewrite) → artifact 工具链 → 澄清门。
对真实后端(uvicorn+真实 DeepSeek/自定义网关模型)运行:

    cd backend && uv run uvicorn app.main:app --env-file ./.env --port 8010 &
    uv run python ../scratch/harness/probe_full_chain.py --base http://127.0.0.1:8010

每个检查输出 PASS/FAIL 与证据;任何 FAIL 都会使退出码非零。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx

ALLOWED_EVENTS = {
    "run.prepared",
    "context.compressing",
    "message.delta",
    "tool.delta",
    "tool.status",
    "clarification.requested",
    "todo.updated",
    "state.snapshot",
    "run.finished",
    "run.error",
}

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, evidence: str = "") -> None:
    RESULTS.append((ok, name, evidence))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" | {evidence}" if evidence else ""), flush=True)


async def sse_run(
    client: httpx.AsyncClient,
    thread_id: str,
    message: str,
    *,
    mode: str = "pro",
    model_name: str,
    thinking: bool = True,
    timeout: float = 300.0,
) -> list[dict[str, Any]]:
    """POST runs/stream 并解析全部 SSE 帧为 [{event, data}]。"""

    events: list[dict[str, Any]] = []
    body = {
        "message": message,
        "model_name": model_name,
        "mode": mode,
        "thinking_enabled": thinking,
        "agent_name": "default",
        "files": [],
        "metadata": {"source": "probe"},
    }
    async with client.stream(
        "POST",
        f"/api/chat/threads/{thread_id}/runs/stream",
        json=body,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        current_event: str | None = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line[len("event: "):].strip()
            elif line.startswith("data: ") and current_event:
                try:
                    data = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    data = {"_raw": line}
                events.append({"event": current_event, "data": data})
            elif not line.strip():
                current_event = None
    return events


def event_names(events: list[dict[str, Any]]) -> list[str]:
    return [item["event"] for item in events]


def streamed_content(events: list[dict[str, Any]]) -> str:
    return "".join(
        item["data"].get("delta", "")
        for item in events
        if item["event"] == "message.delta" and item["data"].get("channel") != "reasoning"
        and isinstance(item["data"].get("delta"), str)
    )


def streamed_reasoning(events: list[dict[str, Any]]) -> str:
    return "".join(
        item["data"].get("delta", "")
        for item in events
        if item["event"] == "message.delta" and item["data"].get("channel") == "reasoning"
        and isinstance(item["data"].get("delta"), str)
    )


def validate_common_stream(tag: str, events: list[dict[str, Any]]) -> None:
    names = event_names(events)
    unknown = sorted(set(names) - ALLOWED_EVENTS)
    check(not unknown, f"{tag}: 事件名全部在契约内", f"unknown={unknown}" if unknown else f"{len(names)} events")
    check(names.count("run.finished") == 1, f"{tag}: 恰好一次 run.finished", f"count={names.count('run.finished')}")
    check("run.error" not in names, f"{tag}: 无 run.error", "")
    if "run.finished" in names:
        after = names[names.index("run.finished") + 1 :]
        check(not after, f"{tag}: run.finished 之后无其他事件", f"after={after}")
    prepared_positions = [i for i, n in enumerate(names) if n == "run.prepared"]
    check(
        len(prepared_positions) == 1 and prepared_positions[0] == 0,
        f"{tag}: run.prepared 恰好一次且在最前",
        f"positions={prepared_positions}",
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--skip-clarify", action="store_true")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=60.0) as client:
        # 0) 服务与模型目录
        health = await client.get("/health")
        check(health.status_code == 200, "GET /health 可用", str(health.status_code))
        models = (await client.get("/api/chat/models")).json()
        available = [
            m
            for provider in models.get("providers", [])
            for m in provider.get("models", [])
            if m.get("available")
        ]
        check(bool(available), "模型目录非空", str([m.get("id") for m in available])[:160])
        default_model = models.get("default_model")
        model_name = default_model if any(m.get("id") == default_model for m in available) else (
            available[0]["id"] if available else None
        )
        check(model_name is not None, "取到可用模型名", str(model_name))
        if model_name is None:
            return finish()

        # 1) 建线程(前端第一步)
        thread = (await client.post("/api/chat/threads", json={"title": "probe 全链路"})).json()
        thread_id = thread["id"]
        check(bool(thread_id), "POST /threads 建线程", thread_id)

        # 2) 第一轮:普通问答,校验事件序列/流式与落库一致性
        t0 = time.time()
        events = await sse_run(
            client, thread_id, "用一句话介绍你自己,不要使用任何工具。", model_name=model_name
        )
        validate_common_stream("轮1", events)
        content = streamed_content(events)
        check(bool(content.strip()), "轮1: 流式正文非空", content[:80])
        reasoning = streamed_reasoning(events)
        print(f"      轮1耗时 {time.time()-t0:.1f}s | 正文{len(content)}字 | 思考{len(reasoning)}字")

        messages = (await client.get(f"/api/chat/threads/{thread_id}/messages")).json()
        roles = [m["role"] for m in messages]
        check(roles == ["user", "assistant"], "轮1: 落库 user+assistant 各一条", str(roles))
        persisted = next((m for m in messages if m["role"] == "assistant"), {})
        p_content = (persisted.get("content") or "").strip()
        s_content = content.strip()
        consistent = bool(p_content) and (
            p_content.startswith(s_content) or s_content.startswith(p_content) or p_content == s_content
        )
        check(consistent, "轮1: 落库正文与流式正文一致(前缀关系)", f"persisted={len(p_content)} streamed={len(s_content)}")
        # 思考内容不应混进正文
        check(
            "<think>" not in p_content and "reasoning_content" not in p_content,
            "轮1: 思考内容未泄漏进正文",
            "",
        )

        # 3) 标题生成(首轮后)
        threads = (await client.get("/api/chat/threads")).json()
        this_thread = next((t for t in threads if t["id"] == thread_id), {})
        check(bool(this_thread.get("title")), "轮1后: 线程标题存在", str(this_thread.get("title"))[:60])

        # 4) 第二轮:多轮状态(checkpointer)
        events2 = await sse_run(
            client, thread_id, "我上一句话让你做什么?请引用我的原话。", model_name=model_name
        )
        validate_common_stream("轮2", events2)
        content2 = streamed_content(events2)
        check("介绍" in content2 or "一句话" in content2, "轮2: 模型能看见上一轮(多轮状态)", content2[:100])

        # 5) 显式记忆(LLM rewrite 链路)
        mem_thread = (await client.post("/api/chat/threads", json={"title": "probe 记忆"})).json()
        mem_before = len((await client.get("/api/memory")).json())
        events3 = await sse_run(
            client,
            mem_thread["id"],
            "请记住:我喜欢简洁的中文回答。",
            model_name=model_name,
        )
        validate_common_stream("记忆轮", events3)
        await asyncio.sleep(2)
        memories = (await client.get("/api/memory")).json()
        new_memories = memories[: max(0, len(memories) - mem_before)] if len(memories) > mem_before else []
        check(len(memories) > mem_before, "显式记忆已保存", json.dumps([m.get("content") for m in memories[:3]], ensure_ascii=False))
        if new_memories:
            top = new_memories[0]
            check(
                "简洁" in (top.get("content") or ""),
                "记忆内容语义正确",
                f"content={top.get('content')} extraction={ (top.get('metadata') or {}).get('extraction') }",
            )

        # 6) artifact 工具链
        art_thread = (await client.post("/api/chat/threads", json={"title": "probe 产物"})).json()
        events4 = await sse_run(
            client,
            art_thread["id"],
            "请用 artifact_write 工具创建文件 hello.html,内容是一个居中的<h1>SlotFlow 测试页</h1>,写完用一句话确认即可。",
            model_name=model_name,
            timeout=420.0,
        )
        validate_common_stream("产物轮", events4)
        tool_events = [e for e in events4 if e["event"] == "tool.status"]
        check(bool(tool_events), "产物轮: 出现 tool.status 事件", str([e["data"].get("tool_name") or e["data"].get("toolName") for e in tool_events])[:120])
        listing = (await client.get("/api/workspace/artifacts")).json()
        # 列表接口是逐层目录:根层是各 thread 目录,需下钻一层找文件(前端同样逐层浏览)。
        files = [f for f in listing if f.get("kind") == "file"]
        for entry in listing:
            if entry.get("kind") == "directory":
                children = (
                    await client.get("/api/workspace/artifacts", params={"path": entry["path"]})
                ).json()
                files.extend(c for c in children if c.get("kind") == "file")
        html_files = [f for f in files if f.get("path", "").endswith(".html")]
        check(bool(html_files), "产物轮: workspace 列表出现 html 文件", str([f.get("path") for f in html_files])[:160])
        if html_files:
            read = await client.get("/api/workspace/artifacts/read", params={"path": html_files[0]["path"]})
            ok_read = read.status_code == 200 and "SlotFlow" in (read.json().get("content") or "")
            check(ok_read, "产物轮: 文件可读且内容正确", str(read.status_code))

        # 7) 澄清门(ultra 模式、短且欠指定的创作请求)
        if not args.skip_clarify:
            cl_thread = (await client.post("/api/chat/threads", json={"title": "probe 澄清"})).json()
            events5 = await sse_run(
                client, cl_thread["id"], "帮我写个东西", model_name=model_name, mode="ultra"
            )
            names5 = event_names(events5)
            clarified = "clarification.requested" in names5
            check(clarified, "澄清轮: 触发 clarification.requested", str(names5))
            if clarified:
                cl_event = next(e for e in events5 if e["event"] == "clarification.requested")
                opts = cl_event["data"].get("options") or []
                check(bool(opts), "澄清轮: 携带选项", json.dumps(opts, ensure_ascii=False)[:160])
                # 前端行为:把选项 label 作为新消息发回同线程
                answer = (opts[0].get("label") if opts and isinstance(opts[0], dict) else None) or "写一首关于秋天的短诗"
                events6 = await sse_run(
                    client, cl_thread["id"], answer, model_name=model_name, mode="ultra", timeout=420.0
                )
                names6 = event_names(events6)
                check("run.finished" in names6 and "run.error" not in names6, "澄清轮: 选择后 run 正常完成", str(names6[:8]))
                check(bool(streamed_content(events6).strip()) or "clarification.requested" in names6, "澄清轮: 选择后有产出", streamed_content(events6)[:80])

    return finish()


def finish() -> int:
    failed = [r for r in RESULTS if not r[0]]
    print("\n===== 探针汇总 =====")
    print(f"共 {len(RESULTS)} 项,失败 {len(failed)} 项")
    for ok, name, evidence in failed:
        print(f"  FAIL: {name} | {evidence}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
