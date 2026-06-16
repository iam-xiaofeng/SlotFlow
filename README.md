# SlotFlow

English | [中文](./README_zh.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)

SlotFlow is an open-source agent workspace built around a FastAPI backend, a
Next.js chat interface, extensible skills, local memory, MCP tools, artifacts,
and focused sub-agents.

It is designed for local-first research, coding, analysis, and report
generation workflows where the agent should not only answer, but also inspect
files, call tools, remember useful context, and produce durable outputs.

---

## Table of Contents

- [Quick Start](#quick-start)
  - [Configuration](#configuration)
  - [Running the Application](#running-the-application)
  - [Local Development](#local-development)
- [Core Features](#core-features)
  - [Chat Workspace](#chat-workspace)
  - [Model Selection](#model-selection)
  - [Skills and Tools](#skills-and-tools)
  - [Sub-Agents](#sub-agents)
  - [Artifacts](#artifacts)
  - [Memory](#memory)
  - [MCP Servers](#mcp-servers)
- [Project Layout](#project-layout)
- [Verification](#verification)
- [Security Notes](#security-notes)
- [Contributing](#contributing)
- [License](#license)

## Quick Start

### Configuration

Clone the repository and enter the project directory:

```bash
git clone <your-repository-url>
cd SlotFlow
```

Set provider credentials in your backend environment. Model selection is handled
by the frontend at runtime; environment variables should provide secrets and
provider endpoints only.

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

Optional local runtime settings:

```bash
SLOTFLOW_CHECKPOINTER_BACKEND=memory
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
SLOTFLOW_WORKSPACE_WRITES_ENABLED=false
SLOTFLOW_NETWORK_ENABLED=true
```

### Running the Application

Start the backend:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```bash
cd frontend
pnpm install
pnpm dev
```

Open the frontend at:

```text
http://localhost:3000
```

The frontend calls the backend at `http://127.0.0.1:8000` during local browser
development. Override this with:

```bash
NEXT_PUBLIC_SLOTFLOW_API_BASE_URL=http://127.0.0.1:8000
```

### Local Development

Use WSL or Linux for the smoothest development workflow:

```bash
cd ~/code/SlotFlow
```

Install and verify dependencies:

```bash
cd backend
uv run pytest -q

cd ../frontend
pnpm install
pnpm typecheck
pnpm build
```

Or run the repository checks:

```bash
make verify
```

## Core Features

### Chat Workspace

SlotFlow provides a persistent chat workspace with threads, message history,
streaming responses, uploaded files, queued messages, clarification prompts,
thinking output, and task progress.

### Model Selection

The composer exposes two runtime controls:

- `mode`: `flash`, `pro`, or `ultra`
- `model`: discovered from configured DeepSeek, OpenAI, or Anthropic credentials

The backend exposes available models through `/api/chat/models`. The selected
model and mode are sent with each run request, so `.env` does not decide which
model a conversation uses.

### Skills and Tools

Skills are local capability packages that teach the agent how to handle a
specialized workflow. SlotFlow can list, enable, pin, reorder, upload, install,
and remove skills from the UI.

Built-in tool groups include:

- workspace listing, reading, tree, and search
- artifact listing
- web fetch and search
- skill matching and installation
- MCP server management

### Sub-Agents

In `ultra` mode, SlotFlow can delegate focused work to named sub-agents. Current
profiles include:

- `researcher`: source gathering and open-question tracking
- `analyst`: metric interpretation and scenario reasoning
- `planner`: task decomposition and verification planning
- `coder`: codebase inspection and implementation notes
- `reviewer`: risk review and missing-test analysis
- `writer`: reports, README text, and release notes

The lead agent can inspect enabled profiles with `subagent_list`, then delegate
work with `task_tool`.

### Artifacts

Agent-generated files are surfaced in the artifact panel. Markdown artifacts are
rendered as Markdown previews, while raw file access remains available through
the workspace API.

### Memory

SlotFlow includes local long-term memory for facts, preferences, profile notes,
and topic context. Memory can be created, edited, deleted, and attached to agent
runs through middleware.

### MCP Servers

MCP servers can be configured from environment JSON or managed from the UI.
HTTP MCP servers can be added, enabled, pinned, reordered, and removed without
restarting the frontend.

## Project Layout

```text
SlotFlow/
  backend/        FastAPI API, chat runtime, harness, tools, tests
  frontend/       Next.js UI, chat workspace, artifact panel
  docs/           Local notes and architecture references
  Makefile        Verification shortcuts
```

## Verification

Backend:

```bash
cd backend
uv run pytest
```

Frontend:

```bash
cd frontend
pnpm typecheck
pnpm build
```

All checks:

```bash
make verify
```

## Security Notes

SlotFlow is intended for local trusted environments by default. Be careful when
exposing it to a LAN or the public internet.

Recommended safeguards:

- keep API keys out of git
- do not commit `.env` files
- keep workspace writes disabled unless trusted
- restrict network access when running untrusted prompts
- put authentication in front of any public deployment
- review generated artifacts before serving them publicly

## Contributing

External contributors usually work from forks:

1. Fork the repository.
2. Create a feature branch in the fork.
3. Make focused commits with tests.
4. Open a pull request against the main repository.
5. Address review comments and keep the branch updated.

Maintainers should protect the default branch, require pull requests, and run
backend plus frontend checks before merging.

## License

Add a license before publishing the repository publicly. Until then, all rights
are reserved by default.
