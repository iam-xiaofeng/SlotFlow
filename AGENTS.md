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
  components/chat/      chat UI: chat-app + extracted hooks, sidebar(+context/search),
                        message-list(+parts), composer(+parts), artifact-panel
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
  when it is absent (old clients). `.env` never decides the conversation model.
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
- **Artifacts**: `artifact_write` is the ONLY user-facing write tool; it auto-namespaces to
  `artifacts/<thread_id>/`. There is no `workspace_write`. Workspace writes are enabled by
  default (only the sandboxed `artifact_write` is exposed). User-visible deliverables must
  go through `artifact_write` to appear in the panel.
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
