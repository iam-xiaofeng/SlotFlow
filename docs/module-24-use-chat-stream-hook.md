# 模块 24：前端 useChatStream hook

## 这个模块解决什么问题？

模块 24 把前端 smoke 页面里的流式聊天逻辑抽成正式 hook：

```txt
useChatStream
  -> 创建 thread
  -> 发送 runs/stream 请求
  -> 解析 SSE 事件
  -> 维护 user/assistant 消息状态
  -> 维护 loading/error/cancel 状态
```

这样模块 25 做 shadcn/ui 聊天界面时，不需要在页面组件里重复写 SSE 循环和
assistant 文本拼接逻辑。UI 只消费 hook 暴露的 `messages/events/isStreaming/error`
和动作函数。

## 它接收什么输入？

hook 的初始化参数是默认运行配置：

```ts
useChatStream({
  defaultThreadTitle: "SlotFlow smoke test",
  defaultModelName: "deepseek-v4-flash",
  defaultMode: "pro",
  defaultAgentName: "default",
  defaultMetadata: { source: "frontend-smoke" },
  maxEventLogItems: 12,
})
```

发送消息时调用：

```ts
await sendMessage("解释当前链路", {
  files: ["file_abc123def456"],
  metadata: { source: "chat-ui" },
})
```

`files` 仍然是模块 22/23 定下来的上传文件 ID 列表。hook 不直接读文件，只负责把
这些 ID 放进后端 chat stream 请求。

## 它输出什么数据？

`useChatStream` 返回：

```ts
{
  thread,
  messages,
  events,
  isStreaming,
  error,
  sendMessage,
  startNewThread,
  cancelStream,
  clearError,
}
```

其中 `messages` 是 UI 可直接渲染的消息状态：

```ts
type ChatUiMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  status: "streaming" | "done" | "error" | "cancelled"
  runId?: string
}
```

`events` 保留最近若干条 SlotFlow 业务 SSE 事件，方便 smoke 页面和后续调试面板观察
`run.prepared`、`message.delta`、`state.snapshot`、`run.finished`、`run.error`。

## 它在链路里的位置是什么？

模块 24 位于前端 API helper 和页面 UI 之间：

```txt
React page / 后续 Chat UI
-> useChatStream
-> createThread / streamThreadRun
-> Next rewrite /api/*
-> FastAPI /api/chat/threads/{thread_id}/runs/stream
-> Business SSE events
-> useChatStream 更新 messages/events
-> React UI 渲染
```

低层 `streamThreadRun` 仍然只负责 fetch、读取 response body、解析 SSE frame。
`useChatStream` 负责把事件解释成前端状态。

## 当前边界

这个模块只做 hook，不做完整聊天产品 UI：

```txt
已做：
  thread 自动创建
  发送 stream
  message.delta 拼接 assistant 文本
  state.snapshot 用最终 assistant 内容校准消息
  run.error -> error 状态
  AbortController cancel -> cancelled 状态
  页面改为消费 hook

未做：
  左侧 thread 历史列表
  文件上传控件
  历史消息恢复
  用户可见的 mode/model 配置面板
```

这些未做项留给模块 25。
