# SlotFlow 后端重构方案：从 `create_agent` + middleware 迁移到 LangGraph node + edge graph

> 状态：方案（未改代码）。分支：`refactor/langgraph-node-edge-graph`（需本机创建，见末尾）。
> 配合阅读：`AGENTS.md`（当前架构地图）、`HARNESS_NOTES.md`（harness 工程日志，尤其 §12 HITL 链路）。
> 所有论断均对照当前代码核实（文件/行见正文），非纸面推断。

---

## 0. 一句话目标

把后端从 **LangChain `create_agent` 单 ReAct 循环 + 11 个 `AgentMiddleware`** 改为
**LangGraph 原生 `StateGraph`（显式 node + edge）**，让链路按我们规定的路径严格运行、可可视化，
HITL（`interrupt`/`resume`）和多 agent 协作有更清晰的扩展位，同时**保住**前端 SSE 事件契约、
reasoning/content 通道分离、274 个离线测试、以及 HARNESS_NOTES 里所有 live-verified 不变量。

## 1. 现状（核实过的边界）

- 组装入口：`backend/app/harness/builder.py::build_slotflow_harness_graph` → `_create_agent_graph`
  调 `create_agent(model, tools, middleware, system_prompt, state_schema=SlotFlowAgentState,
  context_schema=RunContext, checkpointer)`（`builder.py:210-218`）。
- 中间件链由 `middleware/registry.py::build_harness_middleware` 的 append 顺序隐式决定（见 §1.2）。
- state schema：`harness/state.py::SlotFlowAgentState(AgentState)`，字段 `messages`（来自 `AgentState`）
  + `slotflow: NotRequired[dict|None]` + `todos: NotRequired[list[dict]]`。
- context schema：`chat/models.py::RunContext`（thread_id/run_id/model_name/model_provider/mode/
  agent_name/files/uploaded_files/thinking_enabled/is_plan_mode/subagent_enabled）。
- HITL：两条路径都靠 LangGraph 原生 `interrupt()`/`Command(resume=...)`：
  - 自愿工具：`tools/builtins.py::ask_clarification_tool` 体内 `interrupt(payload)`。
  - 强制门：`middleware/clarify_gate_middleware.py::_clarify_via_interrupt` 在 `abefore_model` 里 `interrupt(payload)`，
    resume 后注入 `HumanMessage`（原文话）。
- resume 检测：`chat/agent_adapter/streaming.py::_pending_interrupt` 读 `graph.aget_state(config).interrupts`，
  有 pending interrupt ⇒ 这条用户消息就是答案 ⇒ `Command(resume=request.message)`；否则开新回合。
- 澄清事件只来自 pending interrupt（`clarification_event_from_interrupt`），不扫历史——这是 re-popup 根因修复，必须保留。
- 投影层：`chat/agent_adapter/projections.py` 把 v3 projections（messages/values/tool_calls）归一成 `AgentEvent`。
  `is_summarization_node_name` 靠节点名含 `"SummarizationMiddleware"` 识别压缩步骤。
- 子代理：`harness/subagents/tools.py::SubagentTaskRunner.arun` 内部再起一个 `create_agent` 子图。
- 可用 API（已核对，`langgraph==1.2.2` / `langchain==1.3.2`）：
  `StateGraph/START/END/Command/interrupt/ToolNode`、`create_agent(...)`、`AgentMiddleware` 的 10 个 hook
  （before/after_agent/model、wrap_model_call、wrap_tool_call 及 a 版本）。

### 1.1 当前中间件清单（`middleware/registry.py` 顺序）

| 中间件 | hook | 职责 |
|---|---|---|
| `DanglingToolCallMiddleware` | wrap_model_call | 修悬空 tool call（补 error ToolMessage） |
| `ToolSafetyMiddleware` | wrap_tool_call | 工具异常 → error ToolMessage |
| `SummarizationMiddleware` | wrap_model_call | 超阈值压上下文 |
| `LongTermMemoryMiddleware` | before_agent / wrap_model_call / after_agent / aafter_agent | 检索注入记忆、system 段、显式「请记住」、后台 LLM 抽取 |
| `SkillsPreflightMiddleware` | before_agent | 专业任务注入已装 Skill 候选 |
| `UploadsMiddleware` | before_agent | 把上传文件路径写进最新用户消息（含 image blocks） |
| `ClarifyGateMiddleware` | abefore_model（首步） | pro/ultra 首步 triage，不可做 → interrupt 澄清 |
| `TodoMiddleware` | before_model | todo reminder 条件注入 |
| `SubagentLimitMiddleware` | after_model | 截断超额 `task_tool` 到 3 |
| `ArtifactDiscoveryMiddleware` | before_agent / after_agent | 产物基线快照 + 新增项收集 |
| `RuntimeSummaryMiddleware` | before_agent | 写 runtime 摘要进 `slotflow` |

---

## 2. 目标 graph（节点 + 边）

```
START
  → prepare
  → triage_gate          (仅首步；pro/ultra 强制澄清门)
  → pre_model            ←─────────┐
  → agent (model call)             │  ReAct 循环
  → post_model           ──────────┤
  → route                          │
       ├─ has tool_calls ─→ tools ─┘  (ask_clarification 在 tools 内 interrupt)
       ├─ splittable parallel ─→ Send(subagent)×N ─→ merge ─→ pre_model
       └─ no tool_calls      ─→ finalize ─→ END
```

### 2.1 各节点职责（对应现在哪个中间件见 §3）

- **prepare**（每回合一次，等价所有 `before_agent`）：runtime summary、uploads 注入、skills preflight、
  artifact 基线快照、长期记忆检索（拼成 system 段，存进 state）。产出增强后的 messages + `slotflow`。
- **triage_gate**（仅首步，等价 `ClarifyGate.abefore_model`）：fresh user turn 跑廉价 triage；
  `actionable=false` → `interrupt(payload)`；resume 后把答案原样 `HumanMessage` 追加。`actionable=true`
  或已澄清过 → 直通。**线性节点，不在 ReAct 循环里**，避免首步被循环重放。
- **pre_model**（每步，等价 `before_model` + `wrap_model_call` 请求变换）：todo reminder 条件注入、
  dangling tool call 修复、summarization 压上下文、（记忆 system 段已在 prepare 算好，这里拼装）。
- **agent**：纯模型调用，reasoning/content 从这里流式出来。
- **post_model**（每步，等价 `after_model`）：subagent 并发上限截断 `task_tool`。
- **route**：条件边。`has_tool_calls(messages[-1])` → `tools`；否则 → `finalize`。
- **tools**（`ToolNode` + `wrap_tool_call` 等价物）：执行工具；`ask_clarification` 内部 `interrupt()` 即 HITL 自愿路径；
  tool safety 把异常包成 error `ToolMessage`。
- **finalize**（每回合一次，等价所有 `after_agent`）：artifact 新增项收集、长期记忆后台 fire-and-forget 抽取。

### 2.2 为什么是这套拓扑

- `prepare` / `finalize` 提到循环外，避免每步重跑「每回合一次」的工作（现在 `before_agent` 只在回合开头触发，
  节点化后语义更明确）。
- `triage_gate` 单独成节点，首步强制澄清逻辑可视化，resume 重放面缩到这一个节点。
- `route` 是 ReAct 循环的枢纽，循环体只有 `pre_model→agent→post_model→route→tools`，干净。
- HITL 两条路径都落在显式位置：门在 `triage_gate`，工具在 `tools`。

---

## 3. middleware 怎么处理（核心问题一）

**结论：不是「保留 vs 删除」二选一，而是按 hook 语义归并进节点。** `AgentMiddleware` 抽象在新 graph 里
不再使用；每个中间件改写成「节点函数」或「节点内调用的纯函数」，顺序由 graph 的边显式保证。

| 现 hook | 现中间件 | 新归属 | 形态 |
|---|---|---|---|
| before_agent | RuntimeSummary / Uploads / SkillsPreflight / ArtifactDiscovery(基线) / LongTermMemory(检索) | `prepare` 节点内顺序调 5 个纯函数 | 节点内逻辑 |
| before_model(首步) | ClarifyGate | `triage_gate` 节点（线性，不在循环里） | 节点 |
| before_model(每步) | Todo(reminder) | `pre_model` 节点内 | 节点内逻辑 |
| wrap_model_call | DanglingToolCall / Summarization / LongTermMemory(system 注入) | `pre_model`→`agent` 的请求变换 | 节点内逻辑 |
| after_model | SubagentLimit | `post_model` 节点 | 节点 |
| wrap_tool_call | ToolSafety | `tools` 节点内包一层 | 节点内逻辑 |
| after_agent | ArtifactDiscovery(新增) / LongTermMemory(后台抽取) | `finalize` 节点 | 节点内逻辑 |

落地建议：保留 `harness/steps/` 目录把每个纯函数集中（`prepare_steps.py` / `finalize_steps.py` /
`pre_model_steps.py` / `post_model_steps.py`），可单测，和现在中间件可单测的体验一致。中间件类本体删除。

### 3.1 summarization 节点识别（投影层必须跟着改的点）

现在 `projections.py::is_summarization_node_name` 靠节点名含 `"SummarizationMiddleware"` 识别压缩步骤，
避免把压缩用的内部消息当用户流式正文。summarization 搬进 `pre_model` 后有两种处理：

- **方案 A（推荐）**：summarization 在 `pre_model` 内执行时，产出的 `RemoveMessage`/摘要消息打一个稳定标签
  （如 `metadata["lc_source"]="summarization"`），投影层改判定这个标签（`has_lc_source_summarization` 已支持 dict 标签）。
- 方案 B：给 summarization 单独一个 `summarize` 子节点串在 `pre_model` 之后，保留节点名识别。更直观但多一个节点。

任选其一，**必须保 `test_provider_reasoning_contract.py` 绿**（它锁的是 reasoning/content 通道不串）。

---

## 4. 新的 agent 创建方式（核心问题二：`create_agent` 参数怎么处理）

旧路径（`builder.py:210-218`）：
```python
return create_agent(
    model=model, tools=tools, middleware=middleware,
    system_prompt=system_prompt, state_schema=SlotFlowAgentState,
    context_schema=RunContext, checkpointer=checkpointer,
)
```

新路径：`harness/graph.py` 手写 `StateGraph`，参数映射如下：

| `create_agent` 参数 | 新 graph 怎么处理 |
|---|---|
| `model` | 传给 `agent` 节点内的 model 调用（`model.astream` / `model.ainvoke`），并 `bind_tools(tools)` |
| `tools` | `ToolNode(tools, name="tools")` 作为 `tools` 节点；`agent` 节点用 `model.bind_tools(tools)` |
| `middleware` | **整体弃用**。每个中间件 → §3 表里的节点/纯函数。不再传 `middleware=` |
| `system_prompt` | 在 `prepare` 计算好 system 段（含记忆、skills、MCP 状态、operating-procedure），存进 state；`agent` 节点调用时把它作为 `SystemMessage` 拼到 messages 前 |
| `state_schema=SlotFlowAgentState` | **保留不变**。`StateGraph(SlotFlowAgentState)` 直接用。`messages`（`AgentState` 带，含 add_messages reducer）+ `slotflow` + `todos` 照旧 |
| `context_schema=RunContext` | **保留不变**。`StateGraph(..., context_schema=RunContext)`；节点签名 `(state, runtime: Runtime[RunContext])`，用 `runtime.context` 读业务开关（和现在中间件读 `runtime.context` 一致） |
| `checkpointer` | `graph.compile(checkpointer=checkpointer)`。不变 |
| `interrupt_before/after` | 不需要（我们用节点内 `interrupt()`），不传 |
| `response_format` / `transformers` / `cache` / `store` / `name` / `debug` | 暂不使用；`store` 若以后要做长期记忆语义检索可在此接 |

### 4.1 state_schema / context_schema 为什么能不动

- `SlotFlowAgentState` 继承 `langchain.agents.AgentState`，后者已带 `messages`（含 `add_messages` reducer）
  和 ReAct 所需的 messages 语义。`StateGraph(SlotFlowAgentState)` 直接吃这个 schema，节点返回的
  `{"messages": [...]}` 会被 reducer 正确合并。`slotflow` / `todos` 是 `NotRequired`，沿用现写法。
- `RunContext` 作为 `context_schema` 不变：节点里用 `runtime.context.mode / thinking_enabled /
  subagent_enabled / run_id / thread_id`，和现在中间件里的 `runtime.context` 读取完全一致。
  这正是 §12 强调的「RunContext vs config.configurable」边界，迁移不碰它。

### 4.2 graph 组装骨架（示意，非最终代码）

```python
def build_slotflow_graph(*, model, tools, system_prompt, run_context, checkpointer):
    builder = StateGraph(SlotFlowAgentState, context_schema=RunContext)

    builder.add_node("prepare",      prepare_node(model_run_ctx=run_context, base_prompt=system_prompt))
    builder.add_node("triage_gate",  triage_gate_node(model=model))
    builder.add_node("pre_model",    pre_model_node())
    builder.add_node("agent",        agent_node(model=model.bind_tools(tools)))
    builder.add_node("post_model",   post_model_node(max_concurrent=3))
    builder.add_node("tools",        ToolNode(tools, name="tools"))
    builder.add_node("finalize",     finalize_node(run_context=run_context))

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "triage_gate")
    builder.add_edge("triage_gate", "pre_model")
    builder.add_edge("pre_model", "agent")
    builder.add_edge("agent", "post_model")
    builder.add_conditional_edges("post_model", route_after_model, {"tools": "tools", "finalize": "finalize"})
    builder.add_edge("tools", "pre_model")      # ReAct 回环
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
```

- `triage_gate` 只在首步有效：节点内用 `state` 判断 fresh user turn + 是否已澄清（沿用现 `_is_fresh_user_turn`
  / `_already_clarified`），非首步直接 return 不 interrupt。
- `prepare` / `finalize` 每回合一次：靠 graph 拓扑（它们不在循环边上）天然保证，无需额外计数器。
- 节点函数用闭包工厂构造（注入 model/tools/run_context），保持节点体是纯 `(state, runtime) -> dict`。

### 4.3 子代理（`task_tool`）怎么处理

推荐：**子代理仍走 `task_tool` 委派机制，但子图也用新 node+edge 模式构建**；`post_model` 保留并发上限截断。
理由见 §5。子代理 runner（`subagents/tools.py`）内部的 `create_agent` 改成调 `build_slotflow_graph` 的简化版
（子图不需要 `triage_gate`/`finalize`，可复用同一 builder 的参数化）。

---

## 5. 多 agent 协作（核心问题二后半）

两条路：

- **路 A：tool 内嵌子图委派**（已否决）。`task_tool` 调 `SubagentTaskRunner`，子图用新 node+edge 构建；
  `post_model` 截断并发上限。优点：流式效果好、和主图解耦、不抢主图边拓扑、HITL 语义简单（子任务一般不需要 HITL）。
- **路 B（已选）：主图并行分支**。`route` 节点用 `Send` 扇出多个子 agent 分支 → `merge` 汇总。优点是「真并行」可视化；
  缺点是流式归并复杂、子任务 HITL 难定义、收益不抵当前成本。

节点化主图后，未来要从 A 升到 B，只需把 `route` 扩展成 `Send` 扇出 + 加 `merge` 节点，改造面小。**这是产品决策，需拍板。**

---

## 6. HITL / interrupt 在新 graph（核心问题一后半）

两条澄清路径都保留，且更清晰：

- **强制门**：`triage_gate` 节点内 `interrupt(payload)`。线性节点，resume 时 LangGraph 从该节点重放——
  和现在 `abefore_model` 重放 triage 同代价（已知、良性，HARNESS_NOTES §12 已记）。resume 值 → `HumanMessage`（原文话，不加元包装，避免模型回显，§12.3）。
- **自愿工具**：`tools` 节点执行 `ask_clarification` 时工具体内 `interrupt()` 暂停；resume 值即工具结果 `ToolMessage`。不变。

resume 检测仍由 `streaming.py` 的 `graph.aget_state(config).interrupts` 决定（前端零改动），澄清事件仍只来自
pending interrupt（re-popup 根因修复保留）。**关键约束**：`interrupt()` 必须在节点函数体内直接调用，不要包在会被
`except Exception` 吞掉的 try 里——沿用现在 `except GraphBubbleUp: raise` 在 fail-open 之前的写法（`GraphInterrupt`
是 `Exception` 子类，吞了就暂停失效）。

---

## 7. 投影层 / streaming / SSE 契约（必须保住的不变量）

- SSE 事件名（`run.prepared/message.delta/tool.delta/clarification.requested/todo.updated/state.snapshot/run.finished/run.error`）
  和前端消费（`lib/sse-parser.ts`、`hooks/use-chat-stream.ts`、`components/chat/*`）**完全不动**。
- `astream_events(version="v3")` 对编译后的 `StateGraph` 同样可用，messages/values/tool_calls projections 仍按现逻辑归一。
- **需跟着改**：`projections.py::is_summarization_node_name` 的识别方式（§3.1）。
- **需跟着改**：`runtime_summary` / `slotflow` 命名空间写入点从中间件改成 `prepare`/`finalize`，但写入内容/形状不变。
- `test_provider_reasoning_contract.py` 全程绿；`test_agent_adapter.py` / `test_clarify_gate_middleware.py` /
  `test_harness_middleware.py` 等按新结构改写（断言节点行为而非 hook 行为）。

---

## 8. 迁移策略（分阶段，都在新分支）

每阶段一个中文 conventional commit（重构/测试/文档），最后开 PR（必过 `Verify`）。

### 阶段 A — 抽取纯函数（零行为变化）
把每个中间件核心逻辑抽成无状态函数（`prepare_runtime_summary(state,ctx)` 等），中间件先改成调这些函数。
仍用 `create_agent`+middleware。跑全量离线测试应全绿。
**工作量：~1.5 天**。产物：`harness/steps/*.py`、中间件瘦身。

### 阶段 B — 手写 graph 骨架
新建 `harness/graph.py` 用 `StateGraph(SlotFlowAgentState)` 组装 §2 拓扑，节点内调阶段 A 纯函数。
`builder.py` 切到新 graph，保留旧 `create_agent` 路径作 fallback 开关（env/flag）便于回滚。
先只迁最简单的（RuntimeSummary/Uploads/ArtifactDiscovery/Todo/SubagentLimit/ToolSafety/Dangling），
ClarifyGate/LongTermMemory/Summarization 留到 C。
**工作量：~2 天**。产物：`harness/graph.py`、`builder.py` 切换、基本 ReAct 循环跑通。

### 阶段 C — 迁难中间件
按 §3 表逐个搬 ClarifyGate（→`triage_gate`）、LongTermMemory（→`prepare` 检索 + `finalize` 后台抽取 +
`pre_model` system 注入）、Summarization（→`pre_model`，含 §3.1 投影层适配）。每搬一个跑一次全量测试。
**工作量：~2 天**。

### 阶段 D — 投影层适配 + 子代理切新子图
更新 summarization 识别；`subagents/tools.py` 子图改用新 builder 参数化版。跑 `test_provider_reasoning_contract.py`
和子代理测试。
**工作量：~1 天**。

### 阶段 E — 删旧路径 + 文档
删 `create_agent` fallback、`middleware/__init__` 旧导出、死中间件类；更新 `AGENTS.md`（架构段重写为 node+edge）
和 `HARNESS_NOTES.md`（新增 §13：为什么从 middleware 迁到显式 graph、live 实测对比、新不变量）。
**工作量：~1 天**。

### 阶段 F — live 验证
用 HARNESS_NOTES §9 的 in-process 探针对 `deepseek-v4-pro` 跑 clarify/memory/todo/subagent 四项，
确认行为不回归；重点验 re-popup 不复发、triage 不污染用户流、thinking 模式不报 `reasoning_content` 回传错。
**工作量：~0.5 天**。

**总工作量：约 8 个工作日**（含测试改写与 live 验证；不含 PR review 往返）。

---

## 9. 不变量（迁移全程勿回归，来自 HARNESS_NOTES §5/§8/§12.6）

1. 不强制 `tool_choice`（DeepSeek thinking 会 400）。
2. 内部小调用（triage / memory 抽取）必须 `config={"callbacks": []}`，否则污染用户流。
3. 模型客户端 `max_retries>=2`（长 run 容错）。
4. HITL = `interrupt()`/`resume`：节点内 `interrupt` + 无其它副作用（resume 会重放节点）；
   `interrupt()` 不能被 `except Exception` 吞掉（`GraphInterrupt` 是 `Exception` 子类）。
5. 澄清事件只能来自「当前待处理 interrupt」，禁止再从消息历史扫 `ask_clarification` ToolMessage 派生（re-popup 根因）。
6. 门注入答案用用户原话 `HumanMessage`，不加「针对澄清问题…用户的回答是…」元包装（会被模型回显）。
7. DeepSeek v4 thinking 默认开，OFF 必须 `extra_body={"thinking":{"type":"disabled"}}` 显式下发。
8. reasoning/content 通道分离不串（`test_provider_reasoning_contract.py` 全程绿）。
9. 脚本化 fake-model graph 测试：`proactive_memory_extraction_enabled=False`、`clarify_gate_enabled=False`
   （triage/抽取会吃掉预设响应）——迁移后对应的新开关要保留等价语义。

---

## 10. 已拍板决策（2026-06-30）

1. **子代理形态**：**主图并行分支**（`Send` 扇出 + `merge` 汇总），不用 `task_tool` 子图委派。
   - `route` 判断可拆分并行任务 → `Send` 扇出多个 `subagent` 节点（各自精简 ReAct 子循环）→
     `merge` 汇总 → 回主循环。`post_model` 的并发上限改为限制 `Send` 扇出数。
   - 子任务不带 HITL；HITL 只在主图 `triage_gate`/`tools`。
   - token 取舍：独立任务用并行分支更省（上下文隔离），汇总多一次输入；依赖型任务仍串行。
2. **middleware 抽象**：**完全弃用 `AgentMiddleware`**，不留例外。
3. **迁移方式**：**一步到位**，不留 `create_agent` fallback。测试先行 + live 探针兜底。
4. **summarization 识别**：**复用官方 `SummarizationMiddleware` 的 `lc_source="summarization"` 标签**
   （它已用 `RemoveMessage` 删旧消息、摘要带标签、调用带 `config.metadata.lc_source`）。
   抽成 `pre_model` 内函数；`projections.py::has_lc_source_summarization` 本就识别该标签，改动极小。
   触发点：model 执行前、上下文超阈值——属 `pre_model`。
5. **记忆重写为 mem0**：作为 graph 迁移**之后的独立阶段**（不混进本次重构）。
   - 走 mem0 OSS 本地自建（local-first）：`mem0ai==2.0.10`，`vector_store`=本地 sqlite-vec
     （环境已装 `sqlite-vec==0.1.9`）或本地 qdrant，`llm` 复用对话模型。
   - embedding 起步用 OpenAI 兼容 embedding API（复用现有中转站 `*_BASE_URL`/`*_API_KEY`，零新模型）；
     后续可换本地 sentence-transformers/Ollama embedding 完全离线。
   - 不走 mem0 Cloud（数据上云、按量计费，与 local-first 冲突）。
   - 替换 `harness/memory/store.py` 手写层；`LongTermMemory` 改调 mem0 `add/search/get_all`。

### 10.1 复用现成实现（不重复造轮子）
- `langgraph.prebuilt.ToolNode` / `tools_condition`：`tools` 节点 + `route` 条件边直接用官方。
- `langchain.agents.middleware.SummarizationMiddleware`：抽其 `RemoveMessage` + `lc_source` 逻辑进 `pre_model`。
- `langgraph.types.Send`：主图并行分支扇出。
- `StateGraph` / `AgentState` / `add_messages`：state schema 不变，直接用。
- mem0：记忆层替换（独立阶段）。

## 11. 分支创建（环境限制备注）

方案要求在新分支 `refactor/langgraph-node-edge-graph` 上做。当前环境 `.git` 曾为只读（已恢复），
若仍遇到 `cannot lock ref`，请本机执行：
```bash
git checkout -b refactor/langgraph-node-edge-graph
```
本方案文档先落在 `docs/refactor-plan.md`，代码改动待你拍板 §10 后从阶段 A 开始。

---

## 12. 附录：主图并行分支是什么样（vs 旧 task_tool 子图委派）

### 旧方式（task_tool 子图委派，本次重构不采用）
```
主图循环: prepare → triage_gate → pre_model → agent → post_model → route → tools → ...
                                              ↑
                                              │  模型在一步里调 3 次 task_tool
                                              │  tools 节点串行执行每个 task_tool
                                              │  每个 task_tool 内部又起一个完整 create_agent 子图
                                              └── 三个子任务在 tools 节点内「顺序」跑完，
                                                  主图只看到 3 条 ToolMessage 结果
```
特点：并行只是「模型一次发出多个工具调用」，但 ToolNode 默认是并行执行 task_tool 的；
可每个 task_tool 内部是独立子图，**主图看不到并行结构**，也没法对并行分支单独流式/限流。

### 新方式（主图并行分支，采用）
```
主图:
  prepare → triage_gate → pre_model → agent → post_model → route
                                                   │
                          ┌────────────────────────┼────────────────────────┐
                          │                        │                        │
                    no tool_calls            has tool_calls          splittable parallel
                          │                        │                        │
                       finalize→END              tools→pre_model     Send(subagent)×N
                                                                        │
                                                                   ┌────┼────┐
                                 (LangGraph 把 N 个 subagent 节点实例并行调度) │
                                 subagent_1   subagent_2   subagent_3          │
                                   │            │            │                 │
                                   └────────────┴────────────┘                 │
                                              │                                │
                                           merge  （把 N 个子结果汇总成一条消息） │
                                              │                                │
                                          pre_model ←─────────────────────────┘
```

具体形态：
- `route` 节点多一个判断：`agent` 这一步的 tool_calls 里若是「可拆分的并行任务」
  （如「分别调研 A/B/C」），不进 `tools`，而是用 `Send("subagent", {...})` 扇出 N 份。
- 每个 `subagent` 节点是一个**精简 ReAct 子循环**（只有 pre_model→agent→tools→route，
  没有 triage_gate / finalize / 记忆抽取），跑完返回自己的结果消息。
- LangGraph 的 `Send` 会**并行调度**这 N 个 subagent 实例（受 `post_model` 的扇出上限约束）。
- 全部完成后进 `merge` 节点：把 N 条结果合成一条（或一组）消息，回主循环的 `pre_model`，
  主 agent 据此继续（综合 / 产出 artifact）。
- HITL 只在主图：子任务不带 `interrupt`（并行子任务一般是已明确的具体动作）。

为什么这样更省 token（独立任务）：
- 每个子 agent 上下文隔离，subagent_2 不带 subagent_1 的推理/工具结果历史；
- 主 agent 在 merge 前也不用一直拖着三个任务的中间过程；
- 代价是 merge 多一次「读 N 个结果」的输入，问题越大这点越可忽略。
依赖型任务（B 需要 A 的产出）仍走串行的 `tools` 路径，不扇出。

---

## 13. 迭代 6（2026-06-30 续）：todo 恢复 + 思考流调研 + 子代理统一 + 清理

### 13.1 todo 功能丢失（用户反馈，已修）
**根因**：node+edge 迁移时 `write_todos` 工具丢失。create_agent 时代它靠
`SlotFlowTodoMiddleware` 继承官方 `TodoListMiddleware`、经 `.tools` 属性被 `create_agent`
拾起；迁移后 `build_harness_tools` 从未把它加进去 → 模型无 `write_todos` 可调 → 无 todo。
**修法**（改用 langgraph 原生，不留 middleware 兼容）：
- `harness/steps/todo.py` 直接复用官方 `write_todos` 工具（返回
  `Command(update={"todos","messages":[ToolMessage]})`，ToolNode 原生支持，state 已有 `todos`）；
  抽 `SLOTFLOW_TODO_SYSTEM_PROMPT` + `todo_reminder_update` + 新增 `todo_parallel_call_guard`
  （官方 `after_model` 禁止并行 `write_todos` 的等价物）为纯函数。
- `harness/tools/registry.py`：`plan_enabled` 时把 `write_todos_tool` 加入工具面。
- `harness/graph.py`：`pre_model` 注入 todo system prompt + reminder；`post_model` 加
  `todo_parallel_call_guard`（与 subagent cap 并存，cap 结果优先）。
验证：276 passed；ultra 有 `write_todos`、flash 无（plan gating 正确）。

### 13.2 思考流延迟调研（已回退，保留现状 + 记录）
用户反馈思考块延迟显示。调研 langgraph v3：`AsyncChatModelStream` 原生提供 `.reasoning` /
`.text` typed projection（async iterable of deltas）。尝试改用原生 projection 顺序消费、
移除 create_agent 时代的手写 `asyncio.Queue` 交错 pump。
**结果**：实测死锁真实图。根因——v3 projection channel 是**单消费者** + **caller-driven pump**
（`StreamChannel.__aiter__` 只能调用一次；`_arequest_more` 驱动共享 graph pump）。顺序消费
`.reasoning` 再 `.text` 时，graph pump 被 reasoning projection 独占，text 的数据到了也无法
推进，死锁。手写 queue 并发 pump 两个 channel 再交错输出，正是绕开单消费者限制的必要做法。
**结论**：保留现有队列交错方案；延迟感来自交错缓冲，是 v3 单消费者约束下的必要代价，
不是能简单用「原生 API」消除的。已回退改动，276 passed 恢复。教训记入 HARNESS_NOTES §13.7。

### 13.3 子代理统一（移除最后一个 create_agent）
`subagents/tools.py::SubagentTaskRunner` 原内部起 `create_agent` 子图，是重构后后端最后一个
`create_agent` 调用点。改为 `build_slotflow_graph`（node+edge），子代理图用精简配置
（关 clarify_gate/summarization/memory/skills_preflight/uploads/todo/artifact/runtime_summary，
只留 dangling+tool_safety），与主图同一组装入口。`build_slotflow_graph` import 延迟到 `arun`
内部，避免 graph↔subagents↔tools registry 循环导入。验证：子代理 task_tool 端到端测试通过。

### 13.4 清理
- `builder.py` 删除未使用的 `middleware` 参数（重构后无人传，`AgentMiddleware` 已删）。
- 后端不再有任何 `create_agent` / `AgentMiddleware` 调用（grep 确认仅剩文档/注释历史引用）。
- 补 `write_todos` 工具面断言到 `test_harness_tools` / `test_harness_builder`，防回归。

### 13.5 验证
`ruff check app tests` 通过；`pytest -q -k "not live"` **276 passed**。live 验证待用户在前端
确认 todo 与思考流表现（思考流延迟非本次可消，见 §13.2）。


## 14. 2026-07-16 ????????codex ???

????????????/????????????????????????? API?SSE?SQLite/checkpoint??? schema ??????????

### 14.1 ????

- `37eafb8`?????? importer ? shadcn surface???????? `@radix-ui/react-slot`?????? Knip source/export/types ?????? Playwright launcher ???????
- `066f04d`???? 27 ???? fetch ????/JSON ????? `src/lib/api-client.ts`??? Vitest API client ?????
- `56682ea`?MCP user/base server ? enabled/pinned ?????????????? protected/default override ???
- `fc97169`??????? skill/MCP/memory ??????????? toast ??? `runUiAction`??????? try/catch?

### 14.2 ?????

?? 5 ?? Vulture ? Knip ???Vulture ??? Pydantic/?? fake ???????????Knip ? Playwright launcher ????????????/bootstrap ???????????????

`Makefile verify` ???? backend pytest?frontend Vitest?TypeScript?dead-code contract ? Next build??? 7 ?????backend `401 passed, 1 skipped`?frontend Vitest `2 passed`?ruff?Knip?typecheck?build ? `git diff --check` ????

??????`backend/app` + `frontend/src`?????????? 1,300 ???????/????????????????????????????????
