# SlotFlow

[English](./README.md) | 中文

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)

SlotFlow 是一个开源 Agent 工作空间，由 FastAPI 后端、Next.js 聊天界面、可扩展 Skills、本地记忆、MCP 工具、产物面板和专用 Sub-Agent 组成。

它面向本地优先的研究、编码、分析和报告生成流程。Agent 不只是回答问题，还可以检查文件、调用工具、记住有用上下文，并生成可持久保存的输出。

---

## 目录

- [快速开始](#快速开始)
  - [配置](#配置)
  - [运行应用](#运行应用)
  - [本地开发](#本地开发)
- [核心功能](#核心功能)
  - [聊天工作区](#聊天工作区)
  - [模型选择](#模型选择)
  - [Skills 和工具](#skills-和工具)
  - [Sub-Agents](#sub-agents)
  - [产物](#产物)
  - [记忆](#记忆)
  - [MCP Servers](#mcp-servers)
- [项目结构](#项目结构)
- [验证](#验证)
- [安全说明](#安全说明)
- [贡献](#贡献)
- [许可证](#许可证)

## 快速开始

### 配置

克隆仓库并进入项目目录：

```bash
git clone <your-repository-url>
cd SlotFlow
```

在后端环境中设置模型供应商凭据。模型选择由前端在运行时传入；环境变量只负责提供密钥和供应商接口地址。

```bash
# DeepSeek-compatible runtime
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# OpenAI runtime
OPENAI_API_KEY=your-openai-api-key
# OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic runtime
ANTHROPIC_API_KEY=your-anthropic-api-key
# ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
```

可选的本地运行配置：

```bash
SLOTFLOW_CHECKPOINTER_BACKEND=memory
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
SLOTFLOW_WORKSPACE_WRITES_ENABLED=false
SLOTFLOW_NETWORK_ENABLED=true
```

### 运行应用

启动后端：

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

在另一个终端启动前端：

```bash
cd frontend
pnpm install
pnpm dev
```

打开前端：

```text
http://localhost:3000
```

本地浏览器开发时，前端默认调用 `http://127.0.0.1:8000`。如需覆盖：

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://127.0.0.1:8000
```

### 本地开发

推荐使用 WSL 或 Linux 获得更顺畅的开发体验：

```bash
cd ~/code/SlotFlow
```

安装依赖并验证：

```bash
cd backend
uv run pytest -q

cd ../frontend
pnpm install
pnpm typecheck
pnpm build
```

也可以运行仓库级检查：

```bash
make verify
```

## 核心功能

### 聊天工作区

SlotFlow 提供持久化聊天工作区，支持 threads、消息历史、流式回复、文件上传、消息队列、澄清提示、思考输出和任务进度。

### 模型选择

输入框提供两个运行时控制项：

- `mode`: `flash`、`pro` 或 `ultra`
- `model`: 根据已配置的 DeepSeek、OpenAI 或 Anthropic 凭据自动发现

后端通过 `/api/chat/models` 暴露可用模型。每次运行请求都会携带选中的 model 和 mode，因此 `.env` 不负责决定某个对话使用哪个模型。

### Skills 和工具

Skills 是本地能力包，用来告诉 Agent 如何处理特定工作流。SlotFlow 可以在界面中列出、启用、置顶、排序、上传、安装和删除 Skills。

内置工具组包括：

- workspace 列表、读取、目录树和搜索
- 产物列表
- web fetch 和 search
- skill 匹配与安装
- MCP server 管理

### Sub-Agents

在 `ultra` 模式下，SlotFlow 可以把聚焦任务委派给具名 Sub-Agent。当前 profiles 包括：

- `researcher`: 收集来源并跟踪开放问题
- `analyst`: 指标解释和场景推理
- `planner`: 任务拆解和验证计划
- `coder`: 代码库检查和实现说明
- `reviewer`: 风险审查和缺失测试分析
- `writer`: 报告、README 文案和发布说明

主 Agent 可以用 `subagent_list` 查看可用 profiles，再通过 `task_tool` 委派工作。

### 产物

Agent 生成的文件会显示在产物面板中。Markdown 产物会以 Markdown 预览方式渲染，同时仍可通过 workspace API 访问原始文件。

### 记忆

SlotFlow 包含本地长期记忆，可保存事实、偏好、用户画像备注和主题上下文。记忆可以创建、编辑、删除，并通过中间件挂载到 Agent 运行中。

### MCP Servers

MCP servers 可以通过环境 JSON 配置，也可以从界面管理。HTTP MCP servers 可以添加、启用、置顶、排序和删除，不需要重启前端。

## 项目结构

```text
SlotFlow/
  backend/        FastAPI API、聊天运行时、harness、工具、测试
  frontend/       Next.js UI、聊天工作区、产物面板
  docs/           本地说明和架构参考
  Makefile        验证快捷命令
```

## 验证

后端：

```bash
cd backend
uv run pytest
```

前端：

```bash
cd frontend
pnpm typecheck
pnpm build
```

全部检查：

```bash
make verify
```

## 安全说明

SlotFlow 默认面向本地可信环境。将它暴露到局域网或公网前需要谨慎处理。

建议的保护措施：

- 不要把 API keys 提交到 git
- 不要提交 `.env` 文件
- 除非环境可信，否则保持 workspace writes 关闭
- 运行不可信提示词时限制网络访问
- 任何公开部署前都应增加认证
- 对外提供生成产物前先审查内容

## 贡献

外部贡献者通常从 fork 开始：

1. Fork 仓库。
2. 在 fork 中创建 feature branch。
3. 提交聚焦改动并补充测试。
4. 向主仓库打开 pull request。
5. 处理 review 意见，并保持分支同步。

维护者应保护默认分支，要求通过 pull request 合并，并在合并前运行后端和前端检查。

## 许可证

公开发布仓库前请先添加许可证。在此之前，默认保留所有权利。
