# SlotFlow

[English](./README.md) | 中文

[![Python](https://img.shields.io/badge/Python-3.12--3.13-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](./backend)
[![Next.js](https://img.shields.io/badge/Next.js-000000?logo=next.js&logoColor=white)](./frontend)
[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)](./backend/app/harness)

SlotFlow 是一个本地优先、可扩展的 AI Agent 工作空间。FastAPI + LangGraph 后端驱动
Next.js 聊天界面，支持运行时模型选择、可见 reasoning 流、Skills、MCP 工具、本地记忆、
产物、Docker 隔离代码执行，以及聚焦任务的 Sub-Agent。

它面向研究、编码、分析和报告生成工作流。Agent 不只是回答问题，还可以读取文件、调用工具、
记住有用上下文、主动澄清需求，并生成可在工作区面板中预览的持久产物。

---

## 目录

- [你会得到什么](#你会得到什么)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [bootstrap.sh](#bootstrapsh)
- [Makefile 命令](#makefile-命令)
- [手动安装](#手动安装)
- [配置](#配置)
- [运行 SlotFlow](#运行-slotflow)
- [核心功能](#核心功能)
- [架构](#架构)
- [项目结构](#项目结构)
- [验证](#验证)
- [故障排查](#故障排查)
- [安全说明](#安全说明)
- [贡献](#贡献)
- [许可证](#许可证)

## 你会得到什么

- 支持独立可见 reasoning 输出的流式聊天。
- 从已配置供应商动态发现模型，而不是维护硬编码模型列表。
- 支持 DeepSeek、OpenAI、Anthropic，以及自定义 OpenAI-compatible relay。
- Skills、MCP servers、web 工具、workspace 工具、上传和产物预览。
- 用于不可信代码执行的 Docker 隔离 `sandbox_exec`。
- 用于把 Docker 内已生成文件发布到 UI 产物区的 `sandbox_artifact_copy`。
- 长期记忆和本地 SQLite 持久化。
- 面向大任务的分层 Sub-Agent 委派。
- 仓库根目录的 `bootstrap.sh` 和 `Makefile`，方便新 clone 后快速跑起来。

## 环境要求

推荐环境：

- Linux 或 WSL2
- Python 3.12 或 3.13
- Node.js 20+；`bootstrap.sh` 默认目标版本是 Node 22
- pnpm 10.26.2，版本来自 `frontend/package.json`
- `make`、`curl`、`git`
- Docker Engine，用于代码执行工具和 Docker 沙箱产物
- `ffmpeg` 和 ExifTool，用于完整的 MarkItDown 音频/元数据转换（`bootstrap.sh` 会尽可能安装）

`./bootstrap.sh` 可以在常见 Linux 发行版上安装或校验大部分依赖：
`apt`、`dnf`、`yum`、`pacman`、`apk`、`zypper`。它也有基础工具的 Homebrew 路径，
但 Docker 自动配置主要面向 Linux/WSL。

## 快速开始

```bash
git clone <your-repository-url>
cd SlotFlow
./bootstrap.sh
```

然后编辑 `backend/.env`，至少填入一个模型供应商 API key：

```bash
nano backend/.env
```

启动前后端：

```bash
make dev
```

打开：

```text
http://localhost:3000
```

后端运行在 `http://127.0.0.1:8000`。本地浏览器开发时，前端默认调用这个后端地址。

## bootstrap.sh

`./bootstrap.sh` 是新 clone 仓库后的推荐首启路径。

它会执行：

1. 安装或校验仓库和 `Makefile` 需要的系统依赖。
2. 如果缺少 `uv`，自动安装。
3. 安装 Node 和 pnpm，其中 pnpm 版本读取自 `frontend/package.json`。
4. 用 `uv tool` 安装或刷新 Agent Reach，并准备它在宿主机上的基础渠道。
5. 尽可能安装 MarkItDown 的 ffmpeg/ExifTool，再运行 `uv sync` 安装全格式和视觉 OCR 依赖。
6. 在 `frontend/` 安装锁定依赖，在 apt 主机安装 Chromium 共享库，并下载锁定的 Chromium runtime。
7. 仅在 `backend/.env` 不存在时，把 `backend/.env_example` 复制为 `backend/.env`。
8. 尽可能安装、启动并准备 Docker。
9. 尽可能预拉取 Docker 沙箱镜像。

脚本不会覆盖已有的 `backend/.env`。Playwright 官方依赖安装器目前只会在 Debian/Ubuntu
（`apt`）上自动安装共享库；其他发行版会得到明确 warning，可能仍需手动安装 Chromium runtime 库。

常用 bootstrap 参数：

```bash
# 跳过系统包安装。适合依赖已安装，或你不想让脚本执行 sudo/root 包管理命令时使用。
SLOTFLOW_SKIP_SYSTEM_PACKAGES=1 ./bootstrap.sh

# 跳过所有 Docker 配置。应用仍可运行，但 sandbox_exec 需要 Docker 可用后才能工作。
SLOTFLOW_SKIP_DOCKER=1 ./bootstrap.sh

# 分别跳过 Agent Reach 宿主配置或 Playwright Chromium 下载。
SLOTFLOW_SKIP_AGENT_REACH=1 ./bootstrap.sh
SLOTFLOW_SKIP_PLAYWRIGHT_BROWSER=1 ./bootstrap.sh

# 覆盖 uv tool 使用的 Agent Reach Git 源；重新运行 bootstrap 即为更新入口。
SLOTFLOW_AGENT_REACH_SOURCE=git+https://github.com/Panniantong/Agent-Reach.git ./bootstrap.sh

# 覆盖 bootstrap 使用的运行时工具版本。
SLOTFLOW_NODE_VERSION=22 ./bootstrap.sh
SLOTFLOW_PNPM_VERSION=10.26.2 ./bootstrap.sh

# 覆盖 bootstrap 预拉取的 Docker 沙箱镜像。
SLOTFLOW_DOCKER_IMAGE=python:3.12 ./bootstrap.sh

# 仅在直连 Docker Hub 拉取失败后使用的 registry mirrors。
SLOTFLOW_DOCKER_REGISTRY_MIRRORS="https://docker.1ms.run https://docker.m.daocloud.io" ./bootstrap.sh

# 调整等待 Docker daemon 启动的时间。
SLOTFLOW_DOCKER_DAEMON_WAIT_SECONDS=30 ./bootstrap.sh
```

Docker 注意事项：

- 如果 bootstrap 把当前用户加入了 `docker` 组，需要退出并重新登录后，非 sudo Docker 权限才会生效。
- 在没有 systemd 的 WSL 上，bootstrap 可能会向 `/etc/wsl.conf` 写入 `systemd=true`。
  需要从 Windows 执行一次 `wsl --shutdown`，这个设置才会完整生效。
- 如果 Docker 无法自动启动或镜像无法拉取，bootstrap 会带 warning 完成。SlotFlow 会在第一次使用沙箱时重试启动或拉取。

## Makefile 命令

bootstrap 之后，根目录 `Makefile` 是日常开发入口。

```bash
make dev
```

同时启动本地开发前后端：

- 前端：`cd frontend && pnpm dev`
- 后端：`cd backend && uv run uvicorn app.main:app --env-file ./.env --reload`

用 `Ctrl+C` 停止。

```bash
make verify
```

运行完整本地验证：

- 后端测试：`cd backend && uv run pytest -q`
- 前端类型检查：`cd frontend && pnpm typecheck`
- 前端生产构建：`cd frontend && pnpm build`

也可以单独运行：

```bash
make test-backend
make typecheck-frontend
make build-frontend
```

按端口杀掉本地开发服务：

```bash
make kill
```

`make kill` 使用 `fuser` 清理 `3000` 和 `8000` 端口；多数 Linux 发行版中 `fuser`
来自 `psmisc` 包。

## 手动安装

如果你不希望 `bootstrap.sh` 安装系统包，可以走手动路径。

自行安装：

- Python 3.12 或 3.13
- `uv`
- Node.js 20+
- pnpm 10.26.2
- 如果需要 `sandbox_exec`，安装 Docker Engine
- `make`、`curl`、`git`，以及可选的 `fuser`

安装依赖：

```bash
cd backend
uv sync

cd ../frontend
pnpm install --frozen-lockfile

cd ..
```

创建本地后端环境文件：

```bash
cp backend/.env_example backend/.env
```

然后在 `backend/.env` 中至少填入一个供应商 API key。

## 配置

完整配置模板在：

```text
backend/.env_example
```

复制到：

```text
backend/.env
```

`backend/.env` 已被 git ignore，应该放真实密钥。

### 模型供应商

SlotFlow 通过 LiteLLM 检测已配置的原生 provider，并把这些 provider 在 LiteLLM 内置目录中
所有支持 `chat + function calling` 的模型加入选择器；SlotFlow 不维护厂商模型清单。

```bash
# DeepSeek
DEEPSEEK_API_KEY=sk-...
# DEEPSEEK_BASE_URL=https://api.deepseek.com

# OpenAI
OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_BASE_URL=https://api.anthropic.com/v1

# 其他 LiteLLM 原生 provider 使用其标准环境变量，例如：
GEMINI_API_KEY=...
MISTRAL_API_KEY=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1

# 自定义 OpenAI-compatible relay
CUSTOM_BASE_URL=https://your-relay.example.com/v1
CUSTOM_API_KEY=sk-...
CUSTOM_MODELS=claude-sonnet-4,gpt-5,qwen-max
```

重要行为：

- `.env` 不决定某个对话使用哪个模型。
- 每次运行时，前端会把选中的模型和 provider 一起发给后端。
- 所有 provider 都通过 `ChatLiteLLM` 调用；厂商协议、reasoning、tool call 和 usage 归一化由 LiteLLM 负责。
- relay 不支持 `/models` 时，可以用 `CUSTOM_MODELS` 手动列出模型。
- 所有 provider 都使用 LiteLLM 的 Chat Completions 归一化；OpenAI 不再路由到 Responses，使 DeepSeek/Qwen/custom relay 共用一套兼容的传输形状。

### 前端 URL

默认本地设置下，不需要前端 env 文件。前端默认调用：

```text
http://127.0.0.1:8000
```

如需覆盖，可在前端 shell 或 `frontend/.env.local` 中设置：

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL=http://localhost:8000
```

### 存储

通过 `make dev` 运行时，后端相对路径从 `backend/` 目录解析，所以默认本地数据在
`backend/.slotflow/` 下。

常用设置：

```bash
SLOTFLOW_CHAT_SQLITE_PATH=.slotflow/chat.sqlite3
SLOTFLOW_CHECKPOINTER_BACKEND=memory
SLOTFLOW_CHECKPOINTER_SQLITE_PATH=.slotflow/checkpoints.sqlite3
SLOTFLOW_MEMORY_SQLITE_PATH=.slotflow/memory.sqlite3
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
```

### 功能开关

`backend/.env_example` 默认开启大部分功能：

```bash
SLOTFLOW_LONG_TERM_MEMORY_ENABLED=true
SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION=true
SLOTFLOW_TODO_MIDDLEWARE=true
SLOTFLOW_MCP_ENABLED=true
SLOTFLOW_CODE_EXECUTION_ENABLED=true
```

只有在调试具体子系统或受限本地环境中，才建议关闭某个功能。

### Agent Reach 宿主桥接

`bootstrap.sh` 会在宿主机安装 Agent Reach 及其基础上游 CLI。SlotFlow 只向模型提供固定的
只读操作：渠道体检、Exa 全网搜索、Jina 网页阅读、GitHub 搜索和 YouTube 元数据；不会向
模型开放宿主 shell，也不提供安装、配置或远程写操作。桥接不会挂载进 Docker，重新运行
`bootstrap.sh` 即会刷新它。

```bash
SLOTFLOW_AGENT_REACH_ENABLED=true
SLOTFLOW_AGENT_REACH_HOME=~/.agent-reach
SLOTFLOW_AGENT_REACH_TIMEOUT_SECONDS=60
SLOTFLOW_AGENT_REACH_MAX_OUTPUT_BYTES=524288
```

`SLOTFLOW_NETWORK_ENABLED=false` 时桥接也会关闭。需要 Cookie/登录态的可选渠道仍由用户
明确决定，bootstrap 不会自动启用。

### 内置 Playwright MCP

受保护的 `playwright` MCP preset 默认启用。它使用 headless、isolated Chromium；同一次 run
中的 navigate/snapshot/click 共用一个 session，run 结束即关闭，并发对话互不共享。preset 的 cwd
限制在 workspace，不启用可选 vision/PDF/devtools 能力，也不能被用户 HTTP server 覆盖。

```bash
SLOTFLOW_PLAYWRIGHT_MCP_ENABLED=true
SLOTFLOW_PLAYWRIGHT_MCP_ACTION_TIMEOUT_MS=10000
SLOTFLOW_PLAYWRIGHT_MCP_NAVIGATION_TIMEOUT_MS=60000
```

localhost/私网 origin blocklist 只是纵深防护，并非完整安全边界；重定向和网页内容仍必须视为不可信。
只有确实要访问本地服务时才设置 `SLOTFLOW_NETWORK_ALLOW_PRIVATE=true`。

### MarkItDown 转换与视觉 OCR

唯一的 `convert_file_to_markdown` 工具可转换 workspace 内的 PDF、Word、Excel、PowerPoint、
HTML/数据、图片、音频、EPUB 和压缩包。LiteLLM 判定当前 run 模型支持视觉时会自动复用；否则可配置
专用 OpenAI-compatible Vision 模型。扫描 PDF 和嵌入图片使用官方 `markitdown-ocr` 插件。文件大小、
压缩包展开、OCR 页数/图片数、输出长度、路径和 artifact 写入都有上限。

```bash
SLOTFLOW_MARKITDOWN_ENABLED=true
SLOTFLOW_MARKITDOWN_MAX_INPUT_BYTES=52428800
SLOTFLOW_MARKITDOWN_MAX_OUTPUT_CHARS=750000
SLOTFLOW_MARKITDOWN_VISION_ENABLED=true
SLOTFLOW_MARKITDOWN_VISION_MAX_PAGES=20
SLOTFLOW_MARKITDOWN_VISION_MAX_IMAGES=20

# 可选的专用 OpenAI-compatible client：
# SLOTFLOW_MARKITDOWN_VISION_MODEL=gpt-4o
# SLOTFLOW_MARKITDOWN_VISION_BASE_URL=https://api.openai.com/v1
# SLOTFLOW_MARKITDOWN_VISION_API_KEY=sk-...
```

没有兼容视觉模型时仍会运行普通提取，但图片/扫描 PDF 会返回明确 warning，不会假装 OCR 成功。

### 网络和 Docker 沙箱

网络工具：

```bash
SLOTFLOW_NETWORK_ENABLED=true
SLOTFLOW_NETWORK_ALLOW_PRIVATE=false
SLOTFLOW_NETWORK_MAX_FETCH_BYTES=524288
SLOTFLOW_NETWORK_TIMEOUT_SECONDS=15
```

Docker 沙箱：

```bash
SLOTFLOW_CODE_EXECUTION_ENABLED=true
SLOTFLOW_DOCKER_SANDBOX_IMAGE=python:3.12
SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS=120
SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED=true
SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS=600
SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=true
```

需要显示在右侧工作区面板中的生成文件必须写入产物。Agent 可以使用：

- `artifact_write`：直接写入产物内容。
- `sandbox_artifact_copy`：把 Docker 内已经生成的单个文件复制到当前线程产物目录。

## 运行 SlotFlow

推荐：

```bash
make dev
```

手动启动后端：

```bash
cd backend
uv run uvicorn app.main:app --env-file ./.env --reload
```

手动启动前端：

```bash
cd frontend
pnpm dev
```

打开：

```text
http://localhost:3000
```

## 核心功能

### 聊天工作区

- 持久化 threads、消息历史和流式回复。
- 文件上传和消息队列。
- Human-in-the-loop 澄清提示。
- 与最终回答分离的可见 reasoning 输出。
- 通过 `write_todos` 展示可视化任务进度。
- 用于文件、预览和 Host terminal 的工作区面板。

### 运行时模型选择

输入区可以选择：

- mode：`flash`、`pro` 或 `ultra`
- model：从已配置供应商动态发现

后端会按前端发送的 provider provenance 路由每次运行。

### Skills 和 MCP

界面支持已安装 Skills 和 MCP server 管理，也会显示受保护的有状态 Playwright preset。
Skills 可以启用、禁用、置顶、排序、安装、
上传和删除。MCP servers 可以通过环境 JSON 配置，也可以从界面管理。

### Sub-Agents

SlotFlow 支持通过功能型 Sub-Agent 做聚焦委派，例如 researcher、analyst、planner、
coder、reviewer、writer。角色和领域提示词存储在后端 harness 中，仅在需要时加载，
避免主 Agent 每个任务都读取完整角色库。子图 recursion limit 默认为 100，使多轮工具调用和
工具后反思有足够步数；可用 `SLOTFLOW_SUBAGENT_RECURSION_LIMIT=<positive-int>` 覆盖，
不会改变主图的 recursion limit。

### 产物

生成的可交付文件会存储在线程级产物目录中，并显示在工作区面板。预览面板支持常见源码/文本格式、
Markdown、HTML、PDF、图片、SVG、`.docx`、`.xlsx`/`.xlsm`、`.pptx` 和 `.drawio`。

### 记忆

SlotFlow 有本地长期记忆，用于保存持久事实、偏好、用户画像备注和主题上下文。记忆可以从 UI
显式管理；启用 proactive memory extraction 时，harness 也可以在运行结束后提取持久上下文。

### 终端

右侧面板终端是用户操作的 Host PTY，用于手动 setup 和 debug。它不是 Agent 工具，
也和 Docker 隔离的 `sandbox_exec` 分开。

## 架构

```text
Browser / Next.js UI
  -> POST chat stream request
  -> FastAPI chat routes
  -> RuntimeBackedAgentAdapter
  -> ChatLiteLLM + LangGraph StateGraph
  -> LangGraph v3 projections
  -> SlotFlow AgentEvent
  -> SSE stream
  -> chat UI, todo panel, clarification UI, workspace panel
```

后端关键分层：

- `backend/app/chat/`：chat API、Pydantic models、SQLite repository、run config、SSE。
- `backend/app/chat/runtime/`：环境、模型创建、checkpointer、graph adapter。
- `backend/app/chat/agent_adapter/`：LangGraph projection 归一化。
- `backend/app/harness/`：graph、steps、tools、Skills、MCP、memory、sandbox、sub-agents。

前端关键分层：

- `frontend/src/components/chat/`：chat app、sidebar、message list、composer、workspace。
- `frontend/src/hooks/`：stream 处理、model catalog、workspace data。
- `frontend/src/lib/`：chat stream client 和共享前端工具。

## 项目结构

```text
SlotFlow/
  bootstrap.sh              首次环境配置入口
  Makefile                  仓库根目录开发和验证命令
  backend/
    .env_example            完整后端配置模板
    app/
      chat/                 chat API、runtime config、repository、SSE
      chat/runtime/         model/checkpointer/graph assembly
      chat/agent_adapter/   LangGraph projection -> AgentEvent
      harness/              graph、steps、tools、Skills、MCP、memory、sandbox、sub-agents
      terminal/             Host PTY websocket route
      uploads/              upload API
      workspace/            artifact/workspace API
    tests/                  后端测试
  frontend/
    package.json            Next.js app 和 pnpm 版本
    src/
      app/                  Next.js app shell 和全局 CSS
      components/chat/      SlotFlow 主 UI
      components/ui/        共享 UI primitives
      hooks/                chat/workspace/model hooks
      lib/                  chat stream client 和 helpers
  docs/                     架构和清理记录
```

## 验证

运行全部检查：

```bash
make verify
```

单独运行：

```bash
make test-backend
make typecheck-frontend
make build-frontend
```

只跑后端：

```bash
cd backend
uv run pytest -q
uv run ruff check app tests
```

只跑前端：

```bash
cd frontend
pnpm typecheck
pnpm build
```

Live provider 测试不在默认离线套件中。只有在你准备好 API key 并明确需要 live smoke test 时再运行。

## 故障排查

### `make dev` 找不到 `uv`、`node` 或 `pnpm`

bootstrap 后打开一个新 shell，或导出本地工具路径：

```bash
export PATH="$HOME/.local/bin:$HOME/.volta/bin:$PATH"
```

### `make kill` 没有效果

安装 `fuser`：

```bash
# Debian/Ubuntu
sudo apt-get install psmisc
```

### Docker 只能用 sudo

如果 bootstrap 把当前用户加入了 `docker` 组，退出并重新登录。已有 shell 不会自动获得新 group membership。

### Docker pull 很慢或失败

为 bootstrap 设置 mirrors：

```bash
SLOTFLOW_DOCKER_REGISTRY_MIRRORS="https://docker.1ms.run https://docker.m.daocloud.io" ./bootstrap.sh
```

也可以设置 `SLOTFLOW_SKIP_DOCKER=1`，之后再手动配置 Docker。

### UI 中没有模型

检查：

- `backend/.env` 中至少设置了一个 provider API key
- 后端进程能访问 provider base URL
- custom relay 要么支持 `/models`，要么设置了 `CUSTOM_MODELS`
- 修改 `backend/.env` 后已经重启后端

### 前端连不上后端

确认后端监听在 `8000`：

```bash
curl http://127.0.0.1:8000/api/chat/models
```

如果后端在其他地址，设置：

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://host:port
NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL=http://host:port
```

## 安全说明

SlotFlow 默认面向本地可信环境。暴露到 localhost 以外之前：

- 不要把 API keys 提交到 git。
- 不要提交 `backend/.env` 或前端 `.env.local`。
- 任何公网部署前都要加认证。
- 除非你信任 prompts 和 users，否则保持 private-network fetching 关闭。
- 生成产物在审查前都应视为不可信内容。
- Host terminal 访问只应在本地可信环境中使用。
- 生成/不可信代码应通过 Docker sandbox 执行，不要通过 host shell 执行。

## 贡献

1. 创建 feature branch。
2. 保持改动聚焦。
3. 行为或命令变化时，同步测试和文档。
4. 运行 `make verify`。
5. 向受保护的默认分支打开 pull request。

重要仓库文档：

- [AGENTS.md](./AGENTS.md)：工作规则和当前架构地图。
- [HARNESS_NOTES.md](./HARNESS_NOTES.md)：harness 工程日志。
- [docs/](./docs)：更多架构和清理记录。

## 许可证

公开发布仓库前请先添加许可证。在此之前，默认保留所有权利。
