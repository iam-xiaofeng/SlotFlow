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
import posixpath
import re
import shutil
import sqlite3
import sys
from pathlib import Path, PurePosixPath
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_ROOT = _BACKEND.parent
DEFAULT_OUT = _ROOT / "demo" / "api"
CHAT_DB = _BACKEND / ".slotflow" / "chat.sqlite3"
WORKSPACE = _BACKEND / ".slotflow" / "workspace"

# 产物里这些目录是构建/依赖噪音,不是模型的交付物。
# 真实案例:一次 React 重构会往 artifacts/ 里装出 2600+ 个 node_modules 文件、87 MB——
# 全导出去的话产物面板会被淹掉,静态站点也会膨胀成一堆碎文件。
EXCLUDED_ARTIFACT_DIRS = {"node_modules", ".git", ".vite", "__pycache__", ".cache"}

# 产物按**原目录结构**另外镜像一份到站点根的这个路径下。
# 为什么需要:`dist/index.html` 里是 `./assets/index-xxx.js` 这种相对引用,而产物读取接口是
# `?path=` 形式——相对 URL 解析会丢掉 query string,浏览器就会去敲一个并不存在的路径。
# 镜像成真实目录后,HTML 里的引用改写成绝对路径,JS 里的动态 import(Vite 按模块 URL 解析)
# 也能顺着同一棵目录树自动找到,不用改产品代码。
# 放站点根而不是 `/api/` 下:`demo/_redirects` 把 `/api/*` 整个改写到 `.../index.json`。
ASSET_MOUNT = "/artifact-assets"


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


def resolve_thread_id(db: Path, requested: str) -> str:
    """`latest` = 聊天库里最新那条会话。

    这样"跑完一段真实对话 → 换进展示站点"就不需要手抄 thread id。
    """

    if requested != "latest":
        return requested
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = list(
            conn.execute(
                "select t.id from threads t "
                "join messages m on m.thread_id = t.id "
                "group by t.id order by max(m.sequence) desc limit 1"
            )
        )
    finally:
        conn.close()
    if not rows:
        raise SystemExit("聊天库里还没有任何会话")
    return rows[0][0]


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
        and not EXCLUDED_ARTIFACT_DIRS.intersection(item.relative_to(root).parts)
    ]


_TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".html", ".css", ".js", ".ts", ".csv", ".yaml", ".yml"}

# `src="..."` / `href="..."`,单双引号都吃。
_HTML_REF = re.compile(r"""(?P<head>\b(?:src|href)\s*=\s*)(?P<q>["'])(?P<url>[^"']+)(?P=q)""")
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "blob:", "#", "mailto:", "javascript:")


def resolve_local_ref(url: str, html_path: str, known: set[str]) -> str | None:
    """把 HTML 里的一个引用解析成产物路径;不是站内产物就返回 None。

    先按 HTML 自己所在目录解析(`./assets/x.js`、`../y.css`);解析不中且是 `/` 开头的,
    再逐级往上拿祖先目录当根试一遍——Vite 源码入口里的 `/src/main.tsx` 就是这种写法。
    """

    base = PurePosixPath(html_path).parent
    candidates = [posixpath.normpath(f"{base}/{url}")]
    if url.startswith("/"):
        stripped = url.lstrip("/")
        ancestor = base
        while str(ancestor) not in (".", "/"):
            candidates.append(posixpath.normpath(f"{ancestor}/{stripped}"))
            ancestor = ancestor.parent
    for candidate in candidates:
        if candidate in known:
            return f"{ASSET_MOUNT}/{candidate}"
    return None


def rewrite_html_refs(content: str, html_path: str, known: set[str]) -> str:
    """把 HTML 里指向同批产物的相对引用改写成站点绝对路径。

    产物面板预览时会注入 `<base href="/api/workspace/artifacts/raw?path=...">`,而相对 URL
    解析**会丢掉 query string**——`./assets/a.js` 于是变成 `/api/workspace/artifacts/a.js`,404。
    改成绝对路径后 `<base>` 就影响不到它了。
    """

    def replace(match: re.Match[str]) -> str:
        url = match.group("url").strip()
        if not url or url.startswith(_EXTERNAL_PREFIXES):
            return match.group(0)
        resolved = resolve_local_ref(url, html_path, known)
        if resolved is None:
            return match.group(0)
        return f"{match.group('head')}{match.group('q')}{resolved}{match.group('q')}"

    return _HTML_REF.sub(replace, content)


def mirror_artifacts(mirror_root: Path, artifacts: list[dict[str, Any]]) -> None:
    """按**原目录结构**把产物再落一份真实文件。

    这样 `dist/assets/index-xxx.js` 里的动态 import(Vite 按模块 URL 解析相对路径)能顺着
    同一棵树自己找到兄弟 chunk,不用逐个改写 JS。
    """

    for artifact in artifacts:
        target = mirror_root / artifact["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact["_source"], target)


def export_artifacts(
    api: Path, mirror_root: Path, thread_id: str, artifacts: list[dict[str, Any]]
) -> None:
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
    known = {a["path"] for a in artifacts}
    mirror_artifacts(mirror_root, artifacts)
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
        text: str | None = None
        if is_text:
            text = source.read_text(encoding="utf-8", errors="replace")
            if suffix == ".html":
                text = rewrite_html_refs(text, rel, known)
            payload["content"] = text
        write_json(api / "workspace" / "artifacts" / "read" / slug, payload)
        raw_dir = api / "workspace" / "artifacts" / "raw" / slug
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_target = raw_dir / "index.json"  # serve.py 按原样回内容
        if suffix == ".html" and text is not None:
            raw_target.write_text(text, encoding="utf-8")
        else:
            shutil.copyfile(source, raw_target)


def order_artifacts(artifacts: list[dict[str, Any]], featured: str) -> list[dict[str, Any]]:
    """把指定产物挪到第一条。

    产物面板打开时默认预览的就是列表第一条(`chat-app.tsx` 的
    `activeThreadArtifactFiles[0]`,中间那层只过滤、不排序)。而导出是按路径字典序排的,
    第一条往往是某个 `audit/*.md`——展示页更想让人一眼看到那个能跑的页面。

    `featured` 按**后缀匹配**,所以写 `cybervault-react/dist/index.html` 就够,
    不用带 `<thread>/artifacts/` 前缀。
    """

    needle = featured.strip().strip("/")
    if not needle:
        return artifacts
    index = next(
        (i for i, item in enumerate(artifacts) if item["path"].endswith(needle)), None
    )
    if index is None:
        print(f"警告:--feature 没匹配到任何产物,保持原顺序:{featured!r}")
        return artifacts
    return [artifacts[index], *artifacts[:index], *artifacts[index + 1 :]]


def export(
    out_dir: Path, mirror_root: Path, thread_id: str, db: Path, featured: str = ""
) -> None:
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

    artifacts = order_artifacts(thread_artifacts(thread_id), featured)
    export_artifacts(api, mirror_root, thread_id, artifacts)

    # 环境类读口:展示页不暴露本机的技能 / MCP / 记忆。
    # 路径要和真实后端的路由前缀逐字一致(app/skills/routes.py 等),否则前端启动时
    # 这三个请求会失败,控制台里挂着三条红色 —— 展示页第一眼就掉价。
    for rel in ("skills", "mcp/servers", "memory"):
        write_json(api / rel, [])

    print(
        f"已导出会话 {thread_id}:{len(messages)} 条消息、{len(artifacts)} 个产物 → {out_dir}"
    )
    print(f"产物镜像(供 HTML 相对引用解析)→ {mirror_root}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thread", default="latest",
        help="要导出的 thread id;`latest`(默认)= 聊天库里最新那条会话",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--assets",
        default="",
        help=f"产物镜像目录(默认 <out 的同级>/artifact-assets,对应站点 {ASSET_MOUNT}/)",
    )
    parser.add_argument("--db", default=str(CHAT_DB))
    parser.add_argument(
        "--feature",
        default="",
        help="把匹配这个后缀的产物挪到列表第一条(产物面板默认预览第一条)",
    )
    args = parser.parse_args()
    db = Path(args.db)
    thread_id = resolve_thread_id(db, args.thread)
    out = Path(args.out).resolve()
    mirror_root = (
        Path(args.assets).resolve() if args.assets else out.parent / "artifact-assets"
    )
    for stale in (out, mirror_root):
        if stale.exists():
            shutil.rmtree(stale)
    export(out, mirror_root, thread_id, db, args.feature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
