# AGENTS.md

Guidance for AI agents (and humans) working in the SlotFlow repository.

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
switches vs LangGraph runtime keys), and the **projection layer** — the single place that
absorbs every provider/version quirk into clean `AgentEvent`s.

## Layout

```
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
  emitted, and the projection layer absorbs **all three** shapes: DeepSeek
  `delta.reasoning_content` → bridged to a `{"type":"reasoning","reasoning": ...}` block;
  OpenAI official reasoning models (gpt-5 / o-series) auto-use the **Responses API**
  (`reasoning_effort` only — langchain-openai selects it), emitting reasoning as a
  `{"type":"reasoning","summary":[{"type":"summary_text","text": ...}]}` block (NOT a flat
  string) under the default `responses/v1` output_version; Anthropic `thinking`. DeepSeek
  **and** the `custom` relay use the reasoning-bridging `ChatDeepSeek` subclass
  (`runtime/models.py`) so `delta.reasoning_content` reaches the v3 channel; the **official
  OpenAI** provider uses plain `ChatOpenAI` + `reasoning_effort` (no bridge, no manual
  `use_responses_api`). `custom` sends NO provider-specific thinking flags (unknown relay
  protocol — toggle control is best-effort). The single normalization entry is
  `agent_adapter/projections.py::projection_item_to_agent_event` /
  `extract_message_delta_parts`, with `extract_reasoning_from_content_block` flattening
  the OpenAI Responses `summary[]` list (the 2026-07-02 fix — previously that shape was
  silently dropped, so gpt-5 thinking was invisible). **Before changing this layer, keep
  `tests/test_provider_reasoning_contract.py` green** — it pins that every provider's chunk
  (including OpenAI Responses summary blocks) normalizes to the right single channel with no
  crossing. See `HARNESS_NOTES.md` §17.
- **Streaming merge contract**: `message.delta` is the live user-visible stream; final
  `state.snapshot` is a reconciliation source, not permission to erase already-streamed text.
  Both `chat/routes.py::select_assistant_content` and
  `hooks/use-chat-stream-helpers.ts::mergeAssistantContent` keep the longer/prefix-compatible
  content so a shorter snapshot cannot make the answer visibly shrink at run end. Reasoning uses
  the same principle via `select_assistant_reasoning_content` / `mergeReasoningContent`. Snapshot
  assistant messages with tool calls are intermediate ReAct steps, not final user-visible answers;
  normalization marks them with `has_tool_calls`, and backend/frontend content selectors skip them.
- **Chat scroll behavior**: `frontend/src/components/chat/message-list.tsx` scrolls to the latest
  assistant message when output first appears, then auto-follows streaming output only while the
  user stays near the bottom. If the user scrolls upward during generation, auto-follow stops and
  the completed answer must not force-scroll back to the bottom.
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
  read-only installed Skills when configured. Outputs that should appear in the UI must be written
  under `/workspace/artifacts`; they are already on the host because of the bind mount. Env knobs:
  `SLOTFLOW_CODE_EXECUTION_ENABLED`, `SLOTFLOW_DOCKER_SANDBOX_IMAGE`,
  `SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS`, `SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED`. When the
  model calls `sandbox_exec`, `chat/agent_adapter/streaming.py` emits a `tool.status` SSE event
  from the LangGraph `tool_calls` projection before the blocking ToolNode run, and the frontend
  shows that status on the streaming assistant bubble; this avoids a silent wait while Docker pulls
  or starts the image. `docker_engine_setup` is the only controlled host setup exception: it reads
  `/etc/os-release` to report host OS/package-manager info, checks Docker, returns a fixed
  per-family Linux install script (apt/dnf/pacman), or runs that fixed package-manager/systemctl/usermod
  flow only when `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL` allows host install (default true) and the
  model passes `confirm_host_install=true` after an explicit user request. It is not a generic host
  shell; do not add arbitrary command/script parameters to it. Do not replace `tool.status` with
  `get_stream_writer()` unless LangGraph v3 exposes a custom projection channel in the current
  dependency version.
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
  sub-agents. Todo creation/update is enforced by the graph's `post_model` node instead of this
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
    Gated by `clarify_gate_enabled` (default on) + `run_context.mode in {pro, ultra}`. Scripted-model
    graph tests set `clarify_gate_enabled=False`. Live-validated against `deepseek-v4-pro`:
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
  current question). Saving has three paths:
  (1) explicit `memory_save` tool — the model is nudged to call it proactively for durable facts;
  (2) an explicit `请记住X` synchronous fast-path in `finalize` (`explicit_save_update`); (3)
  **proactive background extraction** — `finalize` fires `memory/extractor.py`
  (`SlotFlowMemoryExtractor`) fire-and-forget on the server loop, which asks the model to pull
  durable preferences/profile/topic facts from the finished turn and saves them via
  `store.add_memory` (which dedups by `source_run_id` and `kind+content`). This replaced the old
  brittle Chinese-regex extraction. The extractor reuses the conversation model with
  `config={"callbacks": []}`; latency is hidden because it does not block the run. Gated by
  `proactive_memory_extraction_enabled` (default on); scripted-model graph tests set it `False`.
  Don't reintroduce "the auto-saves" framing in the prompt (it suppresses the model's own
  `memory_save`).
- **Sub-agent concurrency cap**: the `post_model` graph node (logic in
  `harness/steps/subagent_limit.py::cap_subagent_calls`) truncates excess parallel `task_tool`
  calls on a single model step down to `subagent_max_concurrent` (default 3), a graph-level guard
  that preserves non-`task_tool` calls and the message's `reasoning_content`. Active when
  `subagent_limit_enabled` + `features.subagent_enabled`.
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
  order is fixed by edges. Provider quirks are still handled in the model subclass + projection
  layer. `AgentMiddleware` classes and the middleware registry were deleted; only
  `SlotFlowMiddlewareConfig` (behavior switches consumed by nodes) remains.
- **Interaction UI invariants**: `write_todos` updates surface in one place only: the collapsible
  `ComposerTodoPanel` above the chat composer. Do not add a second inline todo panel inside the
  message list; duplicate panels make the single source of truth look inconsistent. The composer
  panel expands on every distinct todo-list update and stays visible after the run finishes;
  collapsing it at completion makes users think no visual todo panel was used. `state.snapshot`
  todos are also consumed as a frontend fallback in case an intermediate `todo.updated` event is
  missed. Backend streaming emits `todo.updated` for every values snapshot with todos; frontend
  signature dedupe prevents repeated identical events from flickering the panel. Todo items normalize
  to the public `{content, status}` shape; accept legacy/model-emitted
  `{text, status}` only at the tool/projection/frontend parsing boundaries so the panel never shows
  status icons without labels. The Skills/MCP/Memory
  directory uses native `overflow-y-auto` containers (including sub-skill lists) because Base UI
  `ScrollArea` did not reliably wheel-scroll inside the centered dialog. The chat composer should
  not show non-functional placeholder affordances: the lower-left `+` is a direct attachment button,
  there is no voice-input button, and the empty-state prompt chips are removed until they have real
  behavior. The Skills directory includes a hardcoded "推荐 Skills" section in
  `directory-modal.tsx`; recommended cards call the existing `/api/skills/install` flow directly
  with `{package_url, skill_name}` instead of sending hidden chat prompts.

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
