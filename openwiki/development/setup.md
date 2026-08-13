---
type: 开发环境
title: SlotFlow 开发环境设置
description: SlotFlow 本地开发环境的一站式搭建指南，涵盖 bootstrap.sh 自动化安装、Makefile 命令、手动配置和常见问题排查。
tags: [development, setup, bootstrap]
openwiki:
  roles: [development, operations]
  source_paths: [bootstrap.sh, Makefile, README.md, README_zh.md]
  validation_commands: [make verify, cd backend && uv run pytest -q, cd frontend && pnpm test]
---

# SlotFlow 开发环境设置

## 环境要求

推荐平台：Linux 或 WSL2。核心依赖如下：

- Python 3.12 或 3.13
- Node.js 22+（`bootstrap.sh` 默认目标版本）
- pnpm 10.26.2（版本锁定于 `frontend/package.json`）
- `make`、`curl`、`git`
- Docker Engine（用于代码执行沙箱）
- `ffmpeg` 和 ExifTool（用于 MarkItDown 音频/元数据转换，`bootstrap.sh` 会自动安装）

## 快速开始

```bash
git clone <your-repository-url>
cd SlotFlow
./bootstrap.sh
```

`bootstrap.sh` 会自动完成以下操作：
1. 安装/校验系统依赖（`make`、`curl`、`git`、Python 构建工具、`fuser`）
2. 安装 MarkItDown 的 ffmpeg/ExifTool 系统辅助工具
3. 安装 `uv`（Python 包管理器）
4. 安装 Node.js 及 pnpm
5. 安装 Agent Reach（可配置的 Git 源）
6. 运行 `uv sync` 安装 Python 依赖
7. 运行 `pnpm install --frozen-lockfile` 安装前端依赖
8. 安装 Playwright Chromium 浏览器
9. 复制 `backend/.env_example` 到 `backend/.env`（仅当 `.env` 不存在时）
10. 准备 Docker 沙箱：安装 Docker Engine、配置用户组、拉取沙箱镜像

### 跳过可选组件

| 环境变量 | 作用 |
|----------|------|
| `SLOTFLOW_SKIP_SYSTEM_PACKAGES=1` | 跳过 OS 软件包安装 |
| `SLOTFLOW_SKIP_AGENT_REACH=1` | 跳过 Agent Reach 安装 |
| `SLOTFLOW_SKIP_PLAYWRIGHT_BROWSER=1` | 跳过 Chromium 下载 |
| `SLOTFLOW_SKIP_DOCKER=1` | 跳过所有 Docker 配置 |

## 配置模型供应商

编辑 `backend/.env`，至少填入一个模型供应商的 API Key：

```bash
nano backend/.env
```

支持以下供应商（通过环境变量配置）：

- **DeepSeek**：`DEEPSEEK_API_KEY`
- **OpenAI**：`OPENAI_API_KEY`
- **Anthropic**：`ANTHROPIC_API_KEY`
- **自定义 OpenAI-compatible relay**：`CUSTOM_OPENAI_API_KEY` + `CUSTOM_OPENAI_BASE_URL`

详见 [架构 - 模型供应商](../architecture/model-providers.md)。

## 启动开发服务器

```bash
make dev
```

这将同时启动：
- 后端 FastAPI 服务器：`http://127.0.0.1:8000`
- 前端 Next.js 开发服务器：`http://localhost:3000`

停止服务器：

```bash
make kill
```

## 验证命令

| 命令 | 说明 |
|------|------|
| `make test-backend` | 运行后端测试（`cd backend && uv run pytest -q`） |
| `make test-frontend` | 运行前端测试（`cd frontend && pnpm test`） |
| `make typecheck-frontend` | 前端 TypeScript 类型检查 |
| `make dead-code-frontend` | 前端死代码检查（Knip） |
| `make build-frontend` | 前端生产构建 |
| `make verify` | 完整验证：后端测试 + 前端测试 + 类型检查 + 死代码检查 + 构建 |

### 日常开发推荐

```bash
make test-backend    # 修改后端代码后
make typecheck-frontend  # 修改前端代码后
make verify          # 提交前完整检查
```

## 关键约定与不变性条件

### 异步路由边界

FastAPI 端点保持 `async`，但任何可能阻塞的本地文件系统/子进程操作必须通过 `run_in_threadpool` 分发：

- 上传持久化：`uploads/routes.py` → `SlotFlowUploadStore.save_upload`
- 产物目录操作：`workspace/routes.py` → `SlotFlowWorkspace`
- Skills 文件系统操作：`skills/routes.py`
- 聊天和记忆 SQLite 存储：同步内部实现，异步路由通过 `run_in_threadpool` 调用
- 图节点中的记忆检索/保存：使用 `asyncio.to_thread`
- 模型工具：同步 `.invoke()` 用于测试/脚本，异步 `StructuredTool` 协程通过 `asyncio.to_thread` 分发阻塞操作

### 供应商/模型边界

`chat/litellm_provider.py` 是唯一的供应商/模型目录边界。模型选择通过 `provider/model` 格式的命名空间 ID 进行，无需额外的 SlotFlow 供应商映射。

### 文档同步

- `README.md` / `README_zh.md`：入门文档，其中的 bootstrap.sh 和 Makefile 部分必须与实际文件保持一致
- `AGENTS.md`：每次代码变更必须同步更新，记录行为、架构、约定或命令的变化

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| Docker 权限被拒绝 | 运行 `bootstrap.sh` 后需重新登录，使 docker 组成员身份生效 |
| 前端依赖安装失败 | 确认 pnpm 版本为 10.26.2：`pnpm --version` |
| 后端启动失败 | 检查 `backend/.env` 是否已配置至少一个 API Key |
| 端口被占用 | 运行 `make kill` 释放 3000 和 8000 端口 |