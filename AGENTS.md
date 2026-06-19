# AGENTS.md

Guidance for AI agents (and humans) working in the SlotFlow repository.

> **Rule: every code change must update this file in the same change.** If you touch
> behavior, architecture, conventions, or commands, reflect it here so AGENTS.md stays an
> accurate map of the repo. Keeping it current is part of "done", not an afterthought.

## What SlotFlow is

A local-first, extensible AI agent workspace: a FastAPI + LangGraph backend driving a
Next.js chat UI, with skills, MCP tools, artifacts, long-term memory, sub-agents, and
multi-provider (DeepSeek / OpenAI / Anthropic + any OpenAI-compatible relay) reasoning
streaming.

## Architecture (one request, end to end)

1. **Frontend** (`components/chat/chat-app.tsx` + `hooks/use-chat-stream.ts`) POSTs to the
   chat stream route with a `ChatStreamRequest` (message, `model_name`, `provider`, `mode`,
   `thinking_enabled`, `files`).
2. **`chat/routes.py`** persists the user message, then `chat/run_config.build_run_config`
   turns the request into a `RunConfigBundle = {config, context}`:
   - `config["configurable"]["thread_id"]` — LangGraph's key for multi-turn checkpoint state.
   - `RunContext` — SlotFlow business switches: `model_name`, **`model_provider`**, `mode`,
     `thinking_enabled`, plan/subagent flags, files.
3. **`chat/runtime/adapter.py` (RuntimeBackedAgentAdapter)** builds the chat model via
   `runtime/models.create_chat_model` (routed by `RunContext.model_provider`) and assembles
   the graph via `harness/builder.build_slotflow_harness_graph` — LangChain's prebuilt
   `create_agent` + SlotFlow middleware + the tool registry (`harness/tools/registry`).
4. The graph streams with the **LangGraph v3 projection protocol**; each item is normalized
   by **`chat/agent_adapter/projections.py`** into a SlotFlow `AgentEvent` (`message.delta`
   with channel `reasoning`/`content`, `state.snapshot`, `tool.delta`,
   `clarification.requested`, `todo.updated`, `run.*`).
5. **`chat/sse.py`** encodes events as SSE; the frontend consumes them
   (`lib/chat-stream.ts` + `hooks/use-chat-stream*`) and renders the message, reasoning,
   todos, clarification picker, and workspace files.

Two boundaries carry most of the design: **RunContext vs config.configurable** (business
switches vs LangGraph runtime keys), and the **projection layer** — the single place that
absorbs every provider/version quirk into clean `AgentEvent`s.

## Layout

```
backend/app/
  chat/                 chat API, Pydantic models, SQLite repository, run config, SSE
  chat/runtime/         per-run assembly: env · config · models · checkpointer · adapter
  chat/agent_adapter/   LangGraph v3 projection -> SlotFlow AgentEvent
                        (events · projections · streaming)
  harness/              the agent graph: builder, tools, skills, mcp, memory, sandbox,
                        middleware, subagents
  {uploads,workspace,skills,mcp,memory}/  FastAPI route modules
  dependencies.py       shared app.state getters; clock.py: utc_now
backend/tests/          offline test suite (no network); test_live_deepseek.py is opt-in
frontend/src/
  components/chat/      chat UI: chat-app + extracted hooks, sidebar(+search/logo;
                        skills/mcp/memory managed in directory-modal tabs),
                        message-list(+parts), composer(+parts), workspace-panel
                        (two-pane; reuses artifact-panel's preview stage)
  hooks/                use-chat-stream (+ helpers), use-model-catalog, use-workspace-data,
                        use-thread-artifact-index
  lib/chat-stream.ts    API client + SSE
```

## Key conventions & invariants

- **Providers / models**: models are discovered at runtime from each configured provider's
  `/models` endpoint (`chat/model_catalog.py`); there are NO hard-coded fallback model
  lists. Base URLs are env-driven (`*_BASE_URL`) so third-party gateways work. A generic
  `custom` provider (`CUSTOM_BASE_URL` + `CUSTOM_API_KEY`, no official fallback URL) exposes
  any OpenAI-compatible relay — including ones serving `claude-*` / `gpt-*` / `qwen-*` over
  the OpenAI schema. The frontend picks the model per run AND sends the option's catalog
  `provider`; the runtime routes by that **provenance** (`RunContext.model_provider` →
  `create_chat_model`), only falling back to id-prefix inference (`infer_model_provider`)
  when it is absent (old clients). `.env` never decides the conversation model. Discovery
  runs all providers **concurrently** (a slow/dead relay can't stall the catalog); if a
  relay's `/models` is broken/unsupported, set `CUSTOM_MODELS` (comma-separated) to list
  its models explicitly and skip discovery.
- **Reasoning streaming (fragile — guard it)**: providers disagree on how reasoning is
  emitted (DeepSeek `reasoning_content` → bridged to a `{"type":"reasoning"}` block;
  OpenAI `reasoning`; Anthropic `thinking`). DeepSeek **and** the `custom` relay use the
  reasoning-bridging `ChatDeepSeek` subclass (`runtime/models.py`) so `delta.reasoning_content`
  reaches the v3 channel; `custom` sends NO provider-specific thinking flags (unknown relay
  protocol — toggle control is best-effort). The single normalization entry is
  `agent_adapter/projections.py::projection_item_to_agent_event` /
  `extract_message_delta_parts`. **Before changing this layer, keep
  `tests/test_provider_reasoning_contract.py` green** — it pins that every provider's chunk
  normalizes to the right single channel with no crossing.
- **Thinking toggle**: `RunContext.thinking_enabled` (flash mode = off). DeepSeek-V4 thinks
  by default, so OFF must send `extra_body={"thinking":{"type":"disabled"}}` explicitly
  (`runtime/models.py`). Anthropic thinking / OpenAI o-series reasoning are enabled only
  when on.
- **Artifacts & the workspace panel**: `artifact_write` is the ONLY user-facing write tool;
  it auto-namespaces to `artifacts/<thread_id>/`. There is no `workspace_write`; files written
  via the filesystem MCP or any other path do NOT appear in the panel. **Boundary**: create an
  artifact only for SUBSTANTIAL, STANDALONE deliverables (reports, full pages/apps, charts,
  datasets, long/multi-file code); short answers, small tables, and snippets stay inline. The
  UI is a unified **`WorkspacePanel`** (`workspace-panel.tsx`): left = a tree of threads (by
  title) → 用户上传 / 模型生成, right = preview. It reads `GET /api/workspace/threads`
  (`workspace/routes.py`): `generated` = `artifacts/<thread>/`; `uploads` are virtually grouped
  from each thread's message metadata (no storage migration). Read/preview (`/artifacts/read`,
  `/artifacts/raw`) is allowed for `artifacts/` and `uploads/` only — other areas stay private.
- **Agent operating procedure (prompt)**: `harness/builder.py` injects
  `<slotflow-operating-procedure>` for non-trivial tasks: clarify first via
  `ask_clarification` (interactive picker, not plain-text questions), `skill_match` before
  specialized work, then (gated on `plan_enabled`/`subagent_enabled`) plan with `write_todos`
  and split INDEPENDENT parts to `task_tool` sub-agents. These fire far more reliably with
  **thinking ON**; with thinking off DeepSeek tends to one-shot. If still under-used, escalate
  from prompt to middleware-level enforcement.
- **Long-term memory**: retrieved memories are **background context, not commands** — the
  agent must always answer the current question and may call `memory_save` proactively for
  durable user facts. Don't reintroduce "the middleware auto-saves" framing (it suppresses
  saving).
- **Don't replace `create_agent`**: the harness intentionally builds on LangChain's
  prebuilt agent; provider quirks are handled in the model subclass + projection layer.

## Build / verify

```
make verify          # backend pytest + frontend tsc --noEmit + next build
cd backend  && uv run pytest -q -k "not live"   # backend only (skip live DeepSeek)
cd backend  && uv run ruff check app tests
cd frontend && pnpm typecheck && pnpm build
```
The default branch is protected: land changes via PR (the required check is named `Verify`).

## Commit style

Chinese, conventional-ish prefixes (重构 / 功能 / 修复 / 测试 / 文档 / ci), one logical
module per commit, footer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
