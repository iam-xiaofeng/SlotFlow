# 模块 25：shadcn/ui 聊天界面和会话历史

## 这个模块解决什么问题？

模块 24 已经把前端流式聊天逻辑抽成了 `useChatStream` hook，但页面还只是偏 smoke
test 的状态。模块 25 把它升级成第一版可用的聊天界面：

```txt
shadcn/ui Sidebar
-> 会话历史列表
-> ChatGPT 风格消息区
-> 文件上传 composer
-> Markdown/KaTeX 公式渲染
-> 发送/停止/错误状态
```

这一层的重点不是换皮，而是把模块 22-24 做出的后端能力真正接到用户可操作的 UI 上。

## 它接收什么输入？

用户在界面里产生这些输入：

```txt
新聊天
选择历史会话
搜索聊天标题
输入消息文本
上传一个或多个文件
点击发送
点击停止
打开 Skills / MCP / 产物 / 更多 下拉入口
```

前端组件实际调用的接口仍然是前面模块已经定义好的 API：

```txt
GET  /api/chat/threads
POST /api/chat/threads
GET  /api/chat/threads/{thread_id}/messages
POST /api/chat/threads/{thread_id}/runs/stream
POST /api/uploads
```

发送消息时，文件仍然只传 `file_id`：

```ts
await sendMessage(text, {
  files: currentAttachments.map((file) => file.id),
  metadata: {
    source: "chat-ui",
    uploaded_file_count: currentAttachments.length,
    uploaded_files: currentAttachments.map((file) => ({
      id: file.id,
      filename: file.filename,
      content_type: file.content_type,
      size_bytes: file.size_bytes,
    })),
  },
})
```

这里 `uploaded_files` 放进 metadata，是为了让前端刚发送出去的用户消息可以立即显示
附件卡片；后端模块 23 也会把真实上传文件元数据保存到用户消息 metadata。

## 它输出什么数据？

模块 25 的输出主要是用户可见 UI 状态：

```txt
左侧 Sidebar:
  新聊天
  Skills / MCP / 产物 / 更多
  搜索聊天
  刚刚分组下的历史会话

主聊天区:
  用户消息气泡
  assistant Markdown 内容
  KaTeX 公式
  上传文件附件卡片
  streaming 时的停止按钮
  错误提示

composer:
  自动增高输入框
  文件上传 badge
  发送后清空附件
```

`messages` 的来源仍然是模块 24 的 `useChatStream`：

```txt
useChatStream.messages
-> MessageBubble
-> MarkdownContent / MessageAttachments
-> React render
```

## 它在链路里的位置是什么？

模块 25 位于完整链路的最前端和最后端：

```txt
用户输入
-> ChatApp composer
-> useChatStream.sendMessage
-> Next rewrite /api/*
-> FastAPI chat stream
-> AgentAdapter business SSE
-> useChatStream 更新 messages
-> ChatApp 渲染消息 / 公式 / 附件
```

它不直接实现 SSE 解析，也不直接访问后端数据库。模块 25 的职责是消费前面模块暴露的
前端 hook 和 API helper，把状态组织成一个可用的聊天界面。

## 主要代码

```txt
frontend/src/components/chat/chat-app.tsx
frontend/src/hooks/use-chat-stream.ts
frontend/src/lib/chat-stream.ts
frontend/src/app/page.tsx
frontend/src/app/layout.tsx
frontend/src/app/globals.css
frontend/src/components/ui/*
frontend/components.json
frontend/package.json
```

其中 `chat-app.tsx` 是模块 25 的主文件，内部拆成几类组件：

```txt
ChatApp
  页面状态、thread bootstrap、发送消息、上传文件。

ThreadSidebar
  左侧 shadcn Sidebar，包含新聊天、搜索、历史和占位功能入口。

ContextPickerMenu
  Skills / MCP / 产物的下拉选择入口。
  当前只占位，后端能力后续模块再实现。

ThreadHistory
  读取 thread 列表，按“刚刚”分组显示会话。

ComposerTools / ComposerActions
  文件上传菜单、语音占位、发送、停止。

MessageBubble
  区分 user / assistant 消息外观。

MarkdownContent
  assistant Markdown + 数学公式渲染。

MessageAttachments
  用户消息上方的文件附件卡片。
```

## shadcn/ui 这一层怎么用

模块 25 使用 shadcn/ui 的源码式组件，而不是只安装一个黑盒 UI 库。

已经加入的核心组件包括：

```txt
button
sidebar
dropdown-menu
scroll-area
textarea
input
badge
avatar
sheet
tooltip
sonner
skeleton
separator
```

左侧边栏使用官方 `SidebarProvider / Sidebar / SidebarInset` 结构：

```tsx
<SidebarProvider>
  <Sidebar collapsible="icon">
    <ThreadSidebar />
  </Sidebar>

  <SidebarInset>
    <Chat surface />
  </SidebarInset>
</SidebarProvider>
```

这样收起侧边栏时，左边会保留一列图标，而不是完全消失。

## 会话历史怎么流动

页面首次加载：

```txt
ChatApp mount
-> listThreads()
-> setThreads(nextThreads)
-> loadThread(nextThreads[0])
-> listThreadMessages(thread_id)
-> setMessages(storedMessages)
```

用户点击历史会话：

```txt
SidebarMenuButton click
-> handleSelectThread(thread)
-> useChatStream.loadThread(thread)
-> listThreadMessages(thread.id)
-> messageRecordToUiMessage
-> MessageBubble render
```

用户新建聊天：

```txt
点击“新聊天”
-> resetThread()
-> thread = null
-> messages = []
-> attachments = []
```

## 文件上传和消息附件

上传文件时：

```txt
input[type=file]
-> uploadFile(file)
-> POST /api/uploads
-> UploadedFileRecord
-> setAttachments([...])
```

发送消息时：

```txt
currentAttachments = attachments
setInput("")
setAttachments([])
sendMessage(text, {
  files: currentAttachments.map(file => file.id),
  metadata: { uploaded_files: currentAttachments }
})
```

这里先清空 composer 附件，是为了避免用户以为同一批文件还会自动附加到下一条消息。
如果消息没有被 hook 接受，会恢复：

```txt
setInput(text)
setAttachments(currentAttachments)
```

用户消息渲染时：

```txt
message.metadata.uploaded_files
-> getMessageFiles(message)
-> MessageAttachments
-> 文件卡片显示在用户消息上方
```

## 公式为什么需要额外处理

Markdown 数学公式标准写法通常是：

```txt
inline: \( a^2 + b^2 = c^2 \)
block:  \[ ... \]
block:  $$ ... $$
```

但模型有时会输出这种格式：

```txt
[ \begin{aligned} ... \end{aligned} ]
```

这对 Markdown 来说只是普通方括号文本，不会自动进入 KaTeX。

所以模块 25 在渲染前做了一步规范化：

```txt
\[ ... \]                      -> $$ ... $$
\( ... \)                      -> $ ... $
[ \begin{aligned} ... ]         -> $$ \begin{aligned} ... $$
[ \begin{xxx} ... \end{xxx} ]   -> $$ ... $$
```

然后再进入：

```txt
ReactMarkdown
-> remark-gfm
-> remark-math
-> rehype-katex
-> KaTeX HTML
```

相关样式在：

```txt
frontend/src/app/globals.css
  @import "katex/dist/katex.min.css";
  .slotflow-markdown { ... }
```

## 停止按钮做了什么

streaming 时 composer 的发送按钮会切换成停止按钮：

```txt
isStreaming = true
-> 显示停止按钮
-> onClick cancelStream()
-> AbortController.abort()
-> assistant message status = cancelled
```

停止按钮只是取消前端当前 fetch stream。后端是否已经执行到某个不可取消步骤，取决于后端
adapter 和真实 agent runtime 的取消能力。

## 当前占位功能

这些入口已经出现在 UI 中，但后端还没有实现完整能力：

```txt
Skills
  未来支持选择已有 skill、从路径添加、拖拽添加。

MCP
  未来支持通过 HTTP 添加 MCP server、管理连接。

产物
  未来显示模型生成的文件、报告、图表等 artifacts。

右上角用户头像
  当前只是用户菜单占位。
```

这些入口先放在界面结构里，是为了后续模块接后端时不用再重排整体布局。

## 当前验证

前端类型检查：

```bash
cd /home/dell/code/SlotFlow/frontend
pnpm typecheck
```

生产构建：

```bash
pnpm build
```

注意：当前项目使用 `next/font/google` 拉取 Inter 字体。如果本机网络无法访问
Google Fonts，`pnpm build` 可能会失败在字体下载阶段。这不是模块 25 的聊天 UI
逻辑错误。要彻底消除这个外部依赖，后续可以把字体改成本地字体或普通 CSS font stack。

## 这一模块不做什么

模块 25 明确不做：

```txt
不实现真实用户系统
不实现 Skills 后端管理
不实现 MCP HTTP 配置保存
不实现 artifacts 数据模型
不实现文件预览/下载
不实现拖拽上传
不做复杂 Markdown 编辑器
不保证所有模型生成的 LaTeX 都能完美渲染
```

它只完成第一版可用聊天界面，并把模块 22-24 的上传、stream、历史消息恢复能力接到
一个 shadcn/ui 页面里。
