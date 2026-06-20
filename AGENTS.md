# AGENTS.md

Guidance for AI agents (and humans) working in the SlotFlow repository.

> **Rule: every code change must update this file in the same change.** If you touch
> behavior, architecture, conventions, or commands, reflect it here so AGENTS.md stays an
> accurate map of the repo. Keeping it current is part of "done", not an afterthought.

> **See also `HARNESS_NOTES.md`** — the harness engineering log (agent-behavior problems,
> what was tried, live-API test results, current state). Read it for the *why* behind the
> clarify-gate and the known behavioral gaps (subagent / skill-discovery / proactive memory).

> **⚠️ Rule: fix root causes, not symptoms.** Treating a bug as a surface problem — patching
> the visible symptom without tracing *why* it happens — does not make the problem smaller.
> It makes it bigger: the real cause is still there, the patch becomes one more thing that can
> break, the patches start fighting each other, and bugs multiply. Looking only at the surface
> and only ever fixing the surface turns hard problems into harder ones. Before "fixing"
> anything: reproduce it, find the actual mechanism, and audit the whole flow it lives in —
> the same shallow mistake is usually repeated elsewhere. A small correct fix at the root beats
> ten patches at the leaves. (Concrete example: the clarification re-popup was *not* fixed by
> suppressing duplicate questions — that was a symptom patch. The root cause was that the
> `ask_clarification` tool result echoed the question instead of carrying the user's answer
> back to the model; fixing the tool-result protocol fixed it for real. See `HARNESS_NOTES.md`.)

> **📓 Rule: every harness (model-constraint) change MUST be documented in exhaustive detail in
> [`HARNESS_NOTES.md`](HARNESS_NOTES.md).** The "harness" is everything that shapes or constrains
> the model: model selection, the clarify gate + `ask_clarification`, tool binding/dispatch,
> thinking-mode handling, skills discovery/matching, MCP search, sub-agent delegation, memory
> extraction, and the middleware chain. `HARNESS_NOTES.md` must let a reader starting from zero
> understand the **whole agent chain end to end** — for each piece: *why it is designed this way*,
> *what problems were hit*, *what caused them*, *how they were solved*, and *the real result of
> driving the current design through a live API* (e.g. prompts run via Claude Code / Codex against
> the actual model, not just unit tests). Write the full flow: model choice → clarify → tool calls →
> thinking → skills/MCP search → sub-agents → memory → final answer. Update it in the SAME change
> that touches harness behavior — this is part of "done".

> **🔍 Rule: everything written in `AGENTS.md` and `HARNESS_NOTES.md` MUST be verified by reading
> the actual code — never written from memory, assumption, or what context "suggests".** If you
> cannot point to the code that proves a statement, do not write it; go read the code first. Docs
> that drift from the code are worse than no docs.



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
                        message-list(+parts), composer(+parts),
                        workspace-directory-modal, workspace-panel
                        (two-pane preview; reuses artifact-panel's preview stage)
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
  its models explicitly and skip discovery. `custom` also validates discovered/manual models
  with a tiny `/chat/completions` probe by default so generic but unusable relay ids (for
  example GPT names that return 502/unsupported) do not appear in the selector; set
  `CUSTOM_VALIDATE_MODELS=false` only when that probe is too expensive or incompatible.
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
  datasets, long/multi-file code); short answers, small tables, and snippets stay inline.
  Complex planning workflows with human approval steps should write the final approved plan
  as an artifact after approval. The sidebar **工作区** button opens
  `workspace-directory-modal.tsx`, a centered
  global directory over the same `/api/workspace/threads` data; thread folders are collapsed
  by default (search expands matches), and clicking a file switches to its owning conversation
  (except `未归类产物`) and opens the right preview panel on that file. The right
  `WorkspacePanel` does not keep a permanent left file tree; its title is a dropdown file
  selector grouped by thread → 用户上传 / Agent 产物 so preview width stays available. It reads
  `GET /api/workspace/threads` (`workspace/routes.py`): `generated` = recursive
  files under `artifacts/<thread>/`; `uploads` are virtually grouped from each thread's
  message metadata (no storage migration). The workspace route returns only threads that
  actually have uploads or generated files, keeping empty chats out of the file tree.
  Legacy files under `artifacts/` that are not namespaced to a known thread are surfaced as
  `未归类产物`, so older outputs remain findable. Read/preview (`/artifacts/read`,
  `/artifacts/raw`) is allowed for `artifacts/` and `uploads/` only — other areas stay private.
- **Agent operating procedure (prompt)**: `harness/builder.py` injects
  `<slotflow-operating-procedure>` for non-trivial tasks: clarify first via
  `ask_clarification` (interactive picker, not plain-text questions), `skill_match` before
  specialized work, then (gated on `plan_enabled`/`subagent_enabled`) plan with `write_todos`
  and split INDEPENDENT parts to `task_tool` sub-agents. These fire far more reliably with
  **thinking ON**; with thinking off DeepSeek tends to one-shot.
- **HITL clarification = LangGraph native `interrupt()`/resume** (rewired 2026-06-21, see
  `HARNESS_NOTES.md` §12). There are two clarification entry points, both pausing the graph the
  same way — no separate `SlotFlowClarificationMiddleware` anymore (it was deleted):
  - **Voluntary (the model asks)**: `tools/builtins.py::ask_clarification_tool` calls
    `interrupt(build_clarification_payload(...))` and returns the resume value as its result.
  - **Forced gate (pro + ultra)**: `middleware/clarify_gate_middleware.py`
    (`SlotFlowClarifyGateMiddleware`) runs one cheap structured **triage** on the **first model
    step** of a fresh user turn; if not actionable, `abefore_model` calls `interrupt(payload)` and,
    on resume, injects the user's answer **verbatim** as a `HumanMessage` so the model proceeds.
    Prompts alone can't stop a model from one-shot-guessing, so this moves the decision into the graph.
  - The user's answer arrives via `Command(resume=<answer>)` and **is** the tool result / user
    message — no "rewrite the answered tool message" step. `build_clarification_payload` (now in
    `app/harness/clarification.py`) always appends a free-text `其他（自己输入）` option LAST; the
    frontend renders any 其他/other/specify option as an input box.
  - **Resume detection is server-side and provider-agnostic**: `agent_adapter/streaming.py` checks
    `graph.aget_state(config).interrupts` at turn start — if one is pending, the incoming user
    message is treated as the resume value; otherwise a normal turn starts. So the **frontend keeps
    sending the answer as an ordinary message** (no frontend change).
  - **Clarification events come ONLY from a pending interrupt**, never re-scanned from message
    history. `clarification_event_from_interrupt` replaced the old `clarification_event_from_snapshot`
    history scan, whose stale re-derivation was the **root cause of the answered-clarification
    re-popup bug**. An answered clarification leaves no pending interrupt → it cannot re-pop.
  - Skill-first / plan-first / delegate guidance lives in the `<slotflow-operating-procedure>`
    prompt, NOT in the gate (removed 2026-06: on DeepSeek thinking such directives are necessarily
    soft, so it only duplicated the prompt and added conflict surface).
  - **Hard-won provider rules (DeepSeek thinking-mode, live-verified — do NOT regress)**: (1) the
    triage `ainvoke` MUST pass `config={"callbacks": []}` or its tokens pollute the user stream;
    (2) NEVER force `tool_choice` (`"Thinking mode does not support this tool_choice"`); (3) prefer
    `interrupt()`/resume over any synthesized-response-then-continue scheme — `interrupt` pauses at
    tool execution with the model's REAL tool call (real `reasoning_content`), sidestepping the
    `"reasoning_content ... must be passed back"` trap that killed the old `wrap_model_call` path.
  - The gate's `abefore_model` MUST `except GraphBubbleUp: raise` BEFORE its fail-open
    `except Exception` (an interrupt is raised as a `GraphInterrupt`, an `Exception` subclass —
    swallowing it would defeat the pause). On resume the hook replays, so triage runs once more
    (cheap, benign). Only the first step is constrained; never gates twice in a thread (anti-loop).
    Gated by `clarify_gate_enabled` (default on) + `run_context.mode in {pro, ultra}`. Scripted-model
    graph tests set `clarify_gate_enabled=False`. Live-validated against `deepseek-v4-pro`:
    underspecified requests clarify, the answer resumes the run, and the clarification does not re-pop.
- **Long-term memory (cross-conversation, proactive)**: memory is **global, not thread-scoped** —
  `store.search_memories` ranks across ALL threads (`thread_id` is only a relevance bonus), so a
  fact learned in one conversation is retrievable in any other. `long_term_memory.py`:
  `before_agent`/`wrap_model_call` retrieve and inject relevant memories as **background context,
  not commands** (the agent must still answer the current question). Saving has three paths:
  (1) explicit `memory_save` tool — the model is nudged to call it proactively for durable facts;
  (2) an explicit `请记住X` synchronous fast-path in `after_agent`; (3) **proactive background
  extraction** — `aafter_agent` fires `memory/extractor.py` (`SlotFlowMemoryExtractor`)
  fire-and-forget on the server loop, which asks the model to pull durable preferences/profile/
  topic facts from the finished turn and saves them via `store.add_memory` (which dedups by
  `source_run_id` and `kind+content`). This replaced the old brittle Chinese-regex extraction.
  The extractor reuses the conversation model with `config={"callbacks": []}`; latency is hidden
  because it does not block the run. Gated by `proactive_memory_extraction_enabled` (default on);
  scripted-model graph tests set it `False`. Don't reintroduce "the middleware auto-saves" framing
  in the prompt (it suppresses the model's own `memory_save`).
- **Sub-agent concurrency cap**: `middleware/subagent_limit_middleware.py`
  (`SlotFlowSubagentLimitMiddleware`, `after_model`) truncates excess parallel `task_tool` calls
  on a single model step down to `subagent_max_concurrent` (default 3), a graph-level guard that
  preserves non-`task_tool` calls and the message's `reasoning_content`. Registered when
  `subagent_limit_enabled` + `features.subagent_enabled`.
- **Skill discovery (cross-tool, cached)**: `skill_match` → `find-skills` → `search_skill_repos`.
  Skills use the open **SKILL.md** standard shared by Claude Code, OpenAI Codex (`.agents/skills`)
  and GitHub Copilot, so a Skill written for any of them installs into SlotFlow unchanged — there
  is no Codex-specific tool; `search_skill_repos` returns GitHub matches plus authoritative
  `ecosystem_sources` (Anthropic / Codex / skills.sh) to browse. Local installed-skill matching
  (`match_installed_skills` in `tools/customization.py`) is memoized with a short TTL so the skills
  preflight and a later `skill_match` in the same turn don't re-scan disk; `skill_install` calls
  `invalidate_skill_match_cache()`.
- **Don't replace `create_agent`**: the harness intentionally builds on LangChain's
  prebuilt agent; provider quirks are handled in the model subclass + projection layer.

## Roadmap (next steps)

The 2026-06 DeerFlow-alignment pass (clarify slim-down, proactive memory, sub-agent cap, skill
cache — see `HARNESS_NOTES.md` §11) is done and live-validated. Known follow-ups, roughly ordered:

1. **Memory dedup / near-duplicates** — the background extractor and the model's own `memory_save`
   can both fire in one turn and store near-duplicate facts (observed: a Chinese preference saved
   twice with different wording). `store.add_memory` only dedups on exact `kind+content`. Make
   background extraction skip when `memory_save` already fired this turn (current `run_id` match in
   `memory_save_tool_used_for_run` is too strict), and/or add a light semantic merge.
2. **Memory normalization bloat** — `store.py::canonicalize_memory_content` still carries
   hand-written Chinese regex with hardcoded values ("控制工程"/"研究生"). LLM-extracted content is
   already clean; this can be simplified once the extractor is the primary path.
3. **Sub-agent live test** — the concurrency cap is unit-tested only; add an end-to-end live check
   that real parallel `task_tool` delegation is capped and synthesized correctly.
4. **MCP context bloat** — consider DeerFlow's `tool_search` deferred-schema pattern (inject tool
   names, load full schemas on demand) when many MCP tools are configured.
5. **Ship it** — the harness-redesign branch is unpushed; open a PR (required check: `Verify`).

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
