# 模块 8 前端入门完整教程：Next.js 消费 SlotFlow SSE

这份教程面向刚开始学前端的人。它不假设你熟悉 Next.js，只假设你已经看完后端模块 1-7，
知道 SlotFlow 后端会把 agent 事件编码成 SSE 流。

当前阶段的目标不是做完整聊天产品，而是做一个最小浏览器闭环：

```txt
用户在浏览器输入消息
-> Next.js 页面提交请求
-> FastAPI 创建 thread / run
-> 后端返回 text/event-stream
-> 前端逐块读取 response body
-> 解析 event/data
-> 把 assistant 文本显示到页面
```

相关文件：

```txt
frontend/package.json
frontend/next.config.ts
frontend/src/app/layout.tsx
frontend/src/app/page.tsx
frontend/src/app/globals.css
frontend/src/lib/chat-stream.ts
frontend/src/components/ui/button.tsx
frontend/src/lib/utils.ts
docs/module-08-frontend-stream-smoke.md
```

## 1. 这个模块解决什么问题

后端模块 1-7 已经跑通了：

```txt
FastAPI route
-> ChatRepository
-> RunConfigBundle
-> AgentAdapter
-> BusinessSseEvent
-> StreamingResponse
```

但这只证明后端能产出 SSE。模块 8 要验证浏览器里的前端代码能不能消费这条流。

它重点验证四件事：

```txt
1. Next 页面能调用 POST /api/chat/threads 创建 thread
2. Next 页面能调用 POST /api/chat/threads/{thread_id}/runs/stream
3. 浏览器能读取 StreamingResponse 返回的 ReadableStream
4. 前端能把 message.delta / state.snapshot 显示成 assistant 消息
```

所以当前页面叫 smoke test 更准确。它是“链路验证页”，不是最终产品 UI。

## 2. 输入是什么

用户在页面文本框里输入一段文字，例如：

```txt
用三句话解释 SlotFlow 当前后端链路。
```

前端会把它整理成后端认识的 `ChatStreamRequest` JSON：

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

这里的字段归属要分清：

```txt
ChatStreamRequest
  是 SlotFlow 后端自定义的请求体形状
  后端定义在 backend/app/chat/models.py

model_name / mode / agent_name
  是 SlotFlow 当前学习阶段的业务配置
  不是 Next.js 规定的字段

metadata.source = "frontend-smoke"
  是前端自己加的调试标记
  后端会保存到 user message metadata 里
```

## 3. 输出是什么

后端返回的是 SSE 文本帧，不是普通 JSON 数组。

一帧长这样：

```txt
event: message.delta
data: {"message_id":"run_x:assistant","role":"assistant","delta":"你好","index":0}

```

注意最后有一个空行。SSE 靠空行分隔每一帧。

前端会把它解析成 TypeScript 对象：

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

这些事件名是 SlotFlow 自定义的业务事件名，不是浏览器或 Next.js 自动规定的。

页面最终显示两类内容：

```txt
左侧：
  user 消息
  assistant 消息

右侧：
  最近 12 条 SSE 业务事件日志
```

## 4. 它在完整链路里的位置

模块 8 是完整链路的入口和出口：

```txt
前端输入
-> 后端 API
-> run 配置
-> SlotFlow runtime / agent
-> SSE 事件
-> 前端流式状态
-> UI 渲染
```

换句话说，前端做两件事：

```txt
1. 把用户输入变成后端 API 请求
2. 把后端 SSE 事件变成页面状态
```

## 5. Next.js 是什么

Next.js 是基于 React 的 Web 应用框架。

React 负责“页面怎么根据状态渲染”。Next.js 在 React 上面加了这些能力：

```txt
文件路由
服务端渲染
客户端组件
构建和开发服务器
静态资源处理
API 代理 / rewrites
TypeScript 集成
```

当前 SlotFlow 前端使用的是 Next.js App Router。它的核心目录是：

```txt
frontend/src/app/
```

在 App Router 里，文件路径会决定页面结构。

当前有三个关键文件：

```txt
src/app/layout.tsx
  整个应用的根布局

src/app/page.tsx
  首页，也就是 http://localhost:3000/

src/app/globals.css
  全局样式
```

## 6. package.json 怎么读

`frontend/package.json` 是前端项目说明书。

重要脚本：

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "typecheck": "tsc --noEmit"
  }
}
```

含义：

```txt
pnpm dev
  启动 Next.js 开发服务器

pnpm build
  构建生产版本，检查页面能不能被 Next 正常编译

pnpm typecheck
  只做 TypeScript 类型检查，不输出构建产物
```

当前主要依赖：

```txt
next
  Next.js 框架

react / react-dom
  React 本体

tailwindcss
  样式工具

lucide-react
  图标库

class-variance-authority
  用来写 Button 的 variant 样式

clsx / tailwind-merge
  合并 className
```

## 7. layout.tsx 是什么

`src/app/layout.tsx` 是整个应用的根布局。

当前代码核心是：

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SlotFlow",
  description: "Learning-oriented agent workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

几个点：

```txt
metadata
  Next.js 读取的页面元信息
  会影响浏览器标题、SEO metadata 等

import "./globals.css"
  引入全局 CSS
  一般只在根 layout 引入一次

children
  当前路由页面会被塞到这里
  对首页来说，children 就是 page.tsx 的返回结果
```

可以把它理解成：

```txt
layout.tsx 负责 HTML 外壳
page.tsx 负责当前页面内容
```

## 8. page.tsx 为什么有 "use client"

`src/app/page.tsx` 第一行是：

```tsx
"use client";
```

这是 Next.js App Router 里的特殊指令。

默认情况下，App Router 里的组件倾向于 Server Component。Server Component 在服务端执行，
不能直接使用浏览器交互能力，例如：

```txt
useState
useEffect
useRef
点击事件 onClick
表单提交 onSubmit
window / crypto / DOM API
```

当前页面需要输入框、按钮、状态更新、读取浏览器 fetch stream，所以必须是 Client Component。

因此要写：

```tsx
"use client";
```

这个字符串不是普通注释，也不是 TypeScript 语法。它是 Next.js 识别客户端组件的约定。

## 9. page.tsx 的整体结构

当前 `Home` 组件大致分成四块：

```txt
1. import 和类型定义
2. React state
3. 事件处理函数
4. JSX 页面结构
```

简化后是：

```tsx
export default function Home() {
  const [thread, setThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [events, setEvents] = useState([]);
  const [input, setInput] = useState(starterPrompt);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    // 创建 thread
    // 创建本地 user / assistant 消息
    // 调用 streamThreadRun
    // 消费 SSE 事件
  }

  return (
    <main>
      {/* 页面 UI */}
    </main>
  );
}
```

`Home` 是 React 组件。React 组件本质上是一个函数：

```txt
输入：props 和当前 state
输出：一棵 JSX UI 树
```

当 state 改变时，React 会重新调用组件函数，得到新的 UI。

## 10. useState 怎么理解

例如：

```tsx
const [input, setInput] = useState(starterPrompt);
```

含义：

```txt
input
  当前输入框内容

setInput
  修改 input 的函数

starterPrompt
  初始值
```

当输入框变化时：

```tsx
onChange={(event) => setInput(event.target.value)}
```

浏览器触发 `onChange`，React 调用 `setInput`，页面重新渲染，输入框显示新值。

当前页面主要 state：

```txt
thread
  当前会话 thread，第一次发送时创建

messages
  页面上显示的 user / assistant 消息

events
  右侧事件日志

input
  文本框内容

isStreaming
  当前是否正在等待后端流式返回

error
  当前错误信息
```

这些是前端 UI state，不是后端数据库。

## 11. UiMessage 是什么

`page.tsx` 定义了：

```ts
type UiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "streaming" | "done" | "error";
};
```

这是前端页面自己的消息形状。它不等于后端的 `MessageRecord`。

区别：

```txt
UiMessage
  前端临时显示用
  有 status，方便控制 loading / error UI
  id 由浏览器 crypto.randomUUID 生成

MessageRecord
  后端仓库保存用
  有 thread_id / run_id / metadata / created_at
  id 由后端生成
```

当前页面没有调用 `GET /messages` 重新拉后端消息，所以页面消息主要靠流式事件即时构建。

## 12. handleSubmit 的完整流程

表单提交入口：

```tsx
<form onSubmit={handleSubmit}>
```

函数第一步：

```tsx
event.preventDefault();
```

默认 HTML 表单提交会刷新页面。React 单页应用通常要阻止这个默认行为，然后自己用
`fetch` 调 API。

接着清理输入：

```tsx
const text = input.trim();
if (!text || isStreaming) {
  return;
}
```

含义：

```txt
如果只有空白字符，不发送
如果已经在 streaming，不重复发送
```

如果还没有 thread，就创建：

```tsx
const activeThread = thread ?? (await createThread("SlotFlow smoke test"));
```

`??` 是空值合并运算符：

```txt
左边不是 null/undefined -> 用左边
左边是 null/undefined   -> 用右边
```

然后先在页面上放两条消息：

```tsx
userMessage
  用户刚输入的内容

assistantMessage
  空内容，占位，status=streaming
```

这样用户一点击 Send，页面立刻能看到自己的消息和一个正在等待的 assistant 气泡。

之后开始真正消费后端流：

```tsx
for await (const streamEvent of streamThreadRun(activeThread.id, {...})) {
  // 每收到一个 SSE 事件，就处理一次
}
```

这里 `streamThreadRun(...)` 是异步生成器。它不是一次性返回数组，而是一边读 HTTP 流，
一边 `yield` 解析出来的事件。

## 13. for await 是什么

普通数组用：

```ts
for (const item of items) {
  console.log(item);
}
```

异步流用：

```ts
for await (const item of asyncItems) {
  console.log(item);
}
```

当前场景中：

```txt
streamThreadRun(...)
  返回 AsyncGenerator<ChatStreamEvent>

for await
  每当后端发来一帧并解析成功，就进入循环体一次
```

这和 Python 后端里的 `async for` 很像。

后端：

```py
async for event in iter_business_events(events):
    yield encode_sse_event(event)
```

前端：

```ts
for await (const streamEvent of streamThreadRun(...)) {
  // 更新 UI
}
```

## 14. message.delta 怎么更新页面

后端流出：

```txt
event: message.delta
data: {"delta":"你好"}
```

前端处理：

```tsx
if (streamEvent.event === "message.delta") {
  const delta = streamEvent.data.delta;
  if (typeof delta === "string") {
    await appendAssistantTextSlowly(assistantId, delta);
  }
}
```

`streamEvent.data` 的类型是 `Record<string, unknown>`，所以 TypeScript 不知道
`delta` 一定是字符串。必须先判断：

```tsx
typeof delta === "string"
```

然后追加到 assistant 消息：

```tsx
setMessages((current) =>
  current.map((message) =>
    message.id === messageId
      ? { ...message, content: message.content + delta }
      : message,
  ),
);
```

这段是 React 里更新数组 state 的常见写法。

关键规则：

```txt
不要直接修改原数组
不要直接 message.content += delta
而是返回一个新数组、新对象
```

原因是 React 靠引用变化判断 state 是否更新。不可变更新更稳定。

## 15. state.snapshot 为什么还要处理

`message.delta` 是增量文本。它适合做实时显示，但可能有这些问题：

```txt
模型流式 token 边界不稳定
中途可能缺少某些片段
不同 adapter 的 delta 行为可能不同
```

所以后端还会发：

```txt
event: state.snapshot
data: {
  "messages": [
    {"role": "assistant", "content": "最终完整回答"}
  ]
}
```

前端用它校准最终 assistant 文本：

```tsx
if (streamEvent.event === "state.snapshot") {
  const content = latestAssistantContent(streamEvent);
  if (content) {
    replaceAssistantText(assistantId, content);
  }
}
```

可以理解成：

```txt
message.delta
  负责流式体验

state.snapshot
  负责最终校准
```

## 16. run.error 怎么处理

如果后端 adapter 抛异常，SSE 层会转成：

```txt
event: run.error
data: {"name":"ImportError","message":"..."}
```

前端处理：

```tsx
if (streamEvent.event === "run.error") {
  const message = String(streamEvent.data.message ?? "agent stream failed");
  setError(message);
  markAssistant(assistantId, "error");
}
```

页面会显示错误，并把 assistant 消息标记为 error。

这里的 `??` 也是空值合并：

```txt
data.message 有值 -> 使用它
data.message 是 null/undefined -> 使用 "agent stream failed"
```

## 17. chat-stream.ts 的职责

`src/lib/chat-stream.ts` 是前端 API 层。

它做两件事：

```txt
createThread(title)
  调 POST /api/chat/threads

streamThreadRun(threadId, body)
  调 POST /api/chat/threads/{thread_id}/runs/stream
  读取 response.body
  解析 SSE
  yield ChatStreamEvent
```

为什么单独放到 `lib/`：

```txt
page.tsx 专心处理 UI 和 state
chat-stream.ts 专心处理 HTTP 和 SSE 协议
```

这就是前端里常见的边界拆分。

## 18. createThread 怎么读

代码：

```ts
export async function createThread(title?: string): Promise<ThreadRecord> {
  const response = await fetch("/api/chat/threads", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });

  if (!response.ok) {
    throw new Error(`create thread failed: ${response.status}`);
  }

  return response.json() as Promise<ThreadRecord>;
}
```

几个点：

```txt
fetch
  浏览器内置 HTTP 请求函数

method: "POST"
  调后端创建接口

Content-Type: application/json
  告诉后端 body 是 JSON

JSON.stringify({ title })
  把 JS 对象转成 JSON 字符串

response.ok
  HTTP 状态码是否在 200-299

response.json()
  把响应 JSON 解析成 JS 对象
```

`title?: string` 表示 `title` 可传可不传。

## 19. 为什么 POST SSE 不能直接用 EventSource

浏览器原生 `EventSource` 很适合 SSE，但它有一个限制：默认只能发 GET 请求。

当前后端接口是：

```txt
POST /api/chat/threads/{thread_id}/runs/stream
```

因为它要携带 JSON body：

```json
{
  "message": "...",
  "mode": "pro"
}
```

所以当前前端使用：

```ts
fetch(...)
response.body.getReader()
```

自己读取流。

选择关系：

```txt
GET SSE，无复杂 body
  可以考虑 EventSource

POST SSE，需要 JSON body
  用 fetch + ReadableStream
```

## 20. streamThreadRun 怎么读

函数签名：

```ts
export async function* streamThreadRun(
  threadId: string,
  body: ChatStreamRequest,
): AsyncGenerator<ChatStreamEvent>
```

这里有两个关键语法：

```txt
async function*
  异步生成器函数

AsyncGenerator<ChatStreamEvent>
  每次 yield 一个 ChatStreamEvent
```

它先发请求：

```ts
const response = await fetch(`/api/chat/threads/${threadId}/runs/stream`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify(body),
});
```

然后拿到 reader：

```ts
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
```

含义：

```txt
reader
  从 HTTP response body 里一块一块读 bytes

TextDecoder
  把 bytes 解码成字符串

buffer
  保存还没凑成完整 SSE frame 的文本
```

为什么需要 buffer？

因为网络 chunk 和 SSE frame 不是一回事。

后端可能发：

```txt
event: message.delta
data: {"delta":"你好"}

```

但浏览器读到的 chunk 可能刚好切在中间：

```txt
chunk 1: event: message.delta\ndata: {"del
chunk 2: ta":"你好"}\n\n
```

所以前端必须把 chunk 拼起来，直到遇到 `\n\n`，才能确定一帧结束。

## 21. drainSseBuffer 做什么

核心代码：

```ts
function drainSseBuffer(buffer: string): {
  events: ChatStreamEvent[];
  rest: string;
} {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const events: ChatStreamEvent[] = [];
  let rest = normalized;

  while (true) {
    const boundary = rest.indexOf("\n\n");
    if (boundary === -1) {
      break;
    }

    const frame = rest.slice(0, boundary);
    rest = rest.slice(boundary + 2);

    const event = parseSseFrame(frame);
    if (event) {
      events.push(event);
    }
  }

  return { events, rest };
}
```

它做的是：

```txt
1. 把 Windows 换行 \r\n 统一成 \n
2. 在 buffer 里找 \n\n
3. 找到一帧就切出来 parse
4. 剩下不完整的文本留到下一次 reader.read()
```

例子：

```txt
输入 buffer:
  event: run.prepared\ndata: {}\n\nevent: message.delta\ndata: {"delta":"你

输出:
  events:
    run.prepared

  rest:
    event: message.delta\ndata: {"delta":"你
```

下一次 chunk 到了以后，`rest + 新文本` 再继续解析。

## 22. parseSseFrame 做什么

SSE frame 是文本，不是 JSON。

例如：

```txt
event: message.delta
data: {"delta":"你好"}
```

`parseSseFrame` 做三步：

```txt
1. 找 event: 开头的行
2. 找 data: 开头的行
3. 把 data 后面的 JSON 字符串 JSON.parse 成对象
```

简化理解：

```ts
const event = "message.delta";
const data = JSON.parse('{"delta":"你好"}');

return { event, data };
```

当前 parser 只支持 SlotFlow 当前需要的最小 SSE 格式：

```txt
event: ...
data: ...
```

它暂时不处理：

```txt
id:
retry:
注释行
断线续传
```

这是合理的，因为后端当前也没有启用这些高级能力。

## 23. next.config.ts 的 rewrite 是什么

前端开发服务器通常跑在：

```txt
http://localhost:3000
```

后端 FastAPI 通常跑在：

```txt
http://localhost:8000
```

如果浏览器页面直接请求 `http://localhost:8000/api/...`，就会涉及跨端口 CORS。

当前 `next.config.ts` 做了代理：

```ts
const backendUrl = process.env.SLOTFLOW_BACKEND_URL ?? "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${backendUrl}/health`,
      },
    ];
  },
};
```

于是浏览器请求：

```txt
http://localhost:3000/api/chat/threads
```

Next dev server 会转发到：

```txt
http://localhost:8000/api/chat/threads
```

对页面代码来说，只需要写相对路径：

```ts
fetch("/api/chat/threads")
```

这让第一版前端不用先处理 CORS。

## 24. Tailwind 和 globals.css 怎么看

当前 `globals.css` 开头：

```css
@import "tailwindcss";
```

表示引入 Tailwind。

然后定义一组 CSS 变量：

```css
:root {
  --background: #efeee7;
  --foreground: #171914;
  --surface: #fffdf6;
  --border: #d4cdbc;
  --primary: #2457c5;
}
```

页面里这样使用：

```tsx
className="bg-[var(--background)] text-[var(--foreground)]"
```

这表示：

```txt
背景色使用 CSS 变量 --background
文字色使用 CSS 变量 --foreground
```

Tailwind class 例子：

```txt
min-h-screen
  最小高度等于屏幕高度

flex
  使用 flex 布局

grid
  使用 grid 布局

px-4 py-3
  左右 padding 1rem，上下 padding 0.75rem

border
  加边框

text-sm
  小号文字

overflow-y-auto
  垂直方向内容超出时滚动
```

你现在可以先把 Tailwind 当作“写在 className 里的 CSS 快捷方式”。

## 25. Button 组件怎么理解

`src/components/ui/button.tsx` 是一个复用按钮组件。

它封装了：

```txt
默认按钮样式
outline 按钮样式
icon 尺寸
disabled 状态
```

页面里这样用：

```tsx
<Button type="submit" disabled={isStreaming || !input.trim()}>
  <Send className="size-4" />
  Send
</Button>
```

你可以把它理解成带默认样式的 HTML `<button>`。

`Button` 内部用到了：

```txt
class-variance-authority
  根据 variant / size 组合 className

cn(...)
  合并 className，处理 Tailwind 冲突

lucide-react
  提供 Send / LoaderCircle / RotateCcw 图标
```

这些是前端 UI 工具，不影响后端数据流。

## 26. startTransition 是什么

当前代码：

```tsx
startTransition(() => {
  setEvents((current) => [...current.slice(-11), streamEvent]);
});
```

`startTransition` 是 React 提供的 API。它告诉 React：

```txt
这次状态更新不是最紧急的用户输入
可以低优先级处理
```

这里右侧事件日志只是辅助信息，不需要抢占输入框、消息文本这些更重要的渲染。

不过当前页面规模很小，不用 `startTransition` 也能跑。这里更多是让事件日志更新不要过度干扰主聊天文本。

## 27. useRef 和自动滚动

当前代码：

```tsx
const messagesEndRef = useRef<HTMLDivElement | null>(null);

useEffect(() => {
  messagesEndRef.current?.scrollIntoView({ block: "end" });
}, [messages]);
```

页面底部有：

```tsx
<div ref={messagesEndRef} />
```

含义：

```txt
useRef
  保存一个 DOM 节点引用

messagesEndRef.current
  指向底部那个 div

scrollIntoView
  让浏览器滚动到这个 div

[messages]
  每次 messages 改变后执行
```

所以每次 assistant 文本追加，聊天区域都会尽量滚到底部。

## 28. 怎么启动验证

先启动后端：

```bash
cd /home/dell/code/SlotFlow/backend
uv run uvicorn app.main:app --reload --port 8000 --env-file ../.env
```

如果只想 static runtime，不需要 `.env`：

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

点击 Send 后，正常现象：

```txt
左侧出现 user 消息
左侧 assistant 消息逐步出现文字
右侧出现 run.prepared / message.delta / state.snapshot / run.finished
```

## 29. 常见问题

### 请求打不到后端

确认后端在 8000：

```bash
curl http://localhost:8000/health
```

确认前端 rewrite 指向正确：

```txt
frontend/next.config.ts
SLOTFLOW_BACKEND_URL
```

如果后端不是 8000，启动前端前设置：

```bash
SLOTFLOW_BACKEND_URL=http://localhost:8001 pnpm dev
```

### 页面报 run.error

`run.error` 是后端通过 SSE 发回来的业务错误，不是前端解析失败。

常见原因：

```txt
DeepSeek API key 没进后端进程
模型名不对
代理依赖缺失
网络不可用
LangGraph adapter 抛异常
```

处理顺序：

```txt
1. 看右侧 Event Log 的 run.error data.message
2. 看后端终端日志
3. 确认后端是否用 --env-file ../.env 启动
4. 确认 DEEPSEEK_API_KEY 是否存在
```

### 前端没有逐字显示

后端可能一次性发了较大的 delta，也可能 static adapter 切片比较粗。

当前页面用：

```ts
appendAssistantTextSlowly(...)
```

把后端 delta 再按小块显示，这是前端展示效果，不改变真实后端事件。

### state.snapshot 覆盖了前面的文本

这是预期行为。

当前策略是：

```txt
先用 message.delta 展示流式体验
再用 state.snapshot 校准最终内容
```

如果二者内容一样，用户看不出变化。如果不一样，snapshot 会覆盖成最终回答。

## 30. 当前阶段不要急着做什么

现在先不要急着做：

```txt
完整会话侧边栏
复杂消息组件体系
Markdown 渲染
代码高亮
上传文件
取消 run
自动重连
SSE Last-Event-ID
用户登录
数据库持久化前端同步
```

这些都可以做，但不是模块 8 的重点。

当前最重要的是你能清楚说出：

```txt
页面状态在哪里
请求在哪里发
SSE 在哪里解析
message.delta 怎么变成 UI 文本
state.snapshot 为什么要校准
Next rewrite 为什么存在
```

## 31. 建议学习顺序

按这个顺序看代码：

```txt
1. frontend/package.json
   先知道怎么启动、怎么检查

2. frontend/src/app/layout.tsx
   理解根布局和 globals.css

3. frontend/src/app/page.tsx 的 state 定义
   理解页面有哪些状态

4. handleSubmit
   理解用户点击 Send 后发生什么

5. frontend/src/lib/chat-stream.ts
   理解 fetch、ReadableStream、SSE parser

6. page.tsx 的 JSX
   理解 state 如何渲染成界面

7. frontend/next.config.ts
   理解 /api 请求为什么能转到 FastAPI
```

每看一个函数都问四个问题：

```txt
它解决什么问题？
它接收什么输入？
它输出什么数据？
它在前端 -> 后端 -> SSE -> 前端链路里的位置是什么？
```

这和后端模块 1-7 的学习方法保持一致。

