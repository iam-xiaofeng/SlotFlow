---
type: 架构组件
title: Harness 引擎
description: SlotFlow 的 LangGraph Agent 图编排引擎，覆盖图拓扑、节点职责、步骤函数、工具调度、记忆系统和子代理委派机制。
tags: [architecture, harness, langgraph, agent]
openwiki:
  roles: [architecture, domain]
  source_paths:
    - backend/app/harness/graph.py
    - backend/app/harness/builder.py
    - backend/app/harness/steps/
    - backend/app/harness/tools/registry.py
  symbols:
    - build_slotflow_graph
    - build_slotflow_harness_graph
    - SlotFlowState
    - ToolRegistry
  test_paths: [backend/tests/]
  validation_commands:
    - cd backend && uv run pytest -q
---

# Harness 引擎

Harness 是 SlotFlow 的 Agent 运行时核心，基于 LangGraph 原生 `StateGraph`（显式节点 + 边）构建。它不再使用 LangChain 的 `create_agent` 或 `AgentMiddleware`；每个原中间件逻辑以无状态纯函数形式存在于 `harness/steps/` 中，由节点调用，顺序由边固定。

## 图拓扑

```
START → prepare → triage_gate → pre_model → SlotFlowSummarizationMiddleware
                                              → agent → post_model → route
                                                   ├─ tools → pre_model   (ReAct 循环)
                                                   ├─ pre_model           (todo 强制执行重试)
                                                   └─ finalize → END
```

## 节点职责

### prepare（每轮一次，before_agent）

运行时摘要、上传处理、Skills 预检、长期记忆检索、产物基线。

源码：`harness/steps/` 中的 prepare 步骤函数。

**关键行为：**
- 加载当前会话的 LangGraph 检查点状态
- 运行 Skills 预检，确定哪些 Skills 与当前查询相关
- 从长期记忆中检索相关上下文
- 处理用户上传的文件

### triage_gate（仅首步，pro/ultra 模式）

对用户查询进行意图分类，必要时通过 `interrupt()` 发起澄清；恢复时将答案逐字注入为 `HumanMessage`。

**关键行为：**
- 仅在使用 pro 或 ultra 模式时激活
- 调用 LLM 判断查询是否需要澄清
- 如需澄清，暂停图执行并等待用户响应
- 用户响应后，将其作为 `HumanMessage` 注入消息历史

源码：`harness/clarification.py`

### pre_model（每步）

动态 todo 状态提醒、悬空工具调用修复、Skills 预检系统上下文注入、长期记忆系统提示注入。

**关键行为：**
- 在每次 LLM 调用前更新系统提示
- 注入当前 todo 列表状态
- 修复悬空的工具调用（如上一轮未完成的工具执行）
- 注入 Skills 相关的上下文信息

### SlotFlowSummarizationMiddleware

当 token 超过阈值时压缩历史消息。作为独立节点运行，以便投影层可按节点名称过滤其内部摘要流。

**关键行为：**
- 监控对话历史的 token 使用量
- 超过阈值时自动生成摘要
- 将摘要替换历史消息以控制上下文窗口大小

### agent

执行 `model.bind_tools(tools)` 调用；读取 `llm_input_messages` + `system_prompt`。

**关键行为：**
- 将可用工具绑定到 LLM 模型
- 发送消息历史给模型
- 接收模型的响应（文本或工具调用）

### post_model

todo 并行调用守卫 + 动态 todo 强制执行，然后对 `task_tool` 进行子代理并发上限控制。

**关键行为：**
- 确保 todo 列表中标记为并行的任务不会同时执行
- 限制并发子代理数量
- 验证 todo 执行顺序是否符合要求

### route

根据 `tools_condition` 决定下一步：
- 如有工具调用 → 进入 `tools` 节点，然后回到 `pre_model`（ReAct 循环）
- 如待办事项未完成 → 回到 `pre_model` 强制执行
- 否则 → 进入 `finalize`

### tools (ToolNode + SlotFlow 安全包装)

执行模型请求的工具调用。SlotFlow 的工具安全包装器在此节点中运行，确保所有工具调用经过沙箱隔离和权限检查。

详见 [工具系统](tool-system.md)。

### finalize（每轮一次，after_agent）

产物新条目记录、长期记忆显式保存 + 后台 LLM 提取。

**关键行为：**
- 将 Agent 生成的产物（文件、图片等）记录到工作区
- 将重要信息保存到长期记忆
- 触发后台 LLM 提取关键信息用于未来检索

## 消息投影

图使用 LangGraph v3 投影协议进行流式输出。每个条目由 `chat/agent_adapter/projections.py` 规范化为 SlotFlow `AgentEvent`，支持以下通道：

| 通道 | 事件类型 | 说明 |
|------|----------|------|
| `reasoning` | `message.delta` | 模型推理过程（thinking） |
| `content` | `message.delta` | 模型文本输出 |
| — | `state.snapshot` | 图状态快照 |
| — | `tool.delta` | 工具调用增量更新 |
| — | `tool.status` | 工具执行状态（挂载在消息子流 tool_calls 投影上） |
| — | `clarification.requested` | 澄清请求 |
| — | `todo.updated` | 待办事项更新 |
| — | `run.*` | 运行生命周期事件 |

详见 [聊天 API](chat-api.md)。

## 关键设计边界

### RunContext vs config.configurable

- **RunContext**：SlotFlow 业务开关（`model_name`、`model_provider`、`mode`、`thinking_enabled`、plan/subagent 标志、文件）
- **config.configurable**：LangGraph 运行时键（`thread_id` 用于多轮检查点状态）

这两个边界分离了业务语义和框架运行时状态。

### LiteLLM 模型边界

所有模型调用经过 `chat/litellm_provider.py` 归一化。供应商/版本差异在此处处理，投影层再将 LangGraph 消息映射为干净的 `AgentEvent`。

详见 [模型供应商](model-providers.md)。

## 不变性条件

- **消息历史不可变**：图节点不直接修改消息历史，而是生成新状态
- **检查点持久化**：每步执行后自动保存状态到 SQLite，支持断点续传
- **工具调用原子性**：ToolNode 中的每个工具调用是原子的，失败时回滚状态
- **澄清中断安全**：`triage_gate` 使用 LangGraph `interrupt()` 暂停执行，用户响应后从检查点恢复

## 变更导航

| 变更意图 | 入口文件 | 关键符号 | 聚焦测试 |
|----------|----------|----------|----------|
| 修改图拓扑 | `backend/app/harness/graph.py` | `build_slotflow_graph` | `backend/tests/` |
| 添加新步骤函数 | `backend/app/harness/steps/` | 步骤纯函数 | `backend/tests/` |
| 调整澄清逻辑 | `backend/app/harness/clarification.py` | `triage_gate` 相关函数 | `backend/tests/` |
| 修改摘要策略 | `backend/app/harness/graph.py` | `SlotFlowSummarizationMiddleware` 节点 | `backend/tests/` |

**最小验证命令：** `cd backend && uv run pytest -q`