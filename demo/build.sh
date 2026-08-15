#!/usr/bin/env bash
# 把**现有前端原样**静态导出成展示站点,产品代码一行不改。
#
# 做法:
#   1) 临时换上一个 `output: "export"` 的 next.config(构建完立刻换回来,git 里始终干净);
#   2) `pnpm build` 产出纯静态站点;
#   3) 把 `evals.export_demo_api` 生成的 `/api/...` JSON 拷进产物里。
#
# 第 3 步是关键:前端请求的是 `/api/chat/threads` 这种路径,我们就在站点里放一个同路径的
# JSON 文件。`fetch()` 拿到真实文件、`Response.json()` 照常解析——所以**不需要改前端代码**,
# 也不需要后端。每个端点是一个目录 + index.json(路径冲突的原因见 export_demo_api.py)。
#
# 用法:
#   bash demo/build.sh          # 产出 demo/site/
#   bash demo/build.sh --serve  # 产出并起本地静态服务器
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"
OUT="$ROOT/demo/site"
CONFIG="$FRONTEND/next.config.ts"
BACKUP="$FRONTEND/.next.config.ts.demo-backup"

restore_config() {
  if [[ -f "$BACKUP" ]]; then
    mv -f "$BACKUP" "$CONFIG"
    echo "[demo] 已还原 next.config.ts"
  fi
}
trap restore_config EXIT

echo "[demo] 1/3 生成静态 API 数据"
# 默认取聊天库里**最新**那条会话——跑完一段真实对话直接重跑本脚本即可,不用抄 id。
# 想指定:SLOTFLOW_DEMO_THREAD=thread_xxx bash demo/build.sh
THREAD="${SLOTFLOW_DEMO_THREAD:-latest}"
# 置顶产物:产物面板默认预览列表第一条,而导出是按路径字典序排的,
# 第一条常常是某个 .md。指定一下,让人一打开就看到那个能跑的页面。
FEATURED="${SLOTFLOW_DEMO_FEATURED:-cybervault-react/dist/index.html}"
(cd "$ROOT/backend" && uv run python -m evals.export_demo_api --thread "$THREAD" --feature "$FEATURED")

echo "[demo] 2/3 静态导出真前端"
cp -f "$CONFIG" "$BACKUP"
cat > "$CONFIG" <<'TS'
import type { NextConfig } from "next";

// 展示站点专用配置(由 demo/build.sh 临时写入,构建结束会还原)。
// rewrites 在 output: "export" 下无意义(没有服务器),这里整个去掉;
// /api/* 由 demo/build.sh 拷进来的静态 JSON 文件承担。
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
TS

# NEXT_PUBLIC_SLOTFLOW_API_BASE_URL="/" 让 resolveChatStreamBaseUrl 走"显式配置"分支:
# joinBaseUrl 会把末尾斜杠剥掉,得到 "/api/..." 这样的相对路径。
# 不设的话它有个开发期兜底——浏览器 host 是 localhost 时直连 127.0.0.1:8000——
# 本地预览这个静态站点就会去敲一个并不存在的后端。
(cd "$FRONTEND" && rm -rf out && NEXT_PUBLIC_SLOTFLOW_API_BASE_URL="/" pnpm build >/dev/null)
restore_config

echo "[demo] 3/3 组装站点"
rm -rf "$OUT"
cp -r "$FRONTEND/out" "$OUT"
cp -r "$ROOT/demo/api" "$OUT/api"
# 产物按原目录结构的镜像。产物面板预览 HTML 时相对引用要靠它才解析得到
# (原因见 export_demo_api.py 的 ASSET_MOUNT 注释)。放站点根,避开 _redirects 的 /api/* 改写。
if [[ -d "$ROOT/demo/artifact-assets" ]]; then
  cp -r "$ROOT/demo/artifact-assets" "$OUT/artifact-assets"
fi
# Cloudflare Pages 的路由适配器(advanced mode)。本地预览用 demo/serve.py,规则一致。
# 没有它的话:静态站点表达不了 `?path=` → slug 这条规则,产物面板在 Pages 上全 404。
# 注意别再加 _redirects:它在 advanced mode 下**依然生效**,和这里的改写叠加会双重拼接
# index.json,导致首页正常但 /api 全线 404。详见 _worker.js 顶部注释。
if [[ -f "$ROOT/demo/_worker.js" ]]; then
  cp -f "$ROOT/demo/_worker.js" "$OUT/_worker.js"
fi

# 展示页专用补丁:展开工具时间线 + 自动打开产物面板。
# 只注入到静态产物里,**不进产品源码**——真实产品那两个默认值是对的,展示页的诉求不一样。
cp -f "$ROOT/demo/demo-tweaks.js" "$OUT/demo-tweaks.js"
python3 "$ROOT/demo/inject_tweaks.py" "$OUT"

echo "[demo] 完成 → $OUT ($(du -sh "$OUT" | cut -f1))"

if [[ "${1:-}" == "--serve" ]]; then
  echo "[demo] http://localhost:8080"
  exec python3 "$ROOT/demo/serve.py" --root "$OUT" --port 8080
fi
