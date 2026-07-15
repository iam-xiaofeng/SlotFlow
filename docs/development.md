# Development

This document covers the local development workflow for SlotFlow.

## Prerequisites

- Python 3.12 or 3.13
- Node.js 22+
- `uv`
- `pnpm`
- WSL or Linux for the recommended local workflow

For a fresh clone, prefer the root bootstrap script:

```bash
./bootstrap.sh
```

It installs or validates the Makefile prerequisites, `uv`, Node/pnpm, backend/frontend
dependencies, Agent Reach host channels, Playwright MCP/Chromium, MarkItDown ffmpeg/ExifTool
helpers, copies `backend/.env_example` to `backend/.env` if missing, and prepares the Docker sandbox
on common Linux/WSL hosts. It never overwrites an existing `backend/.env`.

## Backend

Install dependencies and run tests:

```bash
cd backend
uv run pytest
```

Start the API server:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Frontend

Install dependencies and run checks:

```bash
cd frontend
pnpm install
pnpm typecheck
pnpm build
```

Start the UI:

```bash
cd frontend
pnpm dev
```

Open:

```text
http://localhost:3000
```

## Environment

The backend reads provider credentials and runtime infrastructure settings from
environment variables. The selected chat model and mode are sent by the
frontend with each request.
Every provider runs through the pinned `langchain-litellm` integration; Python 3.14 is not supported by the current LiteLLM release.

Use `backend/.env_example` as the full template for provider keys, feature flags,
storage paths, MCP, workspace, Docker sandbox, terminal, and title-generation settings.

Common provider variables:

```bash
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com

OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1

ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=https://api.anthropic.com/v1

GEMINI_API_KEY=...
MISTRAL_API_KEY=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1
```

Common runtime variables:

```bash
SLOTFLOW_CHECKPOINTER_BACKEND=memory
SLOTFLOW_CHAT_SQLITE_PATH=.slotflow/chat.sqlite3
SLOTFLOW_SKILLS_ROOT=.slotflow/skills
SLOTFLOW_WORKSPACE_ROOT=.slotflow/workspace
```

Docker sandbox setup is best-effort across common apt/dnf/yum/pacman/apk/zypper Linux
hosts. If bootstrap adds your user to the `docker` group, log out and back in before
expecting the backend process to use Docker without sudo.

## Verification

Run the full local check suite from the repository root:

```bash
make verify
```

Before opening a pull request, run at least:

```bash
cd backend && uv run pytest
cd ../frontend && pnpm typecheck
```
