---
type: 架构组件
title: 聊天 API 与流式输出
description: SlotFlow 聊天 API 的 SSE 流式端点、LangGraph v3 投影协议、AgentEvent 事件类型，以及前端消费流程。
tags: [architecture, chat-api, sse, streaming]
openwiki:
  roles: [architecture, integration]
  source_paths:
    - backend/app/chat/routes.py
    - backend/app/chat/agent_adapter/projections.py
    - backend/app/chat/sse.py
    - frontend/src/hooks/use-chat-stream.ts
    - frontend/src/lib/chat-stream.ts
  symbols:
    - AgentEvent
    - ChatStreamRequest
    - RunConfigBundle
    - projections.py
  test_paths: [backend/tests/]
  validation_commands:
    - cd backend && uv run pytest -q
---

# 聊天 API 与流式输出

聊天 API 是 SlotFlow 前端与后端之间的主要通信通道。它基于 Server-Sent Events (SSE) 实现流式响应，使用 LangGraph v3 投影协议将 Agent 内部状态映射为前端可消费的事件。

## 请求生命周期

```
前端 POST /chat/stream
  │ ChatStreamRequest { message, model_name, provider, mode, thinking_enabled, files }
  ▼
chat/routes.py
  ├─ 持久化用户消息 (chat/repository.py)
  ├─ build_run_config() → RunConfigBundle
  │   ├─ config["configurable"]["thread_id"]
  │   └─ RunContext { model_name, model_provider, mode, thinking_enabled, ... }
  ▼
RuntimeBackedAgentAdapter (chat/runtime/adapter.py)
  ├─ create_chat_model() → ChatLiteLLM
  ├─ build_slotflow_harness_graph() → 编译后的 LangGraph 图
  ▼
图流式执行 (LangGraph v3 投影协议)
  │ 原始 LangGraph 事件流
  ▼
projections.py
  │ 归一化为 SlotFlow AgentEvent
  ▼
chat/sse.py
  │ 编码为 SSE 格式
  ▼
前端 use-chat-stream.ts
  │ 解析 SSE 事件 → UI 渲染
```

## 核心数据模型

### ChatStreamRequest

前端发送的请求体，包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | `str` | 用户消息文本 |
| `model_name` | `str` | 选择的模型名称 |
| `provider` | `str` | 模型供应商 |
| `mode` | `str` | 运行模式 |
| `thinking_enabled` | `bool` | 是否启用推理流 |
| `files` | `list` | 上传的文件列表 |

### RunConfigBundle

由 `chat/run_config.build_run_config` 生成，包含两个部分：

- **`config`**：LangGraph 运行时配置，`configurable.thread_id` 用于多轮对话检查点
- **`context` (RunContext)**：SlotFlow 业务开关，包含模型选择、模式、思考开关等

### AgentEvent

投影层输出的标准化事件，所有事件类型：

| 事件类型 | 通道 | 说明 |
|----------|------|------|
| `message.delta` | `reasoning` | 模型推理过程（thinking tokens） |
| `message.delta` | `content` | 模型文本输出 |
| `state.snapshot` | — | 图状态快照 |
| `tool.delta` | — | 工具调用增量更新 |
| `tool.status` | — | 工具执行状态（挂载在消息子流 tool_calls 投影上） |
| `clarification.requested` | — | 需要用户澄清 |
| `todo.updated` | — | 待办事项列表更新 |
| `run.*` | — | 运行生命周期事件（开始、完成、错误） |

## 投影层

`chat/agent_adapter/projections.py` 是 LangGraph 原始事件到 SlotFlow `AgentEvent` 的转换层。

**职责：**

1. **归一化供应商差异**：不同模型供应商的流式输出格式不同，投影层将其统一为 `AgentEvent`
2. **分离 reasoning 和 content**：将模型输出按通道分离，前端可独立渲染推理过程和最终答案
3. **过滤内部事件**：如 `SlotFlowSummarizationMiddleware` 的内部摘要流，投影层按节点名称过滤
4. **工具状态投影**：将 `tool.status` 挂载到消息子流的 `tool_calls` 投影上，使所有工具执行对前端可见

## SSE 编码

`chat/sse.py` 负责将 `AgentEvent` 编码为 SSE 格式。

SSE 事件格式：
```
event: message.delta
data: {"channel": "content", "delta": "Hello"}

event: tool.status
data: {"tool_name": "read_file", "status": "running"}

event: run.complete
data: {}
```

## 前端消费

### API 客户端

`frontend/src/lib/chat-stream.ts` 封装了 SSE 连接逻辑：

- 使用 `EventSource` 或 `fetch` 流式读取
- 解析 SSE 事件并转换为 TypeScript 类型
- 处理重连和错误

### React Hooks

`frontend/src/hooks/use-chat-stream.ts` 提供聊天流的状态管理：

- 管理消息列表状态
- 累积 `message.delta` 事件为完整消息
- 处理 `clarification.requested` 展示澄清 UI
- 更新 `todo.updated` 待办事项列表
- 渲染 `tool.status` 工具执行状态

## 消息持久化

聊天消息通过 `chat/repository.py` 持久化到 SQLite：

- **用户消息**：请求到达时立即持久化
- **Agent 消息**：流式输出完成后持久化
- **工具消息**：工具执行完成后持久化

所有数据库操作通过 `run_in_threadpool` 在异步路由中执行，避免阻塞事件循环。

## 不变性条件

- **消息顺序**：SSE 事件顺序与图执行顺序严格一致
- **通道隔离**：`reasoning` 和 `content` 通道的事件分别累积，前端可独立控制显示
- **工具状态可见性**：所有工具执行状态通过 `tool.status` 事件对前端可见
- **连接断开处理**：SSE 连接断开时，前端保留已接收的消息状态

## 变更导航

| 变更意图 | 入口文件 | 关键符号 | 聚焦测试 |
|----------|----------|----------|----------|
| 添加新事件类型 | `backend/app/chat/agent_adapter/projections.py` | `AgentEvent` | `backend/tests/` |
| 修改 SSE 格式 | `backend/app/chat/sse.py` | SSE 编码函数 | `backend/tests/` |
| 调整前端流处理 | `frontend/src/hooks/use-chat-stream.ts` | React hooks | `frontend/src/` |
| 修改持久化逻辑 | `backend/app/chat/repository.py` | SQLite 操作 | `backend/tests/` |

**最小验证命令：** `cd backend && uv run pytest -q`（后端）；`cd frontend && pnpm test`（前端）