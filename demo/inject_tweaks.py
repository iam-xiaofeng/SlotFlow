#!/usr/bin/env python3
"""把展示页补丁的 <script> 注入静态导出的每个 HTML 页面。

单独一个文件而不是内联进 build.sh:嵌套 heredoc 太容易被引号咬到。
"""

import pathlib
import sys

TAG = '<script src="/demo-tweaks.js" defer></script>'

# 只注入到 Next 导出的**页面**里。这两棵子树要跳过:
#   api/            —— 端点目录名是产物路径的 slug,`a/b/c.html` → `a__b__c.html`,
#                      于是**目录名也以 .html 结尾**,rglob("*.html") 会把它当文件。
#   artifact-assets/ —— 模型生成的产物原件,往里塞脚本等于篡改交付物。
SKIP_TOP_LEVEL = {"api", "artifact-assets"}


def main() -> int:
    out = pathlib.Path(sys.argv[1])
    patched = 0
    for page in out.rglob("*.html"):
        if not page.is_file():
            continue
        relative = page.relative_to(out)
        if relative.parts and relative.parts[0] in SKIP_TOP_LEVEL:
            continue
        html = page.read_text(encoding="utf-8")
        if TAG in html or "</body>" not in html:
            continue
        page.write_text(html.replace("</body>", f"{TAG}</body>"), encoding="utf-8")
        patched += 1
    print(f"[demo] 已注入展示页补丁到 {patched} 个页面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
