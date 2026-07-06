# SlotFlow

English | [中文](./README_zh.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](./backend)
[![Next.js](https://img.shields.io/badge/Next.js-000000?logo=next.js&logoColor=white)](./frontend)
[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)](./backend/app/harness)

**SlotFlow is a local-first, extensible AI agent workspace.** A FastAPI + LangGraph
backend drives a Next.js chat interface, with pluggable **skills**, **MCP tools**,
**artifacts**, **long-term memory**, focused **sub-agents**, and **multi-provider**
reasoning streaming (DeepSeek · OpenAI · Anthropic).

It is built for research, coding, analysis, and report-generation workflows where the
agent should not just answer, but also read files, call tools, remember useful context,
think out loud, and produce durable, shareable outputs.

---

## Highlights

- **Streaming chat with visible thinking** — token-by-token answers plus a separate
  reasoning channel, surfaced live in the UI.
- **Multi-provider, runtime model selection** — models are discovered at runtime from
  the `/models` endpoint of each configured provider; the frontend picks the model per
  conversation. Base URLs are env-driven, so third-party / self-hosted OpenAI-compatible
  gateways work out of the box.
- **Provider-aware reasoning** — DeepSeek `reasoning_content`, OpenAI o-series
  reasoning, and Anthropic extended thinking are normalized into one reasoning stream.
- **Skills, tools & MCP** — list / install / enable / pin / reorder skills, manage HTTP
  MCP servers, and use workspace + web tools, all from the UI.
- **Artifacts** — agent-generated reports, charts, and pages are saved as HTML/Markdown
  artifacts and previewed in a side panel.
- **Local persistence** — chat threads / messages / runs in SQLite, multi-turn graph
  state via LangGraph checkpoints, and long-term memory in a local store.

## Table of Contents

- [Quick Start](#quick-start)
  - [Configuration](#configuration)
  - [Running the Application](#running-the-application)
  - [Local Development](#local-development)
- [Core Features](#core-features)
- [Architecture](#architecture)
- [Project Layout](#project-layout)
- [Verification](#verification)
- [Security Notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)

## Quick Start

### Configuration

```bash
git clone <your-repository-url>
cd SlotFlow
./bootstrap.sh
```

`./bootstrap.sh` installs/validates the repo prerequisites, backend and frontend
dependencies, creates `backend/.env` from `backend/.env_example` if it does not already
exist, and prepares the Docker sandbox on common Linux/WSL hosts. It never overwrites an
existing `backend/.env`.

Provide provider credentials in `backend/.env`. Start from `backend/.env_example` for the
full list of feature flags and local paths. Secrets and endpoints live in the environment;
**which model** a conversation uses is chosen in the UI at runtime.

```bash
# Any OpenAI-compatible DeepSeek endpoint (official or third-party gateway)
DEEPSEEK_API_KEY=your-deepseek-api-key
# DEEPSEEK_BASE_URL=https://api.deepseek.com        # override for a third-party gateway

# OpenAI (official or compatible)
# OPENAI_API_KEY=your-openai-api-key
# OPENAI_BASE_URL=https://api.openai.com/v1

# Anthropic
# ANTHROPIC_API_KEY=your-anthropic-api-key
# ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
```

Optional local runtime settings:

```bash
SLOTFLOW_CHAT_SQLITE_PATH=.slotflow/chat.sqlite3
SLOTFLOW_CHECKPOINTER_BACKEND=sqlite
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
SLOTFLOW_WORKSPACE_WRITES_ENABLED=true
SLOTFLOW_NETWORK_ENABLED=true
```

Docker sandbox setup is best-effort across common Linux package managers. If the script
adds your user to the `docker` group, log out and back in before expecting non-sudo Docker
access from the backend process.

Only providers whose `*_API_KEY` is set appear in the model selector; a provider whose
endpoint is unreachable or returns no chat models is shown with an `error` status and
no models (there are no hard-coded fallback model lists).

### Running the Application

```bash
make dev
```

Open <http://localhost:3000>. In local browser development the frontend calls the
backend at `http://127.0.0.1:8000`; override with
`NEXT_PUBLIC_SLOTFLOW_API_BASE_URL`.

### Local Development

```bash
make verify      # backend tests + frontend typecheck + build
# or individually:
cd backend  && uv run pytest -q
cd frontend && pnpm typecheck && pnpm build
```

## Core Features

- **Chat workspace** — threads, history, streaming, uploads, queued messages,
  clarification prompts, thinking output, and task/todo progress.
- **Model selection** — `mode` (`flash` / `pro` / `ultra`) plus a model discovered from
  configured providers; both are sent with each run, so `.env` never hard-codes the
  conversation model.
- **Skills & tools** — local capability packages (list / enable / pin / reorder /
  upload / install / remove) plus workspace, web fetch/search, skill, and MCP tools.
- **Sub-agents** — in `ultra` mode the lead agent can delegate to focused profiles
  (researcher, analyst, planner, coder, reviewer, writer) via `task_tool`.
- **Artifacts** — durable HTML/Markdown outputs rendered in a preview panel.
- **Memory** — local long-term memory (facts / preferences / profile / topics) wired
  into runs through middleware.
- **MCP servers** — configured from environment JSON or managed from the UI.

## Architecture

```text
HTTP request
  -> chat routes (SQLite repository, run config)
  -> AgentAdapter (RuntimeBackedAgentAdapter)
       -> per-run: provider model + LangGraph harness graph + checkpointer
       -> LangGraphEventAgentAdapter: astream_events(v3)
  -> projections -> SlotFlow AgentEvent -> SSE -> frontend
```

The backend is layered so the API never touches LangGraph internals directly:
`app/chat/runtime/` assembles models / checkpointers / the graph;
`app/chat/agent_adapter/` (events · projections · streaming) translates LangGraph v3
projections into a small set of business events; `app/harness/` owns tools, skills,
MCP, memory, sandbox, and middleware.

## Project Layout

```text
SlotFlow/
  backend/
    app/chat/          chat API, models, SQLite repository, run config, SSE
    app/chat/runtime/  model/checkpointer/graph assembly (env · config · models · adapter)
    app/chat/agent_adapter/  v3 projection -> AgentEvent (events · projections · streaming)
    app/harness/       tools, skills, MCP, memory, sandbox, middleware, subagents
    tests/             offline backend test suite
  frontend/src/        Next.js chat UI (chat-app, sidebar, message list, composer, hooks)
  docs/                architecture and development notes
  Makefile             verification shortcuts
```

## Verification

```bash
make verify
```

Runs the backend test suite (`uv run pytest`), frontend type checking (`tsc --noEmit`),
and a production frontend build. A live DeepSeek smoke test is available but excluded
from the default suite (set `SLOTFLOW_LIVE_DEEPSEEK=1`).

## Security Notes

SlotFlow targets local, trusted environments by default. When exposing it beyond
localhost:

- keep API keys out of git; never commit `.env`
- keep workspace writes disabled unless the environment is trusted
- restrict network access when running untrusted prompts
- put authentication in front of any public deployment
- review generated artifacts before serving them publicly

## Contributing

1. Fork the repository and create a feature branch.
2. Make focused commits with tests.
3. Open a pull request against `main`; keep backend + frontend checks green.
4. Address review comments and keep the branch up to date.

The default branch is protected and requires pull requests.

## License

Add a license before publishing the repository publicly. Until then, all rights are
reserved by default.
