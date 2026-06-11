# 模块 26：端到端本地运行验收和前端流式显示修复

## 这个模块解决什么问题？

模块 25 已经有了可操作的聊天界面，但本地运行时出现了一个关键问题：

```txt
后端实际在持续输出 SSE
前端页面却像是等模型结束后才一次性显示
```

模块 26 做两件事：

```txt
确认本地前端 -> 后端 -> agent -> SSE -> 前端解析这条链路能跑通
修复前端流式接口被 Next rewrite 缓冲的问题
```

## 根因是什么？

早期前端为了少处理跨端口问题，所有请求都走相对路径：

```txt
浏览器
-> http://127.0.0.1:3000/api/chat/...
-> Next rewrites
-> http://localhost:8000/api/chat/...
```

这对普通 JSON API 没问题，比如：

```txt
GET  /api/chat/threads
POST /api/chat/threads
POST /api/uploads
```

但 SSE 不一样。SSE 依赖网络层持续把小块数据 flush 给浏览器。实际验收时发现：

```txt
直接请求 FastAPI 后端：能分多次收到 SSE chunk
通过 Next rewrite 请求：chunk 会被代理层合并，前端看起来就不流式
```

所以这不是 `useChatStream` 没有 append，也不是 SSE parser 解析错了，而是流式请求不应该再走
Next rewrite 代理。

## 本模块改了什么？

### 1. 前端 stream 接口直连后端

文件：

```txt
frontend/src/lib/chat-stream.ts
```

普通 API 仍然保持相对路径：

```txt
/api/chat/threads
/api/uploads
```

只有 `streamThreadRun()` 单独计算流式 URL：

```txt
NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL
-> NEXT_PUBLIC_SLOTFLOW_API_BASE_URL
-> 本地浏览器默认 http://127.0.0.1:8000
-> 非本地环境回退相对路径
```

这样本地开发时，页面发送消息会直接请求：

```txt
http://127.0.0.1:8000/api/chat/threads/{thread_id}/runs/stream
```

不再经过 Next rewrite。

### 2. 后端允许本地前端跨端口访问

文件：

```txt
backend/app/main.py
```

新增 FastAPI `CORSMiddleware`，默认允许：

```txt
http://localhost:3000
http://127.0.0.1:3000
```

也可以用环境变量覆盖：

```txt
SLOTFLOW_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

### 3. 增加 CORS 回归测试

文件：

```txt
backend/tests/test_health.py
```

新增测试：

```txt
test_local_frontend_origin_is_allowed_by_cors
```

它验证本地前端 origin 对后端 POST 预检请求会得到允许。

## 本地启动命令

后端：

```bash
cd /home/dell/code/SlotFlow/backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file ../.env
```

前端：

```bash
cd /home/dell/code/SlotFlow/frontend
pnpm dev
```

本次验收时已经确认：

```txt
后端：http://127.0.0.1:8000/health -> 200 OK
前端：http://127.0.0.1:3000/ -> 200 OK
```

## 端到端验收结果

### 1. 类型检查

```bash
cd /home/dell/code/SlotFlow/frontend
pnpm typecheck
```

结果：

```txt
通过
```

### 2. 后端相关测试

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest tests/test_chat_routes.py tests/test_sse.py tests/test_health.py
```

结果：

```txt
15 passed
```

### 3. 真实 CORS 预检

请求：

```bash
curl -i -X OPTIONS http://127.0.0.1:8000/api/chat/threads \
  -H 'Origin: http://127.0.0.1:3000' \
  -H 'Access-Control-Request-Method: POST'
```

关键响应：

```txt
HTTP/1.1 200 OK
access-control-allow-origin: http://127.0.0.1:3000
```

### 4. 真实 SSE 分块验收

用本地后端创建 thread 后，请求：

```txt
POST http://127.0.0.1:8000/api/chat/threads/{thread_id}/runs/stream
```

本次采样结果：

```json
{
  "networkChunks": 18,
  "events": 35,
  "deltas": 30,
  "firstChunkMs": 3510,
  "firstDeltaMs": 9076,
  "firstFinishedMs": 9537,
  "totalMs": 9537
}
```

关键点：

```txt
networkChunks > 1，说明网络层不是一次性返回
message.delta 有 30 条，说明业务事件确实在分段输出
firstDeltaMs 早于 finished，说明前端拿到 delta 后可以逐段 append
```

## 当前链路现在怎么走？

发送一条消息时：

```txt
ChatApp
-> useChatStream.sendMessage
-> createThread 仍走 /api rewrite
-> streamThreadRun 直连 http://127.0.0.1:8000
-> FastAPI StreamingResponse
-> AgentAdapter.stream_events
-> iter_business_events
-> message.delta
-> useChatStream appendAssistantText
-> ChatApp 逐段渲染 assistant 消息
```

## 最终前端整理

最终检查时，前端把原来 1000 行以上的 `chat-app.tsx` 拆成了更清楚的边界：

```txt
frontend/src/components/chat/chat-app.tsx
  只保留会话加载、发送、上传、滚动等流程状态。

frontend/src/components/chat/chat-sidebar.tsx
  左侧 shadcn Sidebar、会话历史、Skills/MCP/产物/更多入口、用户菜单。

frontend/src/components/chat/chat-composer.tsx
  输入框、上传附件、发送按钮、停止按钮、添加菜单。

frontend/src/components/chat/message-list.tsx
  消息列表、用户/assistant 气泡、Markdown/KaTeX 渲染、消息附件展示。

frontend/src/components/chat/chat-format.ts
  标题生成、文件大小格式化、公式归一化、消息附件 metadata 解析。
```

这样 `ChatApp` 不再同时承担 UI 细节和业务流程，后续接真实 Skills / MCP / 产物能力时，
可以在对应组件文件里扩展，不需要继续扩大主入口组件。

## 这一阶段还没有做什么？

模块 26 只解决本地端到端运行和前端流式显示阻断点。

没有在这一模块实现：

```txt
生产环境 API 网关设计
登录态和鉴权
多后端地址切换 UI
Skills / MCP / 产物的真实后端能力
浏览器自动化截图验收
```

浏览器自动化本次没有完成，是因为当前环境里的 Browser 运行时不可用，并且前端项目没有安装
Playwright。已经完成的是服务级和网络级验收。

最终补充验收：

```txt
后端完整测试：131 passed, 1 skipped
前端类型检查：pnpm typecheck 通过
前端首页：http://127.0.0.1:3000/ -> 200 OK
前端 /api rewrite 创建 thread -> 200 OK
后端直连 SSE：9 个 network chunk，18 条 message.delta
旧过渡路径扫描：backend/app、backend/tests、frontend/src 内无 StaticProjectionAgentAdapter / adapter_mode / raw fallback 引用
```
