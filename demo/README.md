# SlotFlow 对话实录（静态展示站点）

把**现有前端原样静态导出**成一个只读展示站点。**产品代码一行不改** —— 不是仿一个页面，
就是 SlotFlow 自己的 Next.js 前端，只是数据源换成了静态文件。

线上：<https://slotflow-demo.pages.dev>

```bash
bash demo/build.sh --serve          # 构建并起在 http://localhost:8080
npx wrangler pages deploy demo/site --project-name slotflow-demo --branch main
```

## 它是怎么做到不改代码的

前端请求的是 `/api/chat/threads`、`/api/chat/threads/<id>/messages` 这些路径。
我们就在静态站点里**放同路径的 JSON 文件** —— `fetch()` 拿到真实文件，`Response.json()`
照常解析，前端根本不知道背后没有后端。

有两处要绕：

**① 路径冲突。** `/api/chat/threads` 既得是会话列表，又是 `<id>/messages` 的父目录，
而文件系统里一个名字不能既是文件又是目录。所以约定**每个端点都是一个目录、内容放
`index.json`**。

**② 产物读取接口带 query string。** `?path=a/b/c.md` 静态服务器路由不了，导出时按
slug 建目录（`a__b__c.md`）。另外产物 HTML 里是 `./assets/x.js` 这种相对引用，而预览
iframe 注入的 `<base href>` 做相对解析时**会丢掉 query string**——所以导出会把这些引用
改写成绝对路径，并按原目录结构在 `/artifact-assets/` 下镜像一份真实文件，让 JS 里的
动态 import 也能顺着同一棵树找到。

这两条规则在 `serve.py`（本地）和 `_worker.js`（Cloudflare Pages）里各实现一遍，行为一致。

> **不要再往站点里放 `_redirects`。** Pages 的 advanced mode 下它**并没有失效**，
> `env.ASSETS.fetch()` 仍会套用，和 `_worker.js` 的改写叠加会双重拼接 `index.json`，
> 症状是**首页正常但整个 `/api` 全线 404**。踩过一次，别再踩。

`build.sh` 构建时会临时换上一份 `output: "export"` 的 next.config，**构建结束立刻还原**，
git 里始终干净。

## 数据从哪来

直接读 `backend/.slotflow/chat.sqlite3` —— **就是产品自己会返回的东西**，
连 `metadata.tool_activities` / `metadata.todos` / `metadata.clarification` 都原样带过去，
所以工具时间线、todo 面板、澄清卡片在静态站点里是**真组件**渲染的，不是仿的。

```bash
cd backend
uv run python -m evals.export_demo_api --thread latest   # 默认取聊天库里最新那条会话
bash ../demo/build.sh                                     # 静态导出前端并组装站点
```

常用开关（`build.sh` 会透传同名环境变量）：

| 环境变量 | 作用 |
|---|---|
| `SLOTFLOW_DEMO_THREAD` | 指定导出哪条会话，默认 `latest` |
| `SLOTFLOW_DEMO_FEATURED` | 置顶哪个产物。产物面板默认预览**列表第一条**，不指定的话按字典序排，第一条常常是个 `.md` |

导出会跳过 `node_modules` 这类依赖目录。真机上一次 React 重构在 `artifacts/` 下装出
2660 个依赖文件，不挡的话产物面板直接被淹掉（后端的产物接口现在也一并挡了）。

## 展示页专用补丁

`demo-tweaks.js` 只注入静态产物，**不进产品源码**：拉遮罩盖住首屏空状态、直接进入唯一
那条会话、展开工具时间线、打开产物面板、滚到最后一条消息。真实产品里这些默认值都是对的
（不替用户选会话、不自动弹面板挡对话），展示页的诉求不一样。

两个判据值得记：面板的关闭按钮在**收起时仍留在 DOM 里**（0×0），所以要判"可见"而不是
"存在"；消息列表要按"装着工具时间线、自己又不在时间线内部"来找，按"可滚动高度最大"会
选中时间线自己的内部滚动区。

## 如实说明的限制

**本地预览时模型下拉是空的。** 前端有个开发期兜底：浏览器 host 是 `localhost` 时，
模型接口直连 `127.0.0.1:8000`。本地预览这个静态站点时那个后端并不存在，所以下拉会空。
**部署到真域名后不受影响**（host 不是 localhost，走相对路径）。为这点改产品代码不值得。

**输入框能打字但发不出去** —— 发送要 POST，静态站点没有这个端点。这是只读展示站点。

**Vite 源码入口 `index.html` 打不开是正常的**，它指向 `/src/main.tsx`，浏览器不能执行
TSX。要看成品请开 `dist/index.html`。

## 目录

| | |
|---|---|
| `build.sh` | 静态导出 + 组装站点 |
| `serve.py` | 本地静态服务器（零依赖，解析端点目录与产物 slug） |
| `_worker.js` | Cloudflare Pages 的同款解析规则（advanced mode） |
| `demo-tweaks.js` | 展示页专用补丁，只注入静态产物 |
| `inject_tweaks.py` | 把补丁的 `<script>` 注入导出的页面 |
| `api/` `artifact-assets/` `site/` | 构建产物，已 gitignore |

数据源在 `backend/evals/export_demo_api.py`。
