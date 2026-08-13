---
type: 入口导航
title: SlotFlow 快速开始
description: SlotFlow 知识库入口，提供项目概述、任务路由表和主要文档导航，帮助开发者快速定位代码入口、测试和验证命令。
tags: [quickstart, navigation]
openwiki:
  roles: [architecture, repository]
  source_paths: [README.md, AGENTS.md, Makefile]
---

# SlotFlow 快速开始

SlotFlow 是一个本地优先、可扩展的 AI Agent 工作空间。FastAPI + LangGraph 后端驱动 Next.js 聊天界面，支持运行时模型选择、可见推理流、Skills、MCP 工具、本地记忆、产物、Docker 隔离代码执行和聚焦任务的 Sub-Agent。

## 知识库导航

| 区域 | 页面 | 说明 |
|------|------|------|
| 架构 | [architecture/overview.md](architecture/overview.md) | 系统分层与数据流 |
| Harness 引擎 | [architecture/harness.md](architecture/harness.md) | LangGraph 图拓扑、节点/边 |
| 工具系统 | [architecture/tool-system.md](architecture/tool-system.md) | 工具注册、沙箱执行 |
| 聊天 API | [architecture/chat-api.md](architecture/chat-api.md) | SSE 流、投影层 |
| 模型供应商 | [architecture/model-providers.md](architecture/model-providers.md) | LiteLLM 集成、模型发现 |
| 开发环境 | [development/setup.md](development/setup.md) | bootstrap.sh、验证命令 |
| 扩展指南 | [development/extending.md](development/extending.md) | 添加工具、技能、子代理 |

## 任务路由表

| 变更意图 | 相关页面 | 源码入口 | 关键符号 | 聚焦测试 | 最小验证命令 |
|----------|----------|----------|----------|----------|-------------|
| 添加新工具 | [architecture/tool-system.md](architecture/tool-system.md) | `backend/app/harness/tools/` | `ToolRegistry`, `BaseTool` | `backend/tests/` | `cd backend && uv run pytest -q` |
| 修改 Agent 图拓扑 | [architecture/harness.md](architecture/harness.md) | `backend/app/harness/graph.py` | `build_slotflow_graph`, `SlotFlowState` | `backend/tests/` | `cd backend && uv run pytest -q` |
| 调整聊天流式输出 | [architecture/chat-api.md](architecture/chat-api.md) | `backend/app/chat/routes.py` | `AgentEvent`, `projections.py` | `backend/tests/` | `cd backend && uv run pytest -q` |
| 接入新模型供应商 | [architecture/model-providers.md](architecture/model-providers.md) | `backend/app/chat/litellm_provider.py` | `configured_native_provider_names`, `agent_models_for_provider` | `backend/tests/` | `cd backend && uv run pytest -q` |
| 添加前端 UI 组件 | [development/extending.md](development/extending.md) | `frontend/src/components/` | React hooks in `frontend/src/hooks/` | `frontend/src/` | `cd frontend && pnpm test` |
| 修改记忆系统 | [architecture/harness.md](architecture/harness.md) | `backend/app/harness/memory/` | `MemoryStore`, `memory_routes.py` | `backend/tests/` | `cd backend && uv run pytest -q` |
| 整体验证 | [development/setup.md](development/setup.md) | `Makefile` | `verify` target | 全部 | `make verify` |

## 开发环境

```bash
./bootstrap.sh              # 首次运行：安装系统依赖、Python/Node、Docker
make dev                    # 启动前后端开发服务器
make verify                 # 完整验证：后端测试 + 前端测试 + 类型检查 + 死代码检查 + 构建
```

详见 [development/setup.md](development/setup.md)。

## 待办事项

以下领域有证据支持但尚未完整文档化：

| 领域 | 源码锚点 | 延期原因 |
|------|----------|----------|
| MCP 服务端实现 | `backend/app/mcp/` | 模块稳定后再文档化 |
| Web 终端功能 | `backend/app/terminal/` | 独立功能模块，优先完成核心路径 |
| Skills 系统详细文档 | `backend/app/harness/skills/` | 内部机制复杂，需进一步深入分析 |
| Sub-Agent 委派机制 | `backend/app/harness/subagents/` | 分层架构细节待梳理 |