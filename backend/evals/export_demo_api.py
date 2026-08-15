"""把真实对话导出成**前端原样可读**的静态 API 目录。

思路:不改前端一行代码,而是把它请求的那几个 GET 端点,在静态站点里生成成**同路径的 JSON 文件**。
`fetch("/api/chat/threads")` 拿到的是一个真实存在的文件,`Response.json()` 照常解析——
于是 `next build` 导出的**真前端**在没有后端的情况下也能把历史会话渲染出来。

生成的目录树(和后端路由逐字对应)::

    api/chat/models
    api/chat/threads
    api/chat/threads/<id>/messages
    api/chat/threads/<id>/context-usage
    api/chat/artifacts · skills · mcp/servers · memories   (空列表)

**一个必须写下来的取舍**:后端只持久化 user / assistant 消息(见 `chat/routes.py`),
工具消息不落库。所以真前端重新打开任何一个历史会话——不管是不是 demo——本来就只显示
正文 + 思考框 + 澄清卡片,**没有工具时间线**(它只在流式那一刻由 `tool.status` 事件驱动)。
这里如实保持这个行为,而不是伪造一份后端不会返回的数据。

用法::

    cd backend
    uv run python -m evals.export_demo_api            # 读 demo/transcripts.js,写 demo/api/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_ROOT = _BACKEND.parent
TRANSCRIPTS = _ROOT / "demo" / "transcripts.js"
DEFAULT_OUT = _ROOT / "demo" / "api"

# 会话创建时间:靠前的场景显示得更"新",让侧栏顺序 = SCENARIOS 顺序。
_BASE_TS = "2026-08-15T12:00:00+00:00"


def load_transcripts() -> dict[str, Any]:
    raw = TRANSCRIPTS.read_text(encoding="utf-8")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise SystemExit(f"{TRANSCRIPTS} 里没有找到 JSON 负载")
    return json.loads(raw[start : end + 1])


def _timestamp(position: int) -> str:
    """按位置递减,侧栏(按时间倒序)就会保持 SCENARIOS 的顺序。"""

    from datetime import datetime, timedelta

    base = datetime.fromisoformat(_BASE_TS)
    return (base - timedelta(minutes=position)).isoformat()


def build_records(payload: dict[str, Any]) -> tuple[list[dict], dict[str, list[dict]]]:
    threads: list[dict[str, Any]] = []
    messages_by_thread: dict[str, list[dict[str, Any]]] = {}

    for position, thread in enumerate(payload.get("threads", [])):
        thread_id = thread["id"]
        created = _timestamp(position)
        threads.append(
            {
                "id": thread_id,
                "title": thread["title"],
                "created_at": created,
                "updated_at": created,
            }
        )

        records: list[dict[str, Any]] = []
        for index, message in enumerate(thread.get("messages", [])):
            role = message.get("role")
            if role == "tool":
                # 后端不落库工具消息,这里也不造。见模块注释。
                continue
            metadata: dict[str, Any] = {"source": "agent" if role == "assistant" else "chat-ui"}
            if role == "assistant":
                if message.get("reasoning"):
                    metadata["reasoning_content"] = message["reasoning"]
                    metadata["thinking_enabled"] = True
                clarification = message.get("clarification")
                if clarification:
                    # 前端按 metadata.clarification 渲染澄清卡片(见 message-list-parts.tsx)。
                    metadata["source"] = "clarification"
                    metadata["clarification"] = {
                        "type": "clarification",
                        "id": f"{thread_id}-c{index}",
                        "question": clarification.get("question", ""),
                        "clarification_type": "choice",
                        "context": clarification.get("context") or None,
                        "options": clarification.get("options", []),
                        "source": "slotflow_clarification",
                        "thread_id": thread_id,
                        "run_id": None,
                    }
            content = message.get("content") or ""
            if role == "assistant" and not content and metadata.get("clarification"):
                content = metadata["clarification"]["question"]
            if not content and role == "assistant" and not metadata.get("reasoning_content"):
                continue  # 纯工具调用那一步没有正文,前端也没有东西可显示
            records.append(
                {
                    "id": f"{thread_id}-m{index}",
                    "thread_id": thread_id,
                    "role": role,
                    "content": content,
                    "run_id": f"{thread_id}-run",
                    "metadata": metadata,
                    "created_at": created,
                }
            )
        messages_by_thread[thread_id] = records

    return threads, messages_by_thread


def write_json(path: Path, payload: Any) -> None:
    """每个端点写成 `<路径>/index.json`。

    统一成目录 + index.json 是因为路径天然冲突:`/api/chat/threads` 既要是会话列表,
    又是 `/api/chat/threads/<id>/messages` 的父目录——文件系统里一个名字不能既是文件又是目录。
    于是约定「每个端点都是目录」,由静态服务器把 `<path>` 解析到 `<path>/index.json`
    (见 demo/serve.py,以及 Cloudflare Pages 用的 _redirects)。
    """

    target = path / "index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def export(out_dir: Path, payload: dict[str, Any]) -> None:
    threads, messages_by_thread = build_records(payload)
    model = payload.get("model") or "demo"

    api = out_dir / "chat"
    write_json(api / "threads", threads)
    write_json(
        api / "models",
        {
            "default_model": model,
            "providers": [
                {
                    "provider": "custom",
                    "configured": True,
                    "base_url": None,
                    "status": "available",
                    "message": None,
                    "models": [
                        {
                            "id": model,
                            "provider": "custom",
                            "label": model,
                            "available": True,
                            "source": "catalog",
                        }
                    ],
                }
            ],
        },
    )
    for thread in threads:
        thread_id = thread["id"]
        write_json(api / "threads" / thread_id / "messages", messages_by_thread[thread_id])
        write_json(
            api / "threads" / thread_id / "context-usage",
            {
                "thread_id": thread_id,
                "run_id": f"{thread_id}-run",
                "context_tokens": None,
                "context_cached_tokens": None,
                "context_window_tokens": None,
                "context_input_budget_tokens": None,
                "context_window_source": None,
            },
        )

    # 环境类读口:展示页不暴露本机的技能 / MCP / 记忆 / 产物。
    for rel in ("artifacts", "skills", "mcp/servers", "memories"):
        write_json(api / rel, [])

    # Cloudflare Pages:把无扩展名的 API 路径解析到同名目录下的 index.json。
    (out_dir.parent / "_redirects").write_text(
        "/api/*  /api/:splat/index.json  200\n", encoding="utf-8"
    )

    total = sum(len(v) for v in messages_by_thread.values())
    print(f"已导出 {len(threads)} 个会话 / {total} 条消息 → {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录(默认 demo/api)")
    args = parser.parse_args()
    export(Path(args.out).resolve(), load_transcripts())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
