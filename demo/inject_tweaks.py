#!/usr/bin/env python3
"""把展示页补丁的 <script> 注入静态导出的每个 HTML 页面。

单独一个文件而不是内联进 build.sh:嵌套 heredoc 太容易被引号咬到。
"""

import pathlib
import sys

TAG = '<script src="/demo-tweaks.js" defer></script>'


def main() -> int:
    out = pathlib.Path(sys.argv[1])
    patched = 0
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        if TAG in html or "</body>" not in html:
            continue
        page.write_text(html.replace("</body>", f"{TAG}</body>"), encoding="utf-8")
        patched += 1
    print(f"[demo] 已注入展示页补丁到 {patched} 个页面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
