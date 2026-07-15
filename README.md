# SlotFlow

English | [Chinese](./README_zh.md)

[![Python](https://img.shields.io/badge/Python-3.12--3.13-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](./backend)
[![Next.js](https://img.shields.io/badge/Next.js-000000?logo=next.js&logoColor=white)](./frontend)
[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)](./backend/app/harness)

SlotFlow is a local-first, extensible AI agent workspace. A FastAPI + LangGraph
backend drives a Next.js chat interface with runtime model selection, visible reasoning
streams, Skills, MCP tools, local memory, artifacts, Docker-backed code execution, and
focused sub-agents.

It is designed for research, coding, analysis, and report-generation workflows where
the agent should not only answer, but also read files, call tools, remember useful
context, ask for clarification, and produce durable outputs that can be previewed in the
workspace panel.

---

## Contents

- [What You Get](#what-you-get)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [bootstrap.sh](#bootstrapsh)
- [Makefile Commands](#makefile-commands)
- [Manual Setup](#manual-setup)
- [Configuration](#configuration)
- [Running SlotFlow](#running-slotflow)
- [Core Features](#core-features)
- [Architecture](#architecture)
- [Project Layout](#project-layout)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)

## What You Get

- Streaming chat with separate visible reasoning output.
- Runtime model discovery from configured providers instead of hard-coded model lists.
- DeepSeek, OpenAI, Anthropic, and custom OpenAI-compatible relay support.
- Skills, MCP servers, web tools, workspace tools, uploads, and artifact preview.
- Docker-backed `sandbox_exec` for untrusted code execution.
- `sandbox_artifact_copy` for publishing files generated inside Docker to the UI.
- Long-term memory and local SQLite persistence.
- Layered sub-agent delegation for larger tasks.
- A repo-root `bootstrap.sh` and `Makefile` so new clones can get running quickly.

## Requirements

Recommended platform:

- Linux or WSL2
- Python 3.12 or 3.13
- Node.js 20+; Node 22 is the default target used by `bootstrap.sh`
- pnpm 10.26.2, read from `frontend/package.json`
- `make`, `curl`, `git`
- Docker Engine for code execution tools and Docker sandbox artifacts

`./bootstrap.sh` can install or validate most of these on common Linux families:
`apt`, `dnf`, `yum`, `pacman`, `apk`, and `zypper`. It also has a Homebrew path for
basic tools, but Docker setup is primarily Linux/WSL-oriented.

## Quick Start

```bash
git clone <your-repository-url>
cd SlotFlow
./bootstrap.sh
```

Then edit `backend/.env` and add at least one model provider API key:

```bash
nano backend/.env
```

Start both servers:

```bash
make dev
```

Open:

```text
http://localhost:3000
```

The backend runs on `http://127.0.0.1:8000`. In local browser development the frontend
calls that backend by default.

## bootstrap.sh

`./bootstrap.sh` is the recommended first-run setup path for a fresh clone.

It does the following:

1. Installs or validates system prerequisites used by the repo and `Makefile`.
2. Installs `uv` if it is missing.
3. Installs Node and pnpm, using the pnpm version declared in `frontend/package.json`.
4. Installs or refreshes Agent Reach with `uv tool`, then prepares its core host-side channels.
5. Runs `uv sync` in `backend/`, including MarkItDown all-format and Vision OCR dependencies.
6. Runs `pnpm install --frozen-lockfile` in `frontend/`, installs Playwright's Chromium shared libraries on apt hosts, and downloads the locked Chromium runtime.
7. Copies `backend/.env_example` to `backend/.env` only if `backend/.env` does not exist.
8. Installs, starts, and prepares Docker when possible.
9. Pre-pulls the sandbox image when possible.

The script never overwrites an existing `backend/.env`. Playwright's official dependency
installer currently handles Debian/Ubuntu (`apt`) automatically; on other distributions bootstrap
keeps going with a warning and Chromium runtime libraries may need to be installed manually.

Useful bootstrap knobs:

```bash
# Skip OS package installation. Use this when packages are already installed
# or when you do not want bootstrap to use sudo/root package-manager commands.
SLOTFLOW_SKIP_SYSTEM_PACKAGES=1 ./bootstrap.sh

# Skip all Docker setup. The app can still run, but sandbox_exec will not work
# until Docker is installed and reachable.
SLOTFLOW_SKIP_DOCKER=1 ./bootstrap.sh

# Skip Agent Reach host setup or the Playwright Chromium download independently.
SLOTFLOW_SKIP_AGENT_REACH=1 ./bootstrap.sh
SLOTFLOW_SKIP_PLAYWRIGHT_BROWSER=1 ./bootstrap.sh

# Override the Agent Reach Git source used by uv tool. Rerunning bootstrap is the update path.
SLOTFLOW_AGENT_REACH_SOURCE=git+https://github.com/Panniantong/Agent-Reach.git ./bootstrap.sh

# Override runtime tool versions used by bootstrap.
SLOTFLOW_NODE_VERSION=22 ./bootstrap.sh
SLOTFLOW_PNPM_VERSION=10.26.2 ./bootstrap.sh

# Override the Docker sandbox image pre-pulled by bootstrap.
SLOTFLOW_DOCKER_IMAGE=python:3.12 ./bootstrap.sh

# Override registry mirrors used only after a direct Docker Hub pull fails.
SLOTFLOW_DOCKER_REGISTRY_MIRRORS="https://docker.1ms.run https://docker.m.daocloud.io" ./bootstrap.sh

# Tune how long bootstrap waits for Docker daemon startup.
SLOTFLOW_DOCKER_DAEMON_WAIT_SECONDS=30 ./bootstrap.sh
```

Docker notes:

- If bootstrap adds your user to the `docker` group, log out and back in before
  expecting non-sudo Docker access.
- On WSL hosts without systemd, bootstrap can write `systemd=true` to `/etc/wsl.conf`.
  Run `wsl --shutdown` once from Windows for that setting to fully take effect.
- If Docker cannot be started or the image cannot be pulled, bootstrap completes with a
  warning. SlotFlow will retry Docker startup/pull on first sandbox use.

## Makefile Commands

The root `Makefile` is the normal day-to-day entrypoint after bootstrap.

```bash
make dev
```

Starts both local development servers:

- frontend: `cd frontend && pnpm dev`
- backend: `cd backend && uv run uvicorn app.main:app --env-file ./.env --reload`

Stop the servers with `Ctrl+C`.

```bash
make verify
```

Runs the full local verification set:

- backend tests: `cd backend && uv run pytest -q`
- frontend typecheck: `cd frontend && pnpm typecheck`
- frontend production build: `cd frontend && pnpm build`

Individual targets are also available:

```bash
make test-backend
make typecheck-frontend
make build-frontend
```

Kill local dev servers by port:

```bash
make kill
```

`make kill` uses `fuser` for ports `3000` and `8000`; on most Linux distributions
`fuser` comes from the `psmisc` package.

## Manual Setup

Use this path if you do not want `bootstrap.sh` to install packages.

Install prerequisites yourself:

- Python 3.12 or 3.13
- `uv`
- Node.js 20+
- pnpm 10.26.2
- Docker Engine if you want `sandbox_exec`
- `make`, `curl`, `git`, and optionally `fuser`

Install dependencies:

```bash
cd backend
uv sync

cd ../frontend
pnpm install --frozen-lockfile

cd ..
```

Create a local backend environment file:

```bash
cp backend/.env_example backend/.env
```

Then fill in at least one provider API key in `backend/.env`.

## Configuration

The full configuration template lives in:

```text
backend/.env_example
```

Copy it to:

```text
backend/.env
```

`backend/.env` is ignored by git and should contain your real secrets.

### Model Providers

SlotFlow asks LiteLLM which native providers are configured, then exposes every bundled
`chat + function-calling` model for those providers. No provider/model list is maintained by SlotFlow.

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

# Other LiteLLM-native providers use their standard variables, for example:
GEMINI_API_KEY=...
MISTRAL_API_KEY=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1

# Custom OpenAI-compatible relay
CUSTOM_BASE_URL=https://your-relay.example.com/v1
CUSTOM_API_KEY=sk-...
CUSTOM_MODELS=claude-sonnet-4,gpt-5,qwen-max
```

Important model behavior:

- `.env` does not choose the conversation model.
- The frontend sends the selected model and provider with each run.
- All providers execute through `ChatLiteLLM`; LiteLLM owns provider protocol, reasoning, tool-call, and usage normalization.
- `CUSTOM_MODELS` can be used when a relay does not support `/models`.
- All providers use LiteLLM Chat Completions normalization; OpenAI models are not routed through Responses, so OpenAI-compatible DeepSeek/Qwen/custom relays share one transport shape.

### Frontend URLs

For the default local setup, no frontend env file is required. The frontend calls:

```text
http://127.0.0.1:8000
```

Override this in the frontend shell or `frontend/.env.local`:

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL=http://localhost:8000
```

### Storage

Default local paths are under `backend/.slotflow/` when running through `make dev`,
because backend-relative paths are resolved from the `backend/` directory.

Common settings:

```bash
SLOTFLOW_CHAT_SQLITE_PATH=.slotflow/chat.sqlite3
SLOTFLOW_CHECKPOINTER_BACKEND=memory
SLOTFLOW_CHECKPOINTER_SQLITE_PATH=.slotflow/checkpoints.sqlite3
SLOTFLOW_MEMORY_SQLITE_PATH=.slotflow/memory.sqlite3
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
```

### Feature Flags

Most features are enabled by default in `backend/.env_example`:

```bash
SLOTFLOW_LONG_TERM_MEMORY_ENABLED=true
SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION=true
SLOTFLOW_SKILLS_PREFLIGHT_MIDDLEWARE=true
SLOTFLOW_CLARIFY_GATE=true
SLOTFLOW_TODO_MIDDLEWARE=true
SLOTFLOW_MCP_ENABLED=true
SLOTFLOW_CODE_EXECUTION_ENABLED=true
```

Disable a feature only when debugging a subsystem or running in a constrained
environment.

### Agent Reach Host Bridge

`bootstrap.sh` installs Agent Reach and its core upstream CLIs on the host. SlotFlow exposes only
fixed read-only operations for status, Exa web search, Jina page reading, GitHub search, and YouTube
metadata; it never gives the model a host shell or install/configure/write command. The bridge is not
mounted into Docker and is refreshed by rerunning `bootstrap.sh`.

```bash
SLOTFLOW_AGENT_REACH_ENABLED=true
SLOTFLOW_AGENT_REACH_HOME=~/.agent-reach
SLOTFLOW_AGENT_REACH_TIMEOUT_SECONDS=60
SLOTFLOW_AGENT_REACH_MAX_OUTPUT_BYTES=524288
```

The bridge also turns off when `SLOTFLOW_NETWORK_ENABLED=false`. Cookie/login channels remain an
explicit user choice and are not enabled by bootstrap.

### Built-in Playwright MCP

The protected `playwright` MCP preset is enabled by default. It uses a headless isolated Chromium
session that remains alive across browser actions within one run and closes when that run ends;
concurrent conversations get separate sessions. The preset is workspace-scoped, enables no optional
vision/PDF/devtools capabilities, and cannot be replaced by a user HTTP server.

```bash
SLOTFLOW_PLAYWRIGHT_MCP_ENABLED=true
SLOTFLOW_PLAYWRIGHT_MCP_ACTION_TIMEOUT_MS=10000
SLOTFLOW_PLAYWRIGHT_MCP_NAVIGATION_TIMEOUT_MS=60000
```

Its localhost/private-origin blocklist is defense in depth, not a complete security boundary:
redirects and page content remain untrusted. Set `SLOTFLOW_NETWORK_ALLOW_PRIVATE=true` only when
browser access to local services is intentional.

### Network and Docker Sandbox

Network tools:

```bash
SLOTFLOW_NETWORK_ENABLED=true
SLOTFLOW_NETWORK_ALLOW_PRIVATE=false
SLOTFLOW_NETWORK_MAX_FETCH_BYTES=524288
SLOTFLOW_NETWORK_TIMEOUT_SECONDS=15
```

Docker sandbox:

```bash
SLOTFLOW_CODE_EXECUTION_ENABLED=true
SLOTFLOW_DOCKER_SANDBOX_IMAGE=python:3.12
SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS=120
SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED=true
SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS=600
SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=true
```

Generated files that should appear in the right workspace panel must be written as
artifacts. The agent can use:

- `artifact_write` for direct artifact content writes.
- `sandbox_artifact_copy` for one file already generated inside Docker.

## Running SlotFlow

Recommended:

```bash
make dev
```

Manual backend:

```bash
cd backend
uv run uvicorn app.main:app --env-file ./.env --reload
```

Manual frontend:

```bash
cd frontend
pnpm dev
```

Open:

```text
http://localhost:3000
```

## Core Features

### Chat Workspace

- Persistent threads, message history, and streaming replies.
- File uploads and queued messages.
- Human-in-the-loop clarification prompts.
- Visible reasoning output separated from final answer content.
- Visual todo progress through `write_todos`.
- Workspace panel for files, previews, and a host terminal.

### Runtime Model Selection

The composer lets users choose:

- mode: `flash`, `pro`, or `ultra`
- model: discovered from configured providers

The backend routes each run using the provider provenance sent by the frontend.

### Skills and MCP

The UI supports installed Skills and MCP server management, including the protected stateful
Playwright preset. Skills can be enabled,
disabled, pinned, reordered, installed, uploaded, and deleted. MCP servers can be
configured from environment JSON or managed from the UI.

### Sub-agents

SlotFlow supports focused delegation through functional sub-agents such as researcher,
analyst, planner, coder, reviewer, and writer. Role/domain prompts are stored under the
backend harness and loaded only when needed, so the lead agent does not need to read the
entire role library for every task. Delegated child graphs use a recursion limit of 100 so
multiple tool/reflection rounds can finish; override it with
`SLOTFLOW_SUBAGENT_RECURSION_LIMIT=<positive-int>` without changing the main graph limit.

### Artifacts

Generated deliverables are stored under thread-scoped artifact directories and shown in
the workspace panel. The preview panel supports common source/text formats, Markdown,
HTML, PDF, images, SVG, `.docx`, `.xlsx`/`.xlsm`, `.pptx`, and `.drawio`.

### Memory

SlotFlow has local long-term memory for durable facts, preferences, profile notes, and
topic context. Memory can be explicitly managed from the UI, and the harness can also
extract durable context after a run when proactive memory extraction is enabled.

### Terminal

The right panel terminal is a user-operated host PTY for setup and debugging. It is not
an agent tool and is separate from Docker-backed `sandbox_exec`.

## Architecture

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

Key backend layers:

- `backend/app/chat/`: chat API, Pydantic models, SQLite repository, run config, SSE.
- `backend/app/chat/runtime/`: environment, model creation, checkpointer, graph adapter.
- `backend/app/chat/agent_adapter/`: LangGraph projection normalization.
- `backend/app/harness/`: graph, steps, tools, Skills, MCP, memory, sandbox, sub-agents.

Key frontend layers:

- `frontend/src/components/chat/`: chat app, sidebar, message list, composer, workspace.
- `frontend/src/hooks/`: stream handling, model catalog, workspace data.
- `frontend/src/lib/`: chat stream client and shared frontend utilities.

## Project Layout

```text
SlotFlow/
  bootstrap.sh              first-run setup entrypoint
  Makefile                  repo-root dev and verification commands
  backend/
    .env_example            complete backend configuration template
    app/
      chat/                 chat API, runtime config, repository, SSE
      chat/runtime/         model/checkpointer/graph assembly
      chat/agent_adapter/   LangGraph projection -> AgentEvent
      harness/              graph, steps, tools, Skills, MCP, memory, sandbox, sub-agents
      terminal/             host PTY websocket route
      uploads/              upload API
      workspace/            artifact/workspace API
    tests/                  backend tests
  frontend/
    package.json            Next.js app and pnpm version
    src/
      app/                  Next.js app shell and global CSS
      components/chat/      main SlotFlow UI
      components/ui/        shared UI primitives
      hooks/                chat/workspace/model hooks
      lib/                  chat stream client and helpers
  docs/                     architecture and cleanup notes
```

## Verification

Run everything:

```bash
make verify
```

Run checks individually:

```bash
make test-backend
make typecheck-frontend
make build-frontend
```

Backend-only:

```bash
cd backend
uv run pytest -q
uv run ruff check app tests
```

Frontend-only:

```bash
cd frontend
pnpm typecheck
pnpm build
```

Live provider tests are not part of the default offline suite. Use them only when you
have the required API keys and intentionally want a live smoke test.

## Troubleshooting

### `make dev` cannot find `uv`, `node`, or `pnpm`

Start a new shell after bootstrap, or export the local tool paths:

```bash
export PATH="$HOME/.local/bin:$HOME/.volta/bin:$PATH"
```

### `make kill` does nothing

Install `fuser`:

```bash
# Debian/Ubuntu
sudo apt-get install psmisc
```

### Docker works only with sudo

If bootstrap added your user to the `docker` group, log out and back in. Existing shells
do not automatically receive new group membership.

### Docker pull is slow or fails

Set mirrors for bootstrap:

```bash
SLOTFLOW_DOCKER_REGISTRY_MIRRORS="https://docker.1ms.run https://docker.m.daocloud.io" ./bootstrap.sh
```

Or set `SLOTFLOW_SKIP_DOCKER=1` and configure Docker manually later.

### No models appear in the UI

Check:

- at least one provider API key is set in `backend/.env`
- the provider base URL is reachable from the backend process
- custom relays either support `/models` or set `CUSTOM_MODELS`
- the backend was restarted after editing `backend/.env`

### Frontend cannot reach backend

Verify the backend is listening on port `8000`:

```bash
curl http://127.0.0.1:8000/api/chat/models
```

If the backend is elsewhere, set:

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://host:port
NEXT_PUBLIC_SLOTFLOW_STREAM_BASE_URL=http://host:port
```

## Security Notes

SlotFlow targets local, trusted environments by default. Before exposing it beyond
localhost:

- Keep API keys out of git.
- Never commit `backend/.env` or frontend `.env.local` files.
- Add authentication in front of any public deployment.
- Keep private-network fetching disabled unless you trust the prompts and users.
- Treat generated artifacts as untrusted content until reviewed.
- Keep host terminal access local and trusted.
- Use Docker sandbox execution for generated/untrusted code, not host shell execution.

## Contributing

1. Create a feature branch.
2. Keep changes focused.
3. Update tests and documentation when behavior or commands change.
4. Run `make verify`.
5. Open a pull request against the protected default branch.

Important repo docs:

- [AGENTS.md](./AGENTS.md): working rules and current architecture map.
- [HARNESS_NOTES.md](./HARNESS_NOTES.md): harness engineering log.
- [docs/](./docs): additional architecture and cleanup notes.

## License

Add a license before publishing this repository publicly. Until then, all rights are
reserved by default.
