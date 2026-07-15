# AGENTS.md

Guidance for AI agents (and humans) working in the SlotFlow repository.

> **✅ 2026-07-04 大扫除完成,等待人工验证后提 PR**(分支 `cleanup/audit-20260703`)。
> 改动与问题总览:[`docs/cleanup-2026-07-03-report.md`](docs/cleanup-2026-07-03-report.md);
> API 调用链路:[`docs/api-call-chains.md`](docs/api-call-chains.md);
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
START → prepare → triage_gate → pre_model → SlotFlowSummarizationMiddleware → agent → post_model → route
                                                                                                    ├─ tools → pre_model   (ReAct loop; ask_clarification interrupts here)
                                                                                                    ├─ pre_model           (todo enforcement retry)
                                                                                                    └─ finalize → END
```

- `prepare` (once/turn, all `before_agent`): runtime summary, uploads, skills preflight,
  long-term-memory retrieval, artifact baseline.
- `triage_gate` (first step only, pro/ultra): triage → `interrupt()` clarification; resume
  injects the answer verbatim as a `HumanMessage`.
- `pre_model` (every step): dynamic todo-state reminder, dangling-tool-call repair, skills-preflight
  system-context injection, long-term-memory system-prompt injection.
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
  `asyncio.to_thread`, so `/api/chat/models` does not block FastAPI's event loop. Updating the
  pinned LiteLLM packages updates native provider/model metadata; do not add hand-maintained
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
  Completions-routing tests green.
- **Streaming merge contract**: `message.delta` is the live user-visible stream; final
  `state.snapshot` is a reconciliation source, not permission to erase already-streamed text.
  Both `chat/routes.py::select_assistant_content` and
  `hooks/use-chat-stream-helpers.ts::mergeAssistantContent` keep the longer/prefix-compatible
  content so a shorter snapshot cannot make the answer visibly shrink at run end. Reasoning uses
  the same principle via `select_assistant_reasoning_content` / `mergeReasoningContent`. Snapshot
  assistant messages with tool calls are intermediate ReAct steps, not final user-visible answers;
  normalization marks them with `has_tool_calls`, and backend/frontend content selectors skip them.
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
  Stateless MCP servers keep the original one-session-per-call adapter behavior. The preset can be
  toggled but cannot be deleted or shadowed by a user HTTP server. `bootstrap.sh` installs the locked
  package, runs official `playwright install-deps chromium` on apt hosts, and downloads Chromium;
  non-apt hosts receive a precise shared-library warning. No separate maintenance command exists.
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
  `ask_clarification` (interactive picker, not plain-text questions), `skill_match` before
  specialized work, then (gated on `subagent_enabled`) split INDEPENDENT parts to `task_tool`
  sub-agents. When role fit matters, the prompt tells the model to call `subagent_list` first,
  choose a Layer-1 functional `agent_name`, and usually pass only a Layer-2 `domain` into
  `task_tool`. If a precise professional role matters, it calls `subagent_role_search(query,
  domain)` for a short metadata-only Layer-3 shortlist, then passes one returned `role_name`/id into
  `task_tool`; the full role prompt is loaded only inside that delegated child.
  Todo creation/update is enforced by the graph's `post_model` node instead of this
  prompt. These fire far more reliably with
  **thinking ON**; with thinking off DeepSeek tends to one-shot.
- **HITL clarification = LangGraph native `interrupt()`/resume** (rewired 2026-06-21, see
  `HARNESS_NOTES.md` §12). There are two clarification entry points, both pausing the graph the
  same way — no separate `SlotFlowClarificationMiddleware` anymore (it was deleted):
  - **Voluntary (the model asks)**: `tools/builtins.py::ask_clarification_tool` calls
    `interrupt(build_clarification_payload(...))` and returns the resume value as its result.
  - **Forced gate (pro + ultra)**: the `triage_gate` graph node (logic in
    `harness/steps/clarify_gate.py`) runs one cheap structured **triage** on the **first model
    step** of a fresh user turn; if not actionable, it calls `interrupt(payload)` and,
    on resume, injects the user's answer **verbatim** as a `HumanMessage` so the model proceeds.
    Prompts alone't stop a model from one-shot-guessing, so this moves the decision into the graph.
    To protect first-token latency, `should_skip_triage_model_call` bypasses this extra triage
    LLM call for long, already-detailed requests, explicit "don't ask / just do it" wording, and
    ordinary short messages that are not clearly underspecified creation/output tasks; short
    underspecified prompts such as "做个表格" still go through the gate.
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
  - **Hard-won provider rules (DeepSeek thinking-mode, live-verified — do NOT regress)**: (1) the
    triage `ainvoke` MUST pass `config={"callbacks": []}` or its tokens pollute the user stream;
    (2) NEVER force `tool_choice` (`"Thinking mode does not support this tool_choice"`); (3) prefer
    `interrupt()`/resume over any synthesized-response-then-continue scheme — `interrupt` pauses at
    tool execution with the model's REAL tool call (real `reasoning_content`), sidestepping the
    `"reasoning_content ... must be passed back"` trap that killed the old `wrap_model_call` path.
  - The `triage_gate` node MUST let `interrupt()`'s `GraphBubbleUp`/`GraphInterrupt` (an
    `Exception` subclass) propagate — never swallow it in a fail-open `except Exception`, or the
    pause is defeated. On resume LangGraph replays the node, so triage runs once more (cheap,
    benign). Only the first step is constrained; never gates twice in a thread (anti-loop).
    Gated by `clarify_gate_enabled` (default on; env `SLOTFLOW_CLARIFY_GATE=false`) +
    `run_context.mode in {pro, ultra}`. Scripted-model graph tests set `clarify_gate_enabled=False`.
    Live-validated against `deepseek-v4-pro`:
    underspecified requests clarify, the answer resumes the run, and the clarification does not re-pop.
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
- **Sub-agent role routing**: SlotFlow delegation is three-layered. Layer 1 remains the six
  built-in functional profiles from `harness/subagents/config.py` (`researcher`, `analyst`,
  `planner`, `coder`, `reviewer`, `writer`). Layer 2 is the compact domain catalog in
  `harness/subagents/role_catalog.py` (`engineering`, `design`, `finance`, `market`, `sales`,
  `product`, `research`, `specialized`). Layer 3 is the file-backed local agency-agents role
  library under `harness/subagents/agency_agents/roles/` (220 markdown role prompts copied with
  the upstream MIT license and `divisions.json`). `subagent_list` exposes only functional
  profiles, domain summaries, counts, and sample role metadata; it does NOT dump all role prompts
  into the parent model context. `subagent_role_search(query, domain, max_results)` is the narrow
  lookup path when sample roles are not enough; it returns a bounded metadata-only shortlist and
  falls back to a stable domain shortlist when a query has no keyword hits, so non-English tasks do
  not produce an empty role-selection dead end. `task_tool(agent_name, task, ..., domain, role_name)`
  resolves at most one concrete role template and injects it into the child subagent's system prompt
  inside `<slotflow-agency-role>`, preserving the parent model's context budget.
- **Skill discovery (cross-tool, cached)**: `skill_match` → `find-skills` → `search_skill_repos`.
  Skills use the open **SKILL.md** standard shared by Claude Code, OpenAI Codex (`.agents/skills`)
  and GitHub Copilot, so a Skill written for any of them installs into SlotFlow unchanged — there
  is no Codex-specific tool; `search_skill_repos` returns GitHub matches plus authoritative
  `ecosystem_sources` (Anthropic / Codex / skills.sh) to browse. Local installed-skill matching
  (`match_installed_skills` in `tools/customization.py`) is memoized with a short TTL so the skills
  preflight and a later `skill_match` in the same turn don't re-scan disk; `skill_install` calls
  `invalidate_skill_match_cache()`. The prepare-node skills preflight stores its result only in
  `state.slotflow.skills_preflight`; `pre_model` formats that state into internal system context.
  It must not prepend `installed_matches` / search metadata to `HumanMessage.content`, because user
  messages are later consumed by explicit/background memory extraction.
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
  behavior. The Skills directory intentionally has no hardcoded recommended-Skills page or cards;
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
6. **MCP context bloat** — consider DeerFlow's `tool_search` deferred-schema pattern (inject tool
   names, load full schemas on demand) when many MCP tools are configured.
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

## Commit style

Chinese, conventional-ish prefixes (重构 / 功能 / 修复 / 测试 / 文档 / ci), one logical
module per commit, footer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
