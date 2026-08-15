# AGENTS.md

Guidance for AI agents (and humans) working in the SlotFlow repository.

> **✅ 2026-07-04 大扫除完成,等待人工验证后提 PR**(分支 `cleanup/audit-20260703`)。
> 改动与问题总览:[`docs/cleanup-2026-07-03-report.md`](docs/cleanup-2026-07-03-report.md);
> API 调用链路:[`docs/api-call-chains.md`](docs/api-call-chains.md);
> Agent 上下文压缩、渐进式工具披露与 Codex/Pi/DeerFlow 调研（2026-07-17）：[docs/research/agent-context-tool-disclosure-2026-07-17.md](docs/research/agent-context-tool-disclosure-2026-07-17.md);
> 工程细节:`HARNESS_NOTES.md` §32;断点续传:
> [`HANDOFF_CROSS_SESSION_20260703.md`](HANDOFF_CROSS_SESSION_20260703.md)。
> 行为要点:摘要哨兵/输入冻结两个 P0 已修;tool.status 现挂消息子流 tool_calls 投影
> (所有工具执行前端可见);记忆保存由 LLM 唯一改写(store 只做卫生);Docker 沙箱为
> **持久具名共享容器**(空闲只停不删,线程目录隔离),守护进程可自动拉起。
> **⚠️ 多个 Claude 会话不可同时写本仓库**(踩踏事故见 HARNESS_NOTES §32.7)。


> **Rule: every code change must update this file in the same change.** If you touch
> behavior, architecture, conventions, or commands, reflect it here so AGENTS.md stays an
> accurate map of the repo. Keeping it current is part of "done", not an afterthought.

> **See also `HARNESS_NOTES.md`** — the harness engineering log (agent-behavior problems,
> what was tried, live-API test results, current state). Read it for the *why* behind the
> clarify-gate and the known behavioral gaps (subagent / skill-discovery / proactive memory).
> **2026-06-30**: the harness is now a LangGraph native `StateGraph` (node + edge), not
> `create_agent` + middleware. See `HARNESS_NOTES.md` §13 and `docs/refactor-plan.md`.

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
> extraction, and the graph node chain. `HARNESS_NOTES.md` must let a reader starting from zero
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
LiteLLM-backed multi-provider reasoning streaming plus an optional OpenAI-compatible relay.

## Architecture (one request, end to end)

1. **Frontend** (`components/chat/chat-app.tsx` + `hooks/use-chat-stream.ts`) POSTs to the
   chat stream route with a `ChatStreamRequest` (message, `model_name`, `provider`, `mode`,
   `thinking_enabled`, `files`).
2. **`chat/routes.py`** persists the user message, then `chat/run_config.build_run_config`
   turns the request into a `RunConfigBundle = {config, context}`:
   - `config["configurable"]["thread_id"]` — LangGraph's key for multi-turn checkpoint state.
   - `RunContext` — SlotFlow business switches: `model_name`, **`model_provider`**, `mode`,
     `thinking_enabled`, plan/subagent flags, files.
3. **`chat/runtime/adapter.py` (RuntimeBackedAgentAdapter)** builds one `ChatLiteLLM` model via
   `runtime/models.create_chat_model` (routed by `RunContext.model_provider`) and assembles
   the graph via `harness/builder.build_slotflow_harness_graph` → `harness/graph.build_slotflow_graph`,
   a **LangGraph native `StateGraph` (explicit node + edge)** plus the tool registry
   (`harness/tools/registry`). The graph no longer uses LangChain `create_agent` or
   `AgentMiddleware`; each former middleware's logic lives in `harness/steps/*` as a stateless
   pure function called by a node, with order fixed by edges (see topology below).
4. The graph streams with the **LangGraph v3 projection protocol**; each item is normalized
   by **`chat/agent_adapter/projections.py`** into a SlotFlow `AgentEvent` (`message.delta`
   with channel `reasoning`/`content`, `state.snapshot`, `tool.delta`, `tool.status`,
   `clarification.requested`, `todo.updated`, `run.*`).

**Graph topology (node + edge):**

```
START → prepare → pre_model → SlotFlowSummarizationMiddleware → agent → post_model → route
                                                                                                    ├─ tools → pre_model   (ReAct loop; ask_clarification interrupts here)
                                                                                                    ├─ pre_model           (todo enforcement retry)
                                                                                                    └─ finalize → END
```

- `prepare` (once/turn, all `before_agent`): runtime summary, uploads, long-term-memory
  retrieval, artifact baseline.
- `pre_model` (every step): dangling-tool-call repair + model-input projection, a byte-stable
  system prefix, and ALL volatile context (recalled long-term memory, todo-state
  reminder/enforcement) appended as one trailing `<system-reminder>` via `model_input_suffix`.
- `SlotFlowSummarizationMiddleware` (own node so the projection layer filters its internal
  summary stream by node name): compresses history when token threshold exceeded.
- `agent`: `model.bind_tools(tools)` call; reads `llm_input_messages` + `system_prompt`.
- `post_model`: todo parallel-call guard + dynamic todo enforcement, then sub-agent concurrency
  cap on `task_tool`.
- `route`: todo enforcer → `pre_model`; otherwise `tools_condition` → `tools` (ToolNode +
  SlotFlow tool-safety wrapper) or `finalize`.
- `finalize` (once/turn, all `after_agent`): artifact new-entries, long-term-memory explicit
  save + background LLM extraction.
5. **`chat/sse.py`** encodes events as SSE; the frontend consumes them
   (`lib/chat-stream.ts` + `hooks/use-chat-stream*`) and renders the message, reasoning,
   todos, clarification picker, and workspace files.

Two boundaries carry most of the design: **RunContext vs config.configurable** (business
switches vs LangGraph runtime keys), and the **LiteLLM model boundary** —
provider/version quirks are normalized before the projection layer maps LangGraph messages into clean
`AgentEvent`s.

## Layout

```
bootstrap.sh            first-run setup for system/runtime deps, host integrations, and Docker
Makefile                root developer commands (`verify`, `dev`, `kill`)
backend/app/
  chat/                 chat API, Pydantic models, SQLite repository, run config, SSE
  chat/runtime/         per-run assembly: env · config · models · checkpointer · adapter
  chat/agent_adapter/   LangGraph v3 projection -> SlotFlow AgentEvent
                        (events · projections · streaming)
  harness/              the agent graph: builder, graph (node+edge), steps (pure node
                        logic), tools, skills, mcp, memory, sandbox, subagents
  harness/middleware/   graph behavior switches only (SlotFlowMiddlewareConfig); no
                        AgentMiddleware classes (deleted in node+edge refactor)
  harness/sandbox/      workspace boundary + lazy Docker code-execution sandbox
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

- **First-run setup**: `./bootstrap.sh` is the root setup entry for machines that cannot yet run
  `make`, `uv`, or frontend dependency commands. It installs/validates system Makefile
  prerequisites (`make`, `curl`, `git`, Python/build tools, `fuser` via `psmisc` where available),
  installs MarkItDown's ffmpeg/ExifTool system helpers through supported package managers,
  installs `uv`, installs Node plus the `packageManager` pnpm version from `frontend/package.json`
  (Volta fallback for user-local Node), installs/refreshes Agent Reach from its configurable Git
  source with `uv tool` and prepares its zero-configuration channels on the **host**, runs `uv sync`
  in `backend/` (including `markitdown[all]` plus `markitdown-ocr[llm]`), runs `pnpm install
  --frozen-lockfile` in `frontend/` (including the locked `@playwright/mcp` and matching
  `playwright`), installs Playwright's exact Chromium shared-library set on apt hosts, and downloads
  Chromium, copies `backend/.env_example` to `backend/.env` only when no local `.env` exists, then
  prepares the **Docker sandbox** end to end. Agent Reach is deliberately not installed into that
  sandbox; rerunning `bootstrap.sh` is its refresh path, and optional cookie/login channels remain
  user-managed. Docker setup is
  best-effort for common Linux families (apt/dnf/yum/pacman/apk/zypper, plus WSL); it installs
  Docker Engine if missing, adds the user to the `docker` group, enables systemd in
  `/etc/wsl.conf` on WSL hosts without it, starts the daemon (systemctl → service → rc-service →
  direct `dockerd` fallback, mirroring `docker_engine.py::ensure_daemon`), merges CN registry
  mirrors into `/etc/docker/daemon.json` only when a direct Docker Hub pull fails and no existing
  mirrors are configured, and pre-pulls the sandbox image (`SLOTFLOW_DOCKER_SANDBOX_IMAGE`, with
  `SLOTFLOW_DOCKER_IMAGE` as a bootstrap-only alias; default `python:3.12`). Adding a non-root
  user to the docker group still requires a fresh login before non-sudo Docker access is available.
  Use `SLOTFLOW_SKIP_SYSTEM_PACKAGES=1` to skip OS package installation,
  `SLOTFLOW_SKIP_AGENT_REACH=1` to skip Agent Reach, `SLOTFLOW_SKIP_PLAYWRIGHT_BROWSER=1`
  to skip the Chromium download, and `SLOTFLOW_SKIP_DOCKER=1` to skip all Docker setup.
  Bootstrap-only knobs: `SLOTFLOW_NODE_VERSION`, `SLOTFLOW_PNPM_VERSION`,
  `SLOTFLOW_AGENT_REACH_SOURCE`, `SLOTFLOW_DOCKER_REGISTRY_MIRRORS`,
  `SLOTFLOW_DOCKER_DAEMON_WAIT_SECONDS`.
- **README onboarding**: `README.md` and `README_zh.md` are the first-run/onboarding
  documents. Their `bootstrap.sh` and `Makefile` sections must stay mechanically consistent
  with the actual root `bootstrap.sh`, root `Makefile`, `frontend/package.json`
  `packageManager`, and `backend/.env_example`; verify those files before changing setup docs.
- **Async route boundary**: FastAPI endpoints remain `async`, but any potentially slow local
  filesystem/subprocess work must not run directly on the event loop. Upload persistence
  (`uploads/routes.py` -> `SlotFlowUploadStore.save_upload`), artifact directory/preview/delete
  work (`workspace/routes.py` -> `SlotFlowWorkspace`), and user-managed Skills upload/install/
  update/reorder/delete filesystem or CLI operations (`skills/routes.py`) are dispatched through
  Starlette `run_in_threadpool`. Keep new route-level file parsing, large writes, recursive copies,
  deletes, and external CLI invocations behind the same boundary unless the implementation is truly
  async. Chat and memory SQLite stores are still synchronous internally, but async routes call them
  through `run_in_threadpool`; graph long-term-memory retrieval/save paths use `asyncio.to_thread`,
  and the `prepare` graph node is async so memory search does not occupy the event loop. Model-facing
  workspace, network, and sandbox tools keep synchronous `.invoke()` compatibility for tests/scripts
  while exposing async `StructuredTool` coroutines that dispatch blocking local file parsing,
  `httpx.Client`, and Docker subprocess work through `asyncio.to_thread` during async graph runs.
- **Providers / models**: `chat/litellm_provider.py` is the only provider/catalog
  boundary. `configured_native_provider_names()` calls LiteLLM's public
  `get_valid_models(check_provider_endpoint=False)` against the process environment; for each
  configured provider, `agent_models_for_provider()` filters LiteLLM's bundled
  `models_by_provider` metadata to `mode=chat` plus `supports_function_calling`. Selectable native
  ids are provider-qualified (`provider/model`), so runtime routing needs no SlotFlow provider map
  or model-name inference table. The frontend carries the open-string provider provenance on every
  run. `custom` remains the sole SlotFlow-specific transport configuration:
  `CUSTOM_BASE_URL` + `CUSTOM_API_KEY`, LiteLLM endpoint discovery, optional comma-separated
  `CUSTOM_MODELS`, and a neutral `SLOTFLOW_RELAY_USER_AGENT`. LiteLLM catalog work runs in
  `asyncio.to_thread`, so `/api/chat/models` does not block FastAPI's event loop. The model transport
  timeout is provider-agnostic: `SLOTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS` is read when
  `runtime/models.py` builds each `ChatLiteLLM` instance and defaults to 300 seconds. Do not restore
  the old hard-coded 30-second timeout: reasoning models and relays may legitimately exceed it before
  the first stream chunk or between chunks, which LiteLLM surfaces as `MidStreamFallbackError`
  wrapping a socket read timeout. Updating the pinned LiteLLM packages updates native provider/model
  metadata; do not add hand-maintained
  Gemini/Bedrock/Mistral/etc. lists or credential maps.
- **Reasoning streaming**: every provider is constructed through the minimal
  `chat/litellm_provider.py::ChatLiteLLM` subclass of `langchain_litellm.ChatLiteLLM`.
  The boundary rule is provider-agnostic: outbound assistant `content` carries only text/media;
  opaque reasoning state rides the two top-level carriers LiteLLM normalizes for every provider —
  `reasoning_content` (text, forwarded by upstream) and `thinking_blocks` (signed/opaque blocks,
  restored by the subclass and by two module-converter wrappers because
  `langchain-litellm==0.7.0` drops the field in both directions). Before a follow-up request the
  subclass removes assistant `reasoning`/`thinking` metadata blocks (including `non_standard`
  wrappers) from `content` — canonical LangChain `reasoning` blocks otherwise reach DeepSeek and
  fail with `unknown variant reasoning, expected text` — and consolidates streamed
  `thinking_blocks` partials into complete signed blocks (missing `thinking_blocks` silently
  disables Anthropic extended thinking on tool-loop continuations). Only state the provider itself
  produced is echoed back; do not remove the top-level carriers, move the workaround into provider
  branches, or bump `langchain-litellm` without `tests/test_provider_reasoning_contract.py` green.
  LiteLLM owns provider request translation, streamed
  reasoning/thinking normalization, tool-call chunks, usage, and assistant reasoning round-trips
  after tool results. SlotFlow checks only LiteLLM's public
  `get_supported_openai_params(model=...)`: when `reasoning_effort` is supported, thinking ON sends
  `high` and OFF sends `none`; otherwise SlotFlow sends no thinking parameter. There are no
  DeepSeek/Anthropic/OpenAI capability branches. All native providers use LiteLLM
  `completion/acompletion` and provider-qualified `provider/model` ids. SlotFlow does not add the
  `openai/responses/` prefix; `_skip_responses_api_bridge=True` is passed as an internal LiteLLM
  model parameter so GPT-5 models with tools/reasoning cannot be auto-routed to Responses by LiteLLM.
  `custom` relays also remain on Chat Completions. LangGraph v3 typed `.reasoning` / `.text`
  channels are preferred; `agent_adapter/projections.py` retains only canonical reasoning/thinking
  blocks and LiteLLM `reasoning_content`. Keep `tests/test_provider_reasoning_contract.py` (including
  the final tool-follow-up payload), `tests/test_model_catalog.py`, and the runtime Chat
  Completions-routing tests green. **Streamed tool-call name recovery**: some OpenAI-compatible
  relays (observed live: grok-4.5) stream tool-call deltas so that LangChain's parsed
  `message.tool_calls[i]["name"]` comes out empty while the raw
  `additional_kwargs["tool_calls"][j]["function"]["name"]` still holds it — an empty name makes the
  ToolNode unable to dispatch, so EVERY tool (core ones too) fails closed as
  `tool_not_activated`/unknown. `litellm_provider.py::repair_streamed_tool_call_names` backfills the
  name (by call `id`, strict positional fallback) and the `agent`/`agent_sync` nodes call it on every
  response; subagents inherit it via the shared graph. Pinned by `tests/test_tool_call_name_repair.py`.
- **Streaming merge contract**: `message.delta` is the live user-visible stream; final
  `state.snapshot` is a reconciliation source, not permission to erase already-streamed text.
  Both `chat/routes.py::select_assistant_content` and
  `hooks/use-chat-stream-helpers.ts::mergeAssistantContent` keep the longer/prefix-compatible
  content so a shorter snapshot cannot make the answer visibly shrink at run end. Reasoning uses
  the same principle via `select_assistant_reasoning_content` / `mergeReasoningContent`. Snapshot
  assistant messages with tool calls are intermediate ReAct steps, not final user-visible answers;
  normalization marks them with `has_tool_calls`, and backend/frontend content selectors skip them.
  `chat/sse.py::make_error_event` recursively unwraps LangGraph/AnyIO `ExceptionGroup` failures so
  `run.error` and the persisted run expose the informative leaf exception instead of the generic
  `unhandled errors in a TaskGroup` wrapper; cancellation semantics and tracebacks stay server-side.
- **Request-budget and development reload boundaries**: `title_generation.py` never creates a
  model when `SLOTFLOW_TITLE_MODEL_ENABLED=false`; it derives a deterministic title from the first
  user message, and `.env_example` keeps this no-extra-request mode as the default. Do not route this
  background concern to DeepSeek or another provider. Proactive memory extraction is also one extra
  post-turn call and may be disabled with `SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION=false` for strict-RPM
  relays without changing the user-selected model used by the main agent/subagents. `make dev` limits
  Uvicorn reload watching to `backend/app`, so files written under `.slotflow/workspace` cannot restart
  an active stream. The adapter suppresses only LangGraph's exact Pregel v3 experimental warning at
  the `astream_events` boundary; unrelated warnings remain visible.
- **Checkpointer must be persistent (2026-08-14)**: the model's view of a conversation comes ONLY
  from the checkpointer — `build_agent_input` sends just the new user message each turn and
  `agent_adapter/streaming.py` restores history by `thread_id`. The default was `memory`
  (`InMemorySaver`) while `make dev` runs `uvicorn --reload`, so **every backend edit wiped every
  thread's model-side history**; the UI kept showing the full conversation because messages live in
  a different store (chat SQLite), and the only visible symptom was the composer's token meter
  dropping from ~36k back to ~7k (= system prompt + tool schemas + one message). Default is now
  `sqlite` (`SLOTFLOW_CHECKPOINTER_BACKEND`, path `SLOTFLOW_CHECKPOINTER_SQLITE_PATH`);
  `create_async_checkpointer` already supported it. Pinned by
  `tests/test_context_runtime.py::test_conversation_history_survives_a_backend_restart_with_sqlite_checkpointer`,
  which uses two independent savers over one file to simulate a restart. See `HARNESS_NOTES.md` §61.
- **Context epochs, local usage metrics, and a constant tool set**: every runtime run attaches a local `RunUsageCollector`; `run.usage` is persisted in SQLite `run_metrics` without prompt/tool content. Missing provider cache fields are `unknown`, never a fabricated miss. Context windows resolve from `SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON`, then LiteLLM's bundled metadata, then a conservative local default; input budget reserves `SLOTFLOW_CONTEXT_RESERVE_TOKENS`. The resolved window rides `run.prepared` (`context_window_tokens`/`context_input_budget_tokens`/`context_window_source`) so the UI knows the ceiling before any tokens are counted, and `run.usage` adds `context_tokens` (the **main agent node's** most recent successful prompt size = current window occupancy — NOT the per-run sum, and NOT simply the last call, because the summarization node and `task_tool` sub-agents share the run's callbacks and their prompts are unrelated to window occupancy; sub-agent child graphs also name their node `agent`, so attribution additionally requires the call to start outside any tool, tracked via `on_tool_start`/`on_tool_end` depth) plus the same window fields; the composer renders a live `ComposerContextMeter` (used / max tokens) from these two events, and restores it on thread open from `GET /api/chat/threads/{id}/context-usage` (previously the meter only existed in page-session memory and vanished on reload). Summarization now stores a model-facing `context_epoch` while canonical `messages` remain intact in the checkpointer. Within an epoch the frozen compacted prefix is reused and new messages append; `context_archive_search/read` can inspect only the current graph state's canonical history. The epoch's `source_signature`/`source_message_count` are computed over the SAME `repair_dangling_tool_calls(messages)` view that `pre_model` re-derives each turn (`harness/graph.py::project_with_context_epoch`); computing it over the raw messages instead made the signature mismatch whenever history held a dangling tool call, so the epoch reset every turn, summarization re-fired every turn, and the fixed keep-window slid until earlier user turns were dropped (the 2026-07-18 "compression forgets earlier messages" fix). **Progressive tool disclosure was removed on 2026-08-14; the model-facing tool set is now CONSTANT for a whole run** (`harness/graph.py::_GraphInputs.bound_model` binds once). The provider's cacheable prefix is `tools → system → messages`, so every loader-driven `bind_tools` change invalidated the entire prefix cache from the first token — the `*_tools` loaders, `promoted_tool_names` promotion, and the `tool_not_activated` fail-closed gate all paid a full cache reset per activation, and the loader description re-inlined the whole gated catalog into that same prefix anyway. What replaced it: everyday tools bind directly; browser automation (playwright's ~21 `browser_*` schemas) is owned by the `browser` vertical sub-agent and reached through `task_tool`; MCP collapses to the fixed `mcp_docs`/`mcp_call` pair (below). `harness/tool_spaces.py` keeps only the classification function, which sub-agent tool-face filtering still needs. The system prefix is likewise byte-stable: `pre_model` writes `inputs.system_prompt` verbatim, and ALL volatile context — recalled long-term memory, per-step todo control — rides the trailing `model_input_suffix` `<system-reminder>`; `tests/test_harness_graph_integration.py::test_system_prefix_stays_byte_identical_across_turns` pins both halves. The surviving order-preserving union reducer (`harness/state.py::merge_ordered_unique`) now serves the `used_skills` compaction ledger, which has the same concurrent-write shape: several `skill_read` calls can land in one step and each returns a `Command(update={"used_skills": [...]})`, which without a reducer trips `INVALID_CONCURRENT_GRAPH_UPDATE`. Child agents have no recursive delegation/todo/HITL and receive at most three explicit tool spaces, with `all/*` rejected. Context-overflow BadRequest errors progressively shrink only model input before configurable retries; transient pre-response transport/rate failures use configurable exponential retry, while mid-stream/tool side effects are never replayed.
- **Agent Reach host bridge**: Agent Reach is installed/refreshed by `bootstrap.sh` with `uv tool`
  and initialized from `~/.agent-reach`; it is deliberately **not** installed or mounted into the
  Docker sandbox. `harness/tools/agent_reach.py` is the only model-facing boundary. It exposes five
  read-only StructuredTools (`agent_reach_status`, `agent_reach_web_search`,
  `agent_reach_read_url`, `agent_reach_github_search`, `agent_reach_youtube_metadata`) and never
  accepts an executable, argv, shell fragment, install/update/configure action, or remote write.
  `FixedHostCommandRunner` resolves only `agent-reach`, `mcporter`, `curl`, `gh`, and `yt-dlp` from
  fixed user/system bin directories, invokes argv arrays with `shell=False` semantics and stdin
  closed, fixes cwd to `SLOTFLOW_AGENT_REACH_HOME`, bounds time/output, and redacts secret-valued
  environment strings from errors/results. The Jina/YouTube tools reuse the public-URL/private-IP
  guard from `harness/tools/network.py`; the whole bridge also obeys `SLOTFLOW_NETWORK_ENABLED`.
  Main and child agents receive the same fixed tools. The system prompt directs the model to call
  `agent_reach_status` before multi-platform research and never pretend unavailable channels work.
  Rerunning `bootstrap.sh` is the only repository-provided refresh path; there is intentionally no
  maintenance command/tool, and optional cookie/login channels remain user-controlled.
- **MCP boundary: two fixed tools, never N schemas (2026-08-14)**: MCP tools are no longer bound
  individually. `harness/mcp/proxy.py` exposes exactly `mcp_docs(query, server)` and
  `mcp_call(server, tool, arguments)` regardless of how many servers are configured, so the model's
  tool array — and therefore the provider's cacheable prefix — does not change when a user adds a
  server. `mcp_docs` searches a manual generated from the live tool definitions (name, description,
  argument names/types/required), purely locally: no model call, no network. `mcp_call` invokes the
  real tool host-side through the already-open `MultiServerMcpToolProvider` session; unknown
  server/tool names return a structured error pointing back at `mcp_docs` instead of failing the
  run, and oversized results are truncated with an explicit note. `MCP_SERVER_METADATA_KEY`
  (`slotflow_mcp_server`) is stamped by the loader during its per-server load because
  `langchain_mcp_adapters` records only MCP annotations/`_meta`, not the origin server — without it
  same-named tools from different servers are indistinguishable. Unsafe host-execution tools are
  filtered before the proxy is built, so they cannot reappear through the manual. The alternative
  design (run MCP clients inside the Docker sandbox and let the model write code) was rejected for
  SlotFlow specifically: the default server is playwright (stdio + pnpm + Chromium, which does not
  run in `python:3.12`), it would require injecting MCP credentials into the container, and it would
  make MCP depend on Docker. `build_mcp_status_prompt(..., proxy_available=...)` reflects whether
  the pair was actually bound — describing a tool that is not bound is how a model gets told to call
  something that does not exist.
- **Built-in Playwright MCP**: `chat.runtime.config.build_playwright_mcp_server()` appends a
  protected/pinned `playwright` stdio preset by default. It launches the pnpm-locked upstream MCP
  through `frontend/scripts/playwright-mcp.mjs`; that silent fixed launcher resolves the matching
  locked Chromium via `chromium.executablePath()` instead of requiring system Chrome. The preset is
  headless + isolated, blocks service workers, omits image responses, disables codegen and optional
  vision/PDF/devtools caps, does not allow unrestricted file access, and fixes cwd/output under the
  SlotFlow workspace. Its private/loopback origin list is defense-in-depth only (upstream explicitly
  says origin filters do not cover redirects), not a substitute for treating browsed pages as
  untrusted. `SlotFlowMcpServerConfig.stateful=True` makes `MultiServerMcpToolProvider` keep one MCP
  `ClientSession` open across navigate/snapshot/click calls. `RuntimeBackedAgentAdapter` creates that
  provider/session per run and closes it in `finally`; concurrent runs never share a page/profile.
  MCP discovery is isolated per server: an unavailable optional server is logged and omitted without
  closing healthy stateful sessions or aborting the chat run; cancellation still closes every opened
  stack and propagates. Stateless MCP servers keep the original one-session-per-call adapter behavior.
  The preset can be
  toggled but cannot be deleted or shadowed by a user HTTP server. `bootstrap.sh` installs the locked
  package, runs official `playwright install-deps chromium` on apt hosts, and downloads Chromium;
  non-apt hosts receive a precise shared-library warning. No separate maintenance command exists.
- **MarkItDown local conversion**: `harness/tools/markitdown.py` exposes exactly one model tool,
  `convert_file_to_markdown`. The top-level function of the same name calls upstream
  `MarkItDown.convert_local()` only—never permissive URL conversion—and the tool resolves `path`
  through `SlotFlowWorkspace`; optional output is normalized into `artifacts/<thread>/`. All-format
  Python extras and the official `markitdown-ocr` plugin are uv-locked; bootstrap additionally
  installs ffmpeg + ExifTool where the host package manager is supported. Archives are bounded by
  compressed input, entry count and total uncompressed bytes; Vision work is bounded by PDF pages
  and embedded-image count; converted output is bounded before entering model context/workspace.
  When `use_vision=true`, a Vision-capable selected run model (checked through LiteLLM's public
  `supports_vision`) is wrapped behind the tiny OpenAI `chat.completions.create` shape expected by
  MarkItDown. Dedicated `SLOTFLOW_MARKITDOWN_VISION_{MODEL,BASE_URL,API_KEY}` settings can instead
  create an OpenAI-compatible client without exposing its key. Pure images use MarkItDown's image
  converter; embedded/scanned PDF/Office images use the upstream OCR plugin. A text-only model or
  missing dedicated client does not trigger a blind call: standard extraction runs and the result
  carries an explicit Vision warning. The sync conversion/LLM work runs via the existing threaded
  StructuredTool async boundary. Main and child agents receive the same tool.
- **Network tools**: `web_fetch` and `web_search` live in `harness/tools/network.py` and remain
  read-only, public-URL-only tools under `SlotFlowSandboxConfig` limits. `web_search` uses plain
  HTML search endpoints with fallback: Bing HTML first, then DuckDuckGo Lite. The parser filters
  search-engine navigation/self links, decodes Bing `/ck/a?u=...` redirect targets, and decodes
  DuckDuckGo `/l/?uddg=...` targets before returning compact `{title,url}` results. This fallback is
  intentional because some networks terminate DuckDuckGo TLS with
  `UNEXPECTED_EOF_WHILE_READING`; do not collapse it back to a single search URL. The tools are
  dual sync/async `StructuredTool`s: `.invoke()` remains synchronous, while async graph execution
  runs the blocking `httpx.Client` fetch/search in `asyncio.to_thread`.
- **Chat scroll behavior**: `frontend/src/components/chat/message-list.tsx` anchors each newly
  sent user message near the top of the chat viewport while the assistant response streams below
  it, so a new turn starts where the user can read from the question downward instead of being
  pushed to the bottom. A temporary bottom spacer is measured from the real scroll viewport height
  and the latest user-bubble height, exists only during that streaming turn, and is not animated;
  otherwise the browser's `maxScrollTop` can make the "top" anchor impossible while assistant output
  is still short. While the answer is still streaming, once the real output end grows below the
  visible viewport, auto-follow switches to the latest assistant output so long responses keep
  revealing the newest text. Programmatic scroll events are ignored for manual-intent tracking.
  Bottom scrolling still targets the real message end, not the spacer. If the user scrolls manually
  during generation, automatic turn anchoring/auto-follow stops and the completed answer must not
  force scroll.
- **Thinking toggle**: `RunContext.thinking_enabled` maps only to LiteLLM's unified
  `reasoning_effort` capability metadata (`high` when enabled, `none` when disabled). Models that
  do not advertise this parameter receive no SlotFlow thinking controls; response parsing remains
  entirely LiteLLM-owned.
- **Artifacts & the workspace panel**: user-visible generated files must enter the artifact folder
  through `artifact_write` (direct text/content writes) or `sandbox_artifact_copy` (one file already
  generated inside Docker). Both namespace into `artifacts/<thread_id>/`. There is no
  `workspace_write`; files written via the filesystem MCP or any other path do NOT appear in the
  panel. **Boundary**: create an artifact only for SUBSTANTIAL, STANDALONE deliverables (reports,
  full pages/apps, charts, datasets, long/multi-file code); short answers, small tables, and
  snippets stay inline. Complex planning workflows with human approval steps should write the final
  approved plan as an artifact after approval. The sidebar **工作区** button opens
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
  The reader/preview path handles source/text formats (`.ts`, `.tsx`, `.js`, `.jsx`, `.css`,
  `.sql`, `.graphql`, `.svg`, etc.), Markdown/HTML/PDF/images, `.docx`, `.xlsx`/`.xlsm`,
  `.pptx`, and `.drawio`; Office Open XML files are extracted with lightweight ZIP/XML readers
  and `.docx`/`.xlsx`/`.xlsm`/`.pptx` package previews get a larger 25 MiB package-size ceiling
  because generated reports often exceed the generic 1 MiB text-read limit due to embedded images
  while the preview reads only XML text parts. Plain oversized text previews still return HTTP 413
  instead of a server 500 when they exceed `SLOTFLOW_WORKSPACE_MAX_READ_BYTES`, while old binary
  `.xls`/`.ppt` files get media-type metadata plus a friendly unsupported-binary preview instead
  of raw JSON in the UI.
  Model-facing workspace tools are also dual sync/async `StructuredTool`s. The async path runs local
  file listing/read/write/search work in `asyncio.to_thread`; `workspace_search` processes at most
  the first 1000 sorted candidate paths per call before applying `max_results`, so a huge workspace
  cannot force unbounded file parsing in one tool call.
  The same right panel also has a **终端** view backed by `terminal/routes.py` at
  `/api/terminal/ws`: it is a user-operated host PTY for manual setup/debugging, not an agent
  tool and not part of model tool schemas. The frontend renders PTY output with `@xterm/xterm`
  rather than a plain text div, because real shells emit ANSI/OSC control sequences for prompts,
  colors, bracketed paste, and titles. Once the user opens the terminal view, the WebSocket and
  xterm instance stay connected while switching back to files or closing/reopening the right
  panel; it closes only when the page/component unmounts or the connection dies.
- **Code execution sandbox**: untrusted code, generated scripts, package experiments, and Skill
  helper scripts run through `sandbox_exec`, not on the host. Host shell/code execution tools named
  like `bash`, `shell`, `python_repl`, `run_command`, etc. are filtered out of extra/MCP tool lists
  by `harness/tools/registry.py`; if a model still calls an unregistered unsafe host execution
  tool, `harness/steps/tool_safety.py::build_unknown_tool_error_message` returns an
  `unsafe_host_execution_tool` ToolMessage that tells it to use `sandbox_exec`. Code execution must
  go through the Docker sandbox boundary. Docker images are project/host cached by Docker itself
  (so the same image is downloaded once and reused by later `docker run` calls). The implementation is
  `harness/sandbox/docker.py::LazyDockerSandbox` + `harness/tools/sandbox.py`; it is lazy, so Docker
  is not touched until the tool is actually called. The default image is `python:3.12` with network
  enabled (`bridge`), a 120s command timeout, and a 600s idle container timeout so abandoned sessions
  do not keep consuming Docker resources; set `SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS` to tune
  the close-after-inactivity window. Set `SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED=false` for offline
  execution. The container uses bind mounts instead of copy in/out: `/workspace/uploads` is read-only user uploads,
  `/workspace/artifacts` is read-write for the current thread's generated artifacts,
  `/workspace/work` is read-write scratch under `.sandbox/<thread>`, and `/workspace/skills` is
  read-only installed Skills when configured. Outputs that should appear in the UI should be written
  directly under `/workspace/artifacts` when possible; files already generated inside Docker under
  the current thread scratch directory or `/tmp` can be published with `sandbox_artifact_copy`.
  That tool copies one file inside the container into the current thread's artifact folder, enforces
  `max_write_bytes`, refuses overwrite unless `overwrite=true`, and rejects source/destination
  paths outside the current thread boundary. Env knobs:
  `SLOTFLOW_CODE_EXECUTION_ENABLED`, `SLOTFLOW_DOCKER_SANDBOX_IMAGE`,
  `SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS`, `SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED`,
  `SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS`, `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL`. When the
  model calls `sandbox_exec`, `chat/agent_adapter/streaming.py` emits a `tool.status` SSE event
  from the LangGraph `tool_calls` projection before the blocking ToolNode run, and the frontend
  shows that status on the streaming assistant bubble; this avoids a silent wait while Docker pulls
  or starts the image. `docker_engine_setup` is the only controlled host setup exception: it reads
  `/etc/os-release` to report host OS/package-manager info, checks Docker, returns a fixed
  per-family Linux install script (apt/dnf/yum/pacman/apk/zypper), or runs that fixed package-manager/systemctl/usermod
  flow only when `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL` allows host install (default true) and the
  model passes `confirm_host_install=true` after an explicit user request. It is not a generic host
  shell; do not add arbitrary command/script parameters to it. Its fixed install-script generator
  covers apt/dnf/yum/pacman/apk/zypper hosts and daemon start tries systemctl/service/rc-service/
  direct dockerd. Do not replace `tool.status` with
  `get_stream_writer()` unless LangGraph v3 exposes a custom projection channel in the current
  dependency version. Sandbox tools are dual sync/async `StructuredTool`s: direct `.invoke()` keeps
  existing tests/scripts working, while async graph execution runs Docker subprocess operations in
  `asyncio.to_thread`.
- **Todo tool availability and enforcement**: `write_todos` is registered in every mode, including
  Flash, so explicit todo requests can drive the real visual panel instead of prose simulation.
  Todo planning is no longer a static system-prompt constraint: `harness/steps/todo.py` keeps the
  tool schema strict (`{content,status}` while accepting legacy `text`) and `harness/graph.py` runs
  `todo_enforcement_update` from `post_model`. If a Pro/Ultra task looks todo-worthy and the model
  answered without creating todos, or if active todos are incomplete and the model tries to answer
  without updating them, the node appends a named control message and routes back to `pre_model`.
  The initial-todo heuristic strips leading `<slotflow-...>` injected context blocks before judging
  complexity, so upload/runtime context text cannot turn a simple user request into a todo loop.
  `chat/agent_adapter/streaming.py` emits `todo.updated` for every values snapshot containing
  todos, even when the list is unchanged; the frontend still signature-dedupes UI updates to avoid
  flicker.
- **Agent operating procedure (prompt)**: `harness/builder.py` injects
  `current_utc_date` in `<slotflow-runtime>`, `<slotflow-freshness-policy>`,
  `<slotflow-long-term-memory-status>`, and `<slotflow-operating-procedure>`. Freshness policy tells
  the model to ground time-sensitive answers on the current date and use `web_search`/`web_fetch` or
  other authoritative sources instead of training data alone; stable definitions/basic math/local
  workspace code can still be answered without web search, and material source conflicts must be
  disclosed. Memory policy tells the model to decide at task start whether injected memories are
  enough or `memory_list` is needed, then after work decide whether `memory_save`/`memory_update`/
  `memory_delete` is warranted for durable preferences/profile/project context. For non-trivial
  tasks: clarify first via
  `ask_clarification` (interactive picker, not plain-text questions), `skill_match` +
  `skill_read` before specialized work, then (gated on `subagent_enabled`) split INDEPENDENT parts
  to `task_tool` sub-agents. The sub-agent catalog is static prompt text (`<slotflow-subagents>`),
  so delegation is a single `task_tool` call with no lookup round-trip; an optional `role_query`
  loads one professional role template inside the delegated child only. Browser work is delegated
  to `agent_name='browser'` because those tools exist only in that child.
  Todo creation/update is enforced by the graph's `post_model` node instead of this
  prompt. These fire far more reliably with
  **thinking ON**; with thinking off DeepSeek tends to one-shot.
- **HITL clarification = LangGraph native `interrupt()`/resume** (rewired 2026-06-21, see
  `HARNESS_NOTES.md` §12). **As of 2026-08-14 there is exactly ONE entry point** — the model asks
  for itself:
  - **Voluntary (the model asks)**: `tools/builtins.py::ask_clarification_tool` calls
    `interrupt(build_clarification_payload(...))` and returns the resume value as its result.
  - ~~**Forced gate (pro + ultra)**~~ — the `triage_gate` node and `harness/steps/clarify_gate.py`
    were **deleted on 2026-08-14**. It spent one extra model call on every fresh user turn to judge
    "is this request clear enough", which is first-token latency on every turn plus an interruption
    on requests that were already specific; the model deciding for itself (prompted in
    `<slotflow-operating-procedure>`) is good enough. `SLOTFLOW_CLARIFY_GATE` is gone.
  - **A clarification message MUST persist the turn's reasoning** (`chat/routes.py`): on the
    `clarification.requested` path `clarification_saved=True` suppresses the normal `run.finished`
    save, so that write is the message's ONLY persistence point. It originally stored just
    `{source, clarification}` — the live UI still had `reasoningContent` in memory so it looked
    fine, but any reload rebuilt the message from the DB and the whole thinking block vanished.
    That reads as "the model re-thought after resume"; it did not (the interrupt pauses at the
    `tools` node, the `agent` node's output is already in state and is not re-run). Pinned by
    `tests/test_chat_routes.py::test_stream_run_persists_clarification_request`.
  - The user's answer arrives via `Command(resume=<answer>)` and **is** the tool result / user
    message — no "rewrite the answered tool message" step. `build_clarification_payload` (now in
    `app/harness/clarification.py`) always appends a free-text `其他（自己输入）` option LAST; the
    frontend renders any 其他/other/specify option as an input box. When the user clicks a fixed
    option, the frontend resumes with the option label only (metadata keeps the option id), avoiding
    a visible "我选择 A：..." prefix being fed back as ordinary answer text.
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
  - **Hard-won provider rules (DeepSeek thinking-mode, live-verified — do NOT regress)**: (1) any
    side-channel `ainvoke` (e.g. the proactive memory extractor) MUST pass `config={"callbacks": []}`
    or its tokens pollute the user stream; (2) NEVER force `tool_choice` (`"Thinking mode does not
    support this tool_choice"`); (3) prefer `interrupt()`/resume over any
    synthesized-response-then-continue scheme — `interrupt` pauses at tool execution with the
    model's REAL tool call (real `reasoning_content`), sidestepping the `"reasoning_content ...
    must be passed back"` trap that killed the old `wrap_model_call` path.
  - **The same `GraphBubbleUp`-propagation rule applies to the ToolNode path**: the SlotFlow
    tool-safety wrappers (`harness/graph.py::_slotflow_tool_safety_wrapper` /
    `_slotflow_async_tool_safety_wrapper`) that wrap every ToolNode call MUST re-raise
    `GraphBubbleUp` before their `except Exception` — otherwise the voluntary
    `ask_clarification` tool's `interrupt()` is swallowed into a `tool_execution_error`
    ToolMessage and HITL silently never pauses (the 2026-07-02 fix; regression test
    `test_ask_clarification_via_slotflow_tool_node_actually_interrupts` pins it). See
    `HARNESS_NOTES.md` §18.
- **Long-term memory (cross-conversation, proactive)**: memory is **global, not thread-scoped** —
  `store.search_memories` ranks across ALL threads (`thread_id` is only a relevance bonus), so a
  fact learned in one conversation is retrievable in any other. Logic lives in
  `harness/steps/long_term_memory.py`, called from graph nodes: `prepare` retrieves and `pre_model`
  injects relevant memories as **background context, not commands** (the agent must still answer the
  current question). The store remains synchronous SQLite, but graph async nodes call retrieval and
  saves via `asyncio.to_thread` (`aretrieve_memories`, `aexplicit_save_update`, background
  extraction) so local DB I/O does not block the event loop. Saving has three paths:
  (1) explicit `memory_save` tool — the model is nudged to call it proactively for durable facts;
  (2) an explicit `请记住X` synchronous fast-path in `finalize` (`explicit_save_update`); (3)
  **proactive background extraction** — `finalize` fires `memory/extractor.py`
  (`SlotFlowMemoryExtractor`) fire-and-forget on the server loop, which asks the model to pull
  durable preferences/profile/topic facts from the finished turn and saves them via
  `store.add_memory` (which dedups by `source_run_id` and `kind+content`). This replaced the old
  brittle Chinese-regex extraction. The extractor reuses the conversation model with
  `config={"callbacks": []}`; latency is hidden because it does not block the run. Gated by
  `proactive_memory_extraction_enabled` (default on; env
  `SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION=false` disables it); scripted-model graph tests set it `False`.
  Don't reintroduce "the auto-saves" framing in the prompt (it suppresses the model's own
  `memory_save`).
- **Sub-agent concurrency cap**: the `post_model` graph node (logic in
  `harness/steps/subagent_limit.py::cap_subagent_calls`) truncates excess parallel `task_tool`
  calls on a single model step down to `subagent_max_concurrent` (default 3), a graph-level guard
  that preserves non-`task_tool` calls and the message's `reasoning_content`. Active when
  `subagent_limit_enabled` + `features.subagent_enabled`; env
  `SLOTFLOW_SUBAGENT_LIMIT=false` disables the guard and
  `SLOTFLOW_SUBAGENT_MAX_CONCURRENT=<positive-int>` adjusts the cap. Each delegated child graph
  has its own `SlotFlowSubagentConfig.recursion_limit` (default 100), passed as the top-level
  LangGraph `config["recursion_limit"]` on `graph.ainvoke`;
  `SLOTFLOW_SUBAGENT_RECURSION_LIMIT=<positive-int>` overrides it. This limit applies only to the
  child `task_tool` graph, so multi-tool/reflection loops get room without changing the main graph's
  default recursion limit.
- **Sub-agent delegation (one tool, 2026-08-14)**: the parent agent sees exactly ONE delegation
  schema, `task_tool`. `subagent_list` and `subagent_role_search` were deleted: the former returned
  a value that never changes within a run, so it is now static system text
  (`harness/subagents/tools.py::build_subagent_catalog_prompt`, rendered as `<slotflow-subagents>`
  and cached with the prefix); the latter's lookup sank into `task_tool`'s `role_query` argument, so
  the parent no longer spends two tool round-trips before delegating anything — those were the hops
  the model most often abandoned. Profiles (`harness/subagents/config.py`) are the six functional
  ones plus `browser`, the only **vertical** profile: it exists to carry a heavy tool space, not a
  persona, and owns playwright's `browser_*` tools so page snapshots never enter the parent context.
  `web_search`/`web_fetch` deliberately stay bound on the parent (2 schemas, single round-trip,
  source links must be cited by the parent) — the decision rule is `schema count × interaction depth
  × intermediate-output noise`. The file-backed role library under
  `harness/subagents/agency_agents/roles/` (235 markdown prompts with the upstream MIT license and
  `divisions.json`) is never listed to the parent; `task_tool` resolves at most one template
  (`role_name` exact → `role_query` free text → `domain`+task scoring) and injects it into the child
  system prompt inside `<slotflow-agency-role>`. Role scoring matches **whole tokens** with a
  stopword list and weights identity fields (id/name/division) 3× over descriptions, and the
  `role_query` path additionally requires an identity hit (`_ROLE_QUERY_MIN_SCORE`): substring
  matching made almost any query hit something across 235 roles, and a false positive there injects
  up to 12000 chars of the wrong domain instructions. No match → no template, which beats a wrong one.
  When sub-agents are disabled (flash mode) `browser_*` falls back into the MCP proxy rather than
  vanishing.
- **Skills are two-step: discover, then read (2026-08-14)**: `build_skills_prompt` puts ONLY the
  catalog (`name: description`, top-level skills) in the system prefix — never a Skill body. The
  model decides what is relevant and calls `skill_read(name)`, which returns the SKILL.md body as a
  **tool result** (`harness/skills/reader.py`, host-side). Before this, a Skill body was reachable
  only via `sandbox_exec cat /skills/...`, so Skills silently died whenever Docker was unavailable —
  even though SlotFlow has a full Docker-degraded path. `skill_read(name, path=...)` reads the
  Skill's bundled files (the body's file list comes back with it, path traversal is rejected), and
  `offset` resumes a body truncated at `MAX_SKILL_READ_CHARS`. Skill bodies are deliberately NOT
  subject to tool-output offload (`skill_read` returns a `Command`, which
  `steps/tool_output_offload.py` passes through): moving instructions to a file the model must read
  back defeats the purpose. Every successful read appends to the `used_skills` state channel — the
  **compaction ledger**: `make_summarization_node` injects those names into the summary prompt AND
  appends a deterministic `<slotflow-skills-ledger>` block to the compacted view, telling the model
  to re-`skill_read` or use `context_archive_search/read` rather than reconstruct a Skill's procedure
  from memory. Discovery is unchanged (`skill_match` → `find-skills` → `search_skill_repos`, open
  SKILL.md standard shared with Claude Code / OpenAI Codex `.agents/skills` / GitHub Copilot;
  `match_installed_skills` memoized with a short TTL, invalidated by `skill_install`), but every
  match now ends in "call `skill_read`", never "act on the description". The prepare-node skills
  **preflight was deleted on 2026-08-14** (`harness/steps/skills_preflight.py` is gone): the catalog
  plus the `skill_read` instruction already tell the model everything the preflight was hinting at,
  while the preflight itself cost a disk scan before the first token and dumped the user's raw query
  back into the prompt every turn — the last volatile block breaking prefix caching.
- **Skill management UI/API**: multi-skill installs group dependency skills under a parent via
  `SkillRecord.parent`. Deleting a parent skill from `/api/skills/{name}` must delete the whole
  skill tree: the parent directory, any nested `dependencies/*` child directories, legacy
  same-package child directories that are still top-level, their config entries, runtime enabled
  names, and the scan cache. Otherwise `load_enabled_skills` will rediscover orphaned child
  directories and the Skills panel will show sub-skills after the parent was removed.
- **Node + edge graph (2026-06-30 refactor)**: the harness now uses a LangGraph native
  `StateGraph` (`harness/graph.py`) instead of LangChain `create_agent` + `AgentMiddleware`.
  Each former middleware is a stateless function in `harness/steps/*` called by a named node;
  order is fixed by edges. Provider quirks are normalized by LiteLLM before the projection
  layer. `AgentMiddleware` classes and the middleware registry were deleted; only
  `SlotFlowMiddlewareConfig` (behavior switches consumed by nodes) remains.
- **Interaction UI invariants**: `write_todos` updates surface in one place only: the collapsible
  `ComposerTodoPanel` above the chat composer. Do not add a second inline todo panel inside the
  message list; duplicate panels make the single source of truth look inconsistent. The composer
  panel auto-expands only when a genuinely new todo content list appears (`todoListKey` changes);
  status-only updates and a new streaming answer must not override the user's manual collapsed
  state, but the panel auto-collapses when every todo is completed. If the model creates a different
  todo list, clear/replace the old list first so the new list can expand as a new panel state.
  `state.snapshot`
  todos are also consumed as a frontend fallback in case an intermediate `todo.updated` event is
  missed. Backend streaming emits `todo.updated` for every values snapshot with todos; frontend
  signature dedupe prevents repeated identical events from flickering the panel. Todo items normalize
  to the public `{content, status}` shape; accept legacy/model-emitted
  `{text, status}` only at the tool/projection/frontend parsing boundaries so the panel never shows
  status icons without labels. The center conversation section uses one uniform `slotflow-surface`
  background from message area through composer; the composer footer and todo/input stack must not
  reintroduce translucent bands or hard horizontal border/ring dividers. The Skills/MCP/Memory
  directory uses native `overflow-y-auto` containers (including sub-skill lists) because Base UI
  `ScrollArea` did not reliably wheel-scroll inside the centered dialog. The chat composer should
  not show non-functional placeholder affordances: the lower-left `+` is a direct attachment button,
  there is no voice-input button, and the empty-state prompt chips are removed until they have real
  behavior. Composer paste routes by clipboard item kind: text pastes normally into the textarea,
  while any `kind === "file"` item (images or other files) is intercepted (`extractClipboardFiles`)
  and sent through the same upload path as the `+` button — text stays text, everything else uploads.
  The Skills directory intentionally has no hardcoded recommended-Skills page or cards;
  it shows installed Skills only, with install/upload actions in the header.

## Roadmap (next steps)

The 2026-06 DeerFlow-alignment pass (clarify slim-down, proactive memory, sub-agent cap, skill
cache — see `HARNESS_NOTES.md` §11) is done and live-validated. The 2026-06-30 node+edge refactor
(`create_agent`+middleware → LangGraph `StateGraph`) is done (see `HARNESS_NOTES.md` §13). Known
follow-ups, roughly ordered:

1. **Memory rewrite to mem0** — replace the hand-written `harness/memory/store.py` layer with
   mem0 OSS local-first (vector_store=local sqlite-vec/qdrant, embedder=OpenAI-compatible embedding
   API reusing existing relay, llm=conversation model). Keep `LongTermMemory` step shape; swap the
   store calls to `add/search/get_all`. This is a standalone post-refactor phase (not mixed into the
   graph migration). See `docs/refactor-plan.md` §10.
2. **Main-graph parallel sub-agents (`Send`+`merge`)** — the current `post_model` cap guards
   `task_tool` delegation; upgrade to real main-graph parallel branches (route → `Send(subagent)×N`
   → `merge` → `pre_model`) for independent tasks (better token economy via context isolation).
3. **Memory dedup / near-duplicates** — the background extractor and the model's own `memory_save`
   can both fire in one turn and store near-duplicate facts (observed: a Chinese preference saved
   twice with different wording). `store.add_memory` only dedups on exact `kind+content`. Make
   background extraction skip when `memory_save` already fired this turn (current `run_id` match in
   `memory_save_tool_used_for_run` is too strict), and/or add a light semantic merge.
4. **Memory normalization bloat** — `store.py::canonicalize_memory_content` still carries
   hand-written Chinese regex with hardcoded values ("控制工程"/"研究生"). LLM-extracted content is
   already clean; this can be simplified once the extractor is the primary path.
5. **Sub-agent live test** — the concurrency cap is unit-tested only; add an end-to-end live check
   that real parallel `task_tool` delegation is capped and synthesized correctly.
6. ~~**MCP context bloat**~~ — done (2026-08-14). Deferred/dynamic schema injection was
   evaluated and rejected: changing the tool array at all invalidates the provider prefix cache,
   which costs more than the schemas save. Shipped instead: fixed `mcp_docs`/`mcp_call` proxy pair
   + the `browser` vertical sub-agent. See `HARNESS_NOTES.md` §59.
7. **Ship it** — the refactor branch is unpushed; open a PR (required check: `Verify`).

- **Python runtime**: backend model integration uses `langchain-litellm==0.7.0` / LiteLLM and
  therefore supports Python 3.12 and 3.13 (`requires-python = ">=3.12,<3.14"`). Do not bypass
  LiteLLM's Python `<3.14` metadata; use uv-managed Python 3.13 when the host default is 3.14.

## Build / verify

```
make verify          # backend pytest + frontend tsc --noEmit + next build
cd backend  && uv run pytest -q -k "not live"   # backend only (skip live DeepSeek)
cd backend  && uv run ruff check app tests
cd frontend && pnpm typecheck && pnpm build
```
The default branch is protected: land changes via PR (the required check is named `Verify`).

## Debugging with LangSmith (链路审查)

SlotFlow 的每一次运行都是一张 LangGraph `StateGraph`(节点见上文拓扑),LangSmith 可以把
整张图的每个节点、每次模型调用、每个工具调用、`interrupt()`/resume 都作为可点开的 trace
记录下来,是排查"某条链路到底发生了什么"最直接的手段(远胜于在节点里加 print)。

**开启方式(环境变量,写进 `backend/.env` 或导出到 shell,LangChain/LangGraph 会自动上报;
仓库不落库这些密钥):**

```
LANGSMITH_TRACING=true          # 或旧名 LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=slotflow-dev  # 按环境/分支区分,便于筛选
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com  # 自建/区域端点时才需要
```

**怎么用它审查各链路:**

- 一次 chat 请求 = 一条 root trace;按 `thread_id` / run 时间定位后展开,能逐节点看到
  `prepare → pre_model → SlotFlowSummarizationMiddleware → agent → post_model
  → route → tools → …` 的输入/输出 state、耗时与报错;并发/工具报错(例如本次
  `INVALID_CONCURRENT_GRAPH_UPDATE`)会精确落在触发它的节点/工具 span 上。
- `agent` 节点 span 上能看到最终 `bind_tools` 的工具集、system_prompt、`llm_input_messages`
  投影,用来核对渐进式工具空间披露(`promoted_tool_names`)与 `context_epoch` 是否符合预期。
- 每个 `*_tools` 加载器 / ToolNode 调用都是独立 span,可确认工具是否被 `tool_not_activated`
  失败关闭、以及并发加载器是否被 union reducer 正确合并。
- 排查用量/缓存请以 `run.usage`(`RunUsageCollector`,持久化在 SQLite `run_metrics`)为准;
  LangSmith 的 token/时延是交叉验证。

**重要:trace 隔离约定不要破坏。** proactive memory extractor 的
模型调用刻意使用 `config={"callbacks": []}`,以免它们的 token 污染用户流;这同样会让它们**不**
出现在主 run 的 LangSmith trace 里——这是有意的,排查这两条子链路时单独跑或临时放开 callbacks,
不要为了"看得见"就把它们并回主流(参见 `HARNESS_NOTES.md` 的 provider 规则)。

## Commit style

Chinese, conventional-ish prefixes (重构 / 功能 / 修复 / 测试 / 文档 / ci), one logical
module per commit, footer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## 产物预览的 CORS 取舍(2026-08-15,已生效,可回退)

`GET /api/workspace/artifacts/raw*` 会带 `Access-Control-Allow-Origin: *`
(`app/workspace/routes.py` 的 `RAW_ARTIFACT_HEADERS`)。**这不是图省事,是预览的硬条件**:

产物面板的 iframe 带 `sandbox` 且**刻意不给** `allow-same-origin` —— 产物是模型生成的内容,
给了同源权限它就能读 localStorage、冒充用户调 API。代价是这个 iframe 成了**不透明源**,
发出的请求 `Origin: null`;而 `<script type="module">` 无论如何都以 CORS 模式抓取。
所以少了这个响应头,**任何带 JS 的 HTML 产物都只能白屏**(一个 Vite 构建产物就是这么发现的)。

换来的暴露面:别的网页上的脚本,只要**猜中完整产物路径**、且本机后端正在跑,就能读到该文件。
判断依据是 SlotFlow 是本地开发工具、产物是用户自己的文件,这个交换划算。

**要收紧**:把 `RAW_ARTIFACT_HEADERS` 的值从 `"*"` 改成 `"null"`,只放行不透明源,
浏览器一样接受,普通跨站脚本就读不到了。改完跑
`tests/test_workspace.py::test_raw_artifact_path_style_url_lets_relative_assets_resolve`
(该用例断言了这个头,需同步改断言)。

**不要**通过给 iframe 加 `allow-same-origin` 来"绕过 CORS" —— 那是把模型生成的内容
提升到同源,比这里的暴露面严重得多。

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
