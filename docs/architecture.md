# Architecture

SlotFlow is split into a backend runtime and a frontend chat workspace.

## Backend

The backend is a FastAPI application with these main boundaries:

- `app.chat`: thread, message, run, streaming, model discovery, and runtime setup
- `app.harness`: LangGraph/LangChain graph assembly, tools, middleware, skills, memory, and sub-agents
- `app.uploads`: user file upload storage and staging
- `app.workspace`: artifact and workspace file access
- `app.skills`: skill listing, upload, install, and state management
- `app.mcp`: user-managed MCP server configuration
- `app.memory`: local long-term memory records

The chat stream path is:

```text
POST /api/chat/threads/{thread_id}/runs/stream
-> create run
-> build config and context
-> stream agent events
-> encode business SSE
-> persist assistant output
```

## Frontend

The frontend is a Next.js application. The chat workspace is centered around:

- `ChatApp`: top-level state for threads, attachments, artifacts, skills, MCP, memory, and model selection
- `ChatComposer`: message input, file attachment, mode selection, and model selection
- `useChatStream`: thread creation, SSE consumption, message updates, todos, and cancellation
- `MessageList`: assistant/user message rendering, clarification choices, reasoning display, and retry/edit actions
- `ArtifactWorkspacePanel`: artifact browsing and preview rendering

## Runtime Controls

Each run receives:

- `model_name`: selected by the frontend from `/api/chat/models`
- `mode`: `flash`, `pro`, or `ultra`
- `agent_name`: current top-level agent profile
- `files`: uploaded file ids staged into the run workspace
- `metadata`: UI and request metadata

Provider credentials remain server-side. The frontend only receives sanitized
provider status and selectable model ids.

## Extension Points

- Add a skill under the configured skills root or upload it from the UI.
- Add tools through MCP server configuration.
- Add a sub-agent profile in `app.harness.subagents.config`.
- Add middleware in `app.harness.middleware.registry`.
- Add workspace-safe tools under `app.harness.tools`.
