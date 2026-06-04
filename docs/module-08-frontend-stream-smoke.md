# 模块 8：前端流式验证页

这一模块先做一个最小可验证的 Next.js 前端界面，不做正式视觉设计，也不提前拆出复杂
前端状态架构。

目标很具体：

```txt
前端页面
-> 创建 thread
-> POST /api/chat/threads/{thread_id}/runs/stream
-> 读取 SSE response body
-> 解析 SlotFlow 业务事件
-> 把 assistant 文本增量显示出来
```

## 这一层解决什么问题

模块 1-7 已经证明后端可以产出 SSE，但那只是后端测试和命令行层面的证明。

模块 8 解决的是：“浏览器里的前端代码能不能真的消费这条流？”

它先不追求完整聊天产品，只验证四件事：

```txt
1. Next 页面能创建 thread
2. 浏览器能调用后端 runs/stream
3. 前端能把 SSE frame 解析成业务事件
4. message.delta / state.snapshot 能更新页面上的 assistant 文本
```

## 它接收什么输入

用户在页面输入框里提交一条普通文本，例如：

```txt
用三句话解释 SlotFlow 当前后端链路。
```

前端会整理成后端已经定义好的 `ChatStreamRequest`：

```json
{
  "message": "用三句话解释 SlotFlow 当前后端链路。",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "agent_name": "default",
  "metadata": {
    "source": "frontend-smoke"
  }
}
```

如果页面还没有 thread，会先调用：

```http
POST /api/chat/threads
```

然后再调用：

```http
POST /api/chat/threads/{thread_id}/runs/stream
```

## 它输出什么数据

前端内部把后端 SSE frame 解析成 `ChatStreamEvent`：

```ts
type ChatStreamEvent = {
  event:
    | "run.prepared"
    | "message.delta"
    | "tool.delta"
    | "state.snapshot"
    | "run.finished"
    | "run.error";
  data: Record<string, unknown>;
};
```

页面上会显示两类结果：

```txt
左侧：user / assistant 消息
右侧：最近 12 条 SlotFlow 业务 SSE 事件
```

`message.delta` 会追加到当前 assistant 消息。
`state.snapshot` 会尝试取最后一条 assistant / ai 消息作为最终文本快照。
`run.error` 会显示错误，并把当前 assistant 消息标记为 error。

## 它在完整链路里的位置

这一模块是完整链路的前端入口和前端出口：

```txt
前端输入  <-- 当前模块
-> 后端 API
-> run 配置
-> SlotFlow runtime / agent
-> SSE 事件
-> 前端流式状态  <-- 当前模块
-> UI 渲染       <-- 当前模块
```

它暂时不做正式 `useChatStream` hook，也不做正式聊天 UI 组件拆分。原因是当前目标是先验证
浏览器能不能跑通后端 SSE，而不是提前设计最终前端架构。

## 主要代码

```txt
frontend/src/lib/chat-stream.ts
frontend/src/app/page.tsx
frontend/next.config.ts
```

`chat-stream.ts` 负责：

```txt
createThread()
streamThreadRun()
读取 ReadableStream
按 \n\n 拆 SSE frame
解析 event: 和 data:
```

`page.tsx` 负责：

```txt
维护当前 thread
维护 user / assistant 消息列表
维护最近事件日志
提交表单
把 message.delta 更新到 assistant 文本
```

`next.config.ts` 增加了本地 rewrite：

```txt
/api/:path* -> http://localhost:8000/api/:path*
/health     -> http://localhost:8000/health
```

这样浏览器页面只调用相对路径 `/api/chat/...`，不用在第一版里处理跨端口 CORS。

页面默认发送 `deepseek-v4-flash`。如果后端是默认 static runtime，这个字段只是进入
`RunContext` 的可见配置；如果后端切到了 DeepSeek runtime，这个字段也能直接对应当前
live smoke test 已验证的模型名。

## 怎么验证

先启动后端：

```bash
cd /home/dell/code/SlotFlow/backend
uv run uvicorn app.main:app --reload --port 8000
```

再启动前端：

```bash
cd /home/dell/code/SlotFlow/frontend
pnpm dev
```

打开：

```txt
http://localhost:3000
```

点击 `Send` 后，预期结果是：

```txt
左侧出现 user 消息和 assistant 流式文本
右侧依次出现 run.prepared / message.delta / state.snapshot / run.finished
```

常规代码验证仍然使用：

```bash
cd /home/dell/code/SlotFlow
make verify
```
