"""把 `chat.sqlite3` 里的真实会话导出成**前端原样可读**的静态 API 目录。

思路:不改前端一行代码,而是把它请求的那些 GET 端点,在静态站点里生成成**同路径的 JSON 文件**。
`fetch("/api/chat/threads")` 拿到的是一个真实存在的文件,`Response.json()` 照常解析——
于是 `next build` 导出的**真前端**在没有后端的情况下也能把历史会话渲染出来。

数据源刻意选 `chat.sqlite3` 而不是中间格式:**它就是产品自己会返回的东西**,
连 `metadata.tool_activities` / `metadata.todos` / `metadata.clarification` 都原样带过去,
所以工具时间线、todo 面板、澄清卡片在静态站点里是**真组件**渲染的,不是仿的。

生成的目录树(和后端路由逐字对应)::

    api/chat/models · threads · threads/<id>/messages · threads/<id>/context-usage
    api/workspace/artifacts · threads
    api/workspace/artifacts/read/<slug>   ← 原接口是 ?path=,由 demo/serve.py 转成 slug
    api/workspace/artifacts/raw/<slug>

用法::

    cd backend
    uv run python -m evals.export_demo_api --thread thread_xxx
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_ROOT = _BACKEND.parent
DEFAULT_OUT = _ROOT / "demo" / "api"
CHAT_DB = _BACKEND / ".slotflow" / "chat.sqlite3"
WORKSPACE = _BACKEND / ".slotflow" / "workspace"


def path_slug(path: str) -> str:
    """`a/b/c.md` → `a__b__c.md`。

    原接口是 `?path=a/b/c.md`,而静态文件服务器路由不了 query string。
    转成一段确定性的 slug 当目录名,由 `demo/serve.py` 在收到 `?path=` 时做同样的转换。
    """

    return path.strip("/").replace("/", "__")


def write_json(path: Path, payload: Any) -> None:
    """每个端点写成 `<路径>/index.json`。

    统一成目录 + index.json 是因为路径天然冲突:`/api/chat/threads` 既要是会话列表,
    又是 `/api/chat/threads/<id>/messages` 的父目录——文件系统里一个名字不能既是文件又是目录。
    """

    target = path / "index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_thread(db: Path, thread_id: str) -> tuple[dict, list[dict]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = list(
            conn.execute(
                "select id,title,created_at,updated_at from threads where id=?", (thread_id,)
            )
        )
        if not rows:
            raise SystemExit(f"聊天库里没有会话 {thread_id}")
        tid, title, created, updated = rows[0]
        thread = {"id": tid, "title": title, "created_at": created, "updated_at": updated}
        messages = [
            {
                "id": mid,
                "thread_id": tid,
                "role": role,
                "content": content,
                "run_id": run_id,
                "metadata": json.loads(metadata or "{}"),
                "created_at": ts,
            }
            for mid, role, content, run_id, metadata, ts in conn.execute(
                "select id,role,content,run_id,metadata_json,created_at "
                "from messages where thread_id=? order by sequence",
                (thread_id,),
            )
        ]
        return thread, messages
    finally:
        conn.close()


def thread_artifacts(thread_id: str) -> list[dict[str, Any]]:
    root = WORKSPACE / thread_id / "artifacts"
    if not root.is_dir():
        return []
    return [
        {
            "path": f"{thread_id}/artifacts/{item.relative_to(root).as_posix()}",
            "kind": "file",
            "size_bytes": item.stat().st_size,
            "_source": item,
        }
        for item in sorted(root.rglob("*"))
        if item.is_file()
    ]


_TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".html", ".css", ".js", ".ts", ".csv", ".yaml", ".yml"}


def export_artifacts(api: Path, thread_id: str, artifacts: list[dict[str, Any]]) -> None:
    listing = [{k: v for k, v in a.items() if not k.startswith("_")} for a in artifacts]
    write_json(api / "workspace" / "artifacts", listing)
    write_json(
        api / "workspace" / "threads",
        [
            {
                "thread_id": thread_id,
                "title": "",
                "generated": listing,
                "uploads": [],
            }
        ]
        if listing
        else [],
    )
    for artifact in artifacts:
        source: Path = artifact["_source"]
        rel = artifact["path"]
        slug = path_slug(rel)
        suffix = source.suffix.lower()
        is_text = suffix in _TEXT_SUFFIXES
        payload: dict[str, Any] = {
            "path": rel,
            "kind": "text" if is_text else "binary",
            "media_type": "text/markdown" if suffix == ".md" else "text/plain",
            "size_bytes": artifact["size_bytes"],
            "source": "slotflow_workspace",
            "metadata": {"format": suffix.lstrip(".")},
        }
        if is_text:
            payload["content"] = source.read_text(encoding="utf-8", errors="replace")
        write_json(api / "workspace" / "artifacts" / "read" / slug, payload)
        raw_dir = api / "workspace" / "artifacts" / "raw" / slug
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, raw_dir / "index.json")  # serve.py 按原样回内容


def export(out_dir: Path, thread_id: str, db: Path) -> None:
    thread, messages = load_thread(db, thread_id)
    model = "deepseek-v4-pro"
    for message in messages:
        name = (message["metadata"] or {}).get("model_name")
        if isinstance(name, str) and name:
            model = name
            break

    api = out_dir
    write_json(api / "chat" / "threads", [thread])
    write_json(api / "chat" / "threads" / thread_id / "messages", messages)
    write_json(
        api / "chat" / "threads" / thread_id / "context-usage",
        {
            "thread_id": thread_id,
            "run_id": None,
            "context_tokens": None,
            "context_cached_tokens": None,
            "context_window_tokens": None,
            "context_input_budget_tokens": None,
            "context_window_source": None,
        },
    )
    write_json(
        api / "chat" / "models",
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

    artifacts = thread_artifacts(thread_id)
    export_artifacts(api, thread_id, artifacts)

    # 环境类读口:展示页不暴露本机的技能 / MCP / 记忆。
    for rel in ("chat/skills", "chat/mcp/servers", "chat/memories"):
        write_json(api / rel, [])

    print(
        f"已导出会话 {thread_id}:{len(messages)} 条消息、{len(artifacts)} 个产物 → {out_dir}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", required=True, help="要导出的 thread id")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--db", default=str(CHAT_DB))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    export(out, args.thread, Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
