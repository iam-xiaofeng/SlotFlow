# 模块 9：前端 SSE parser

模块 8 已经让页面能直接消费后端 `runs/stream`。但那一版为了快速验证，把 HTTP 请求、
ReadableStream 读取、SSE frame 解析都放在 `chat-stream.ts` 里。

模块 9 先把最底层的 SSE 解析抽成纯函数模块：

```txt
SSE 文本 buffer
-> 按空行拆 frame
-> 解析 event: / data:
-> ChatStreamEvent[]
```

## 这一层解决什么问题

浏览器拿到的是字节流，不是直接可用的 JSON 对象。后端发出的 SSE 长这样：

```txt
event: message.delta
data: {"delta":"第一段"}

event: run.finished
data: {"thread_id":"thread_xxx","run_id":"run_xxx"}

```

而 `ReadableStream` 每次读到的 chunk 不保证刚好等于一条完整 SSE 事件。它可能是：

```txt
半条 frame
一条完整 frame
多条 frame
上一条剩余 + 下一条开头
```

所以前端需要一个稳定的 parser，把“文本边界问题”和“业务状态更新”分开。

## 它接收什么输入

核心函数接收当前累计的文本 buffer：

```ts
drainSseBuffer(buffer, { flush?: boolean })
```

例如：

```txt
event: message.delta
data: {"delta":"第一段"}

event: message.delta
data: {"delta":"第二段"}
```

如果最后一条还没有 `\n\n` 结束，parser 会把它留在 `rest` 里，等下一次 chunk 继续拼。

## 它输出什么数据

输出是：

```ts
type SseBufferDrainResult = {
  events: ChatStreamEvent[];
  rest: string;
};
```

其中 `ChatStreamEvent` 是 SlotFlow 前端认识的业务事件：

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

这不是 LangGraph 官方事件类型，也不是浏览器原生类型，而是 SlotFlow 前后端之间约定的业务
事件形状。

## 它在完整链路里的位置

模块 9 位于后端 SSE 输出和前端状态更新之间：

```txt
前端输入
-> 后端 API
-> run 配置
-> SlotFlow runtime / agent
-> SSE 事件
-> 前端 SSE parser   <-- 当前模块
-> 前端流式状态
-> UI 渲染
```

它不负责发请求，也不负责更新 React state。这样后面做 `useChatStream` hook 时，hook
可以直接复用 parser，而不用重新处理文本帧细节。

## 主要代码

```txt
frontend/src/lib/sse-parser.ts
frontend/src/lib/chat-stream.ts
```

`sse-parser.ts` 负责纯解析：

```txt
drainSseBuffer()
parseSseFrame()
```

`chat-stream.ts` 负责网络读取：

```txt
fetch runs/stream
response.body.getReader()
TextDecoder
调用 drainSseBuffer()
yield ChatStreamEvent
```

这个拆分后的边界是：

```txt
chat-stream.ts = I/O 层
sse-parser.ts = 纯解析层
page.tsx       = 临时 UI 层
```

## 怎么验证

当前前端还没有单独测试框架，所以本模块先用 TypeScript 和生产构建验证：

```bash
cd /home/dell/code/SlotFlow/frontend
pnpm typecheck
pnpm build
```

后续如果引入 Vitest 或其他前端测试工具，第一批应该补 `drainSseBuffer()` 的聚焦测试：

```txt
单条完整 frame
多条 frame
半条 frame 留在 rest
CRLF 换行
flush 末尾残留 frame
```
