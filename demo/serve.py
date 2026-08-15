#!/usr/bin/env python3
"""展示站点的本地静态服务器(纯标准库,零依赖)。

只比 `python3 -m http.server` 多做一件事:**把 `<path>` 解析到 `<path>/index.json`**。

为什么需要:前端请求的是 `/api/chat/threads` 这种无扩展名路径,而 `/api/chat/threads` 同时又得
是 `/api/chat/threads/<id>/messages` 的父目录——文件系统里一个名字不能既是文件又是目录。
所以约定「每个端点都是一个目录,内容放 index.json」,由这里(以及 Cloudflare Pages 的
`_redirects`)统一解析。

用法::

    python3 demo/serve.py --root demo/site --port 8080
    cloudflared tunnel --url http://localhost:8080
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote


def _path_slug(value: str) -> str:
    """和 `export_demo_api.path_slug` 保持一致:`a/b/c.md` → `a__b__c.md`。"""

    return value.strip("/").replace("/", "__")


class DemoHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        # 产物读取有两种入口,导出时都落在同一个 slug 目录下:
        #   ?path=a/b/c.md            —— 老形式,query string 静态服务器路由不了
        #   /artifacts/raw/a/b/c.md   —— 路径式,带相对引用的 HTML 产物必须用这个
        #                                (`<base href>` 做相对解析时会丢掉 query string)
        head, _, query = path.partition("?")
        head = head.rstrip("/")
        for prefix in ("/artifacts/read", "/artifacts/raw"):
            if head.endswith(prefix):
                target = (parse_qs(query).get("path") or [""])[0]
                if target:
                    path = f"{head}/{_path_slug(unquote(target))}"
                break
            marker = f"{prefix}/"
            if marker in head:
                cut = head.index(marker) + len(marker)
                rest = head[cut:]
                if rest:
                    # slug 幂等:已经是 slug 的原样返回。
                    path = f"{head[:cut]}{_path_slug(unquote(rest))}"
                break
        resolved = Path(super().translate_path(path))
        if resolved.is_dir():
            # API 端点:目录里放的是 index.json;页面:Next 导出的是 index.html。
            for candidate in ("index.json", "index.html"):
                target = resolved / candidate
                if target.is_file():
                    return str(target)
        return str(resolved)

    def end_headers(self) -> None:
        # 展示站点是只读的,允许被任意页面嵌入/抓取;同时禁掉缓存,免得改完数据看到旧的。
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - 匹配基类签名
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="demo/site", help="站点根目录")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"站点目录不存在:{root}\n先跑一次 bash demo/build.sh")
        return 1

    handler = partial(DemoHandler, directory=str(root))
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"SlotFlow 展示站点 → http://localhost:{args.port}  (root={root})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
