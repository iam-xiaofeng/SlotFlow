---
type: 架构概览
title: SlotFlow 架构概览
description: SlotFlow 系统分层架构、核心组件关系、端到端请求数据流，以及关键设计边界。
tags: [architecture, system-design]
openwiki:
  roles: [architecture]
  source_paths: [AGENTS.md, docs/architecture.md, backend/app/main.py]
---

# SlotFlow 架构概览

## 系统分层

SlotFlow 采用前后端分离架构，后端为 FastAPI + LangGraph，前端为 Next.js。

```
┌─────────────────────────────────────────────────┐
│                   前端 (Next.js)                  │
│  Chat UI · Workspace Panel · Model Selector     │
│  hooks/use-chat-stream.ts · lib/chat-stream.ts  │
└────────────────────┬────────────────────────────┘
                     │ SSE (Server-Sent Events)
┌────────────────────┴────────────────────────────┐
│              后端 API 层 (FastAPI)               │
│  chat/routes.py · SSE 端点 · Pydantic 模型      │
│  chat/run_config.py · chat/repository.py        │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────┐
│          运行时适配层 (Runtime Adapter)          │
│  chat/runtime/adapter.py                        │
│  chat/agent_adapter/projections.py              │
│  LangGraph v3 投影 → SlotFlow AgentEvent        │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────┐
│            Harness 引擎 (LangGraph)              │
│  harness/graph.py · harness/builder.py          │
│  harness/steps/ · harness/tools/                │
│  harness/sandbox/ · harness/skills/             │
│  harness/memory/ · harness/subagents/           │
└─────────────────────────────────────────────────┘
```

## 端到端请求流程

一次聊天请求的完整路径：

1. **前端发起请求** — `components/chat/chat-app.tsx` + `hooks/use-chat-stream.ts` 向聊天流端点 POST `ChatStreamRequest`（包含消息、模型名称、供应商、模式、思考开关、文件）。

2. **路由层处理** — `chat/routes.py` 持久化用户消息，然后 `chat/run_config.build_run_config` 将请求转换为 `RunConfigBundle = {config, context}`：
   - `config["configurable"]["thread_id"]` — LangGraph 多轮检查点状态键
   - `RunContext` — SlotFlow 业务开关：`model_name`、`model_provider`、`mode`、`thinking_enabled`、plan/subagent 标志、文件

3. **运行时适配** — `chat/runtime/adapter.py (RuntimeBackedAgentAdapter)` 通过 `runtime/models.create_chat_model`（由 `RunContext.model_provider` 路由）构建一个 `ChatLiteLLM` 模型，并通过 `harness/builder.build_slotflow_harness_graph` → `harness/graph.build_slotflow_graph` 组装图。

4. **图执行与流式输出** — 图使用 LangGraph v3 投影协议进行流式输出；每个条目由 `chat/agent_adapter/projections.py` 规范化为 SlotFlow `AgentEvent`。

5. **SSE 编码** — `chat/sse.py` 将事件编码为 SSE；前端消费并渲染消息、推理、待办事项、澄清选择器和工作区文件。

## 两个关键设计边界

### RunContext vs config.configurable

- **RunContext**：SlotFlow 业务开关（模型选择、模式、思考开关）
- **config.configurable**：LangGraph 运行时键（`thread_id` 用于多轮检查点状态）

这两个边界的分离是设计的核心：业务语义由 SlotFlow 管理，LangGraph 运行时状态由框架管理。

### LiteLLM 模型边界

`chat/litellm_provider.py` 是唯一的供应商/模型目录边界。供应商/版本差异在此处归一化，然后投影层将 LangGraph 消息映射为干净的 `AgentEvent`。

## 图拓扑

LangGraph 原生 StateGraph（显式节点 + 边）：

```
START → prepare → triage_gate → pre_model → SlotFlowSummarizationMiddleware
                                              → agent → post_model → route
                                                   ├─ tools → pre_model   (ReAct 循环)
                                                   ├─ pre_model           (待办事项执行重试)
                                                   └─ finalize → END
```

各节点职责详见 [Harness 引擎](harness.md)。

## 核心组件关系

| 组件 | 职责 | 详情页 |
|------|------|--------|
| Harness 引擎 | Agent 图编排、工具调度、记忆、子代理 | [harness.md](harness.md) |
| 工具系统 | 工具注册、Docker 沙箱、安全包装 | [tool-system.md](tool-system.md) |
| 聊天 API | SSE 流、投影层、AgentEvent | [chat-api.md](chat-api.md) |
| 模型供应商 | LiteLLM 集成、运行时模型发现 | [model-providers.md](model-providers.md) |

## 开发与操作

- [开发环境设置](../development/setup.md) — bootstrap.sh、Makefile、验证命令
- [扩展指南](../development/extending.md) — 添加工具、技能、子代理