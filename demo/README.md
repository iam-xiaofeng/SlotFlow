# SlotFlow 对话实录（静态展示站点）

把**现有前端原样静态导出**成一个只读展示站点。**产品代码一行不改** —— 不是仿一个页面，
就是 SlotFlow 自己的 Next.js 前端，只是数据源换成了静态文件。

```bash
bash demo/build.sh --serve          # 构建并起在 http://localhost:8080
cloudflared tunnel --url http://localhost:8080
```

## 它是怎么做到不改代码的

前端请求的是 `/api/chat/threads`、`/api/chat/threads/<id>/messages` 这些路径。
我们就在静态站点里**放同路径的 JSON 文件** —— `fetch()` 拿到真实文件，`Response.json()`
照常解析，前端根本不知道背后没有后端。

有一个路径冲突要绕：`/api/chat/threads` 既得是会话列表，又是 `<id>/messages` 的父目录，
而文件系统里一个名字不能既是文件又是目录。所以约定**每个端点都是一个目录、内容放
`index.json`**，由 `serve.py`（本地/隧道）或 `_redirects`（Cloudflare Pages）统一解析。

`build.sh` 构建时会临时换上一份 `output: "export"` 的 next.config，**构建结束立刻还原**，
git 里始终干净。

## 数据从哪来

`demo/transcripts.js` —— SlotFlow 的**真实运行结果**，由 `backend/evals/build_demo.py` 跑出来，
其中 8 条复用 `backend/evals/` 的评测样本（跑过真机评测、有分数可查），2 条是专门为展示写的
复杂长任务（做一个落地页、深度调研 Agent 框架差异）。

导出前清洗过一次：丢掉空 assistant 消息、剔除 429/限流/连接错误这类噪音——展示页是给人看
「这个 agent 能做什么」的，基础设施抖动不属于要展示的内容。**清洗只删噪音，绝不改写模型说过的话。**

重新生成：

```bash
cd backend
uv run python -m evals.build_demo                    # 重跑真机，产出 transcripts.js
uv run python -m evals.export_demo_api               # transcripts.js → demo/api/
bash ../demo/build.sh                                # 静态导出前端并组装站点
```

## 两个如实说明的限制

**① 没有工具时间线。** 后端只持久化 user / assistant 消息（见 `backend/app/chat/routes.py`），
工具消息不落库。所以真前端**重新打开任何一个历史会话**——不管是不是 demo——本来就只显示
正文 + 思考框 + 澄清卡片；工具时间线只在流式那一刻由 `tool.status` 事件驱动。
这里如实保持产品行为，没有伪造一份后端不会返回的数据。

**② 本地预览时模型下拉是空的。** 前端有个开发期兜底：浏览器 host 是 `localhost` 时，
模型接口直连 `127.0.0.1:8000`。本地预览这个静态站点时那个后端并不存在，所以下拉会空。
**部署到真域名后不受影响**（host 不是 localhost，走相对路径）。为这点改产品代码不值得。

**输入框能打字但发不出去** —— 发送要 POST，静态站点没有这个端点。这是只读展示站点。

## 目录

| | |
|---|---|
| `transcripts.js` | 真实运行结果（数据源，进 git） |
| `build.sh` | 静态导出 + 组装站点 |
| `serve.py` | 本地静态服务器（零依赖，解析 `<path>` → `<path>/index.json`） |
| `_redirects` | Cloudflare Pages 的同款解析规则 |
| `api/` `site/` | 构建产物，已 gitignore |
