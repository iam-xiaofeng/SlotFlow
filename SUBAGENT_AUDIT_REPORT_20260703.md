# SlotFlow 全库大扫除 · 审计报告(从中断会话重建)

> 生成:2026-07-03,由跨系统(Windows Claude Code)会话从对话 `13a9eb55-227e-401a-ae61-31f408a1d467`
> 及其 8 份 subagent 记录中重建。这份文件就是用户多次要求、却因 429 反复中断始终没能落盘的
> "subagent 完整问题存档"。
>
> **证据可信度分级**:
> - ✅ **已验证** = 原会话协调者或 subagent 亲手对照代码/运行确认过;
> - 🔎 **审计发现** = subagent 侦查过程(thinking/grep 证据)中得出、有具体文件行号支撑;
> - ❓ **待核实** = 被 429 打断前只提出了怀疑,实施前需先确认。
>
> 原始记录位置见文末"证据链附录"。配套进度文件:`HANDOFF_CROSS_SESSION_20260703.md`。

---

## 0. 考古:这份报告为什么长这样

原任务(用户原话摘要):全库大扫除——清理前后端/测试的过时代码、治标补丁残留、兼容性冗余;
复杂实现对照开源项目与 langchain/langgraph 新版内置 API 换掉手写;收紧系统提示词与前后端链路;
左侧工作空间与右侧终端交互优化;全部做完后用测试+scratch/harness 脚本模拟前端点击全链路验证;
最终产出"本次所有改动+发现的问题"文档与"每个 API 调用链路"文档(两份独立 .md)。

原会话派出 4 个审计 subagent(各重试一轮,共 8 份记录),结局:

| 审计方向 | 记录 | 结局 |
|---|---|---|
| 后端过时代码 | a05e7b56(800KB) | 侦查基本跑完,**写总结前被 429 击杀** |
| 前端结构与UX | a2596564(816KB) | 同上 |
| 测试套件 | aee15907(850KB) | 同上(后台 pytest 基线也没收割到结果) |
| langchain/langgraph 内置 | ae5e6eb0(476KB) | ✅ **完整交付 13K 字报告**(§3 全文吸收) |

主会话本体:**零代码改动、零文档落盘**,但留下一段关键的中期综合(两个 P0 bug 的亲手验证),
已并入本报告 §1。今晨用户在 WSL 让 Claude 续做(会话 b5080cab),4 次尝试全部 429,零产出。

---

## 1. P0:两个已验证的真 bug(graph.py 摘要链路)✅

两个都源于"把官方 `SummarizationMiddleware` 复用在它设计所属的 create_agent 循环之外"。
离线测试全用 fake model、不解析消息格式,所以从未暴露。

### Bug 1:摘要一旦触发,下一次模型调用即崩溃
- `SummarizationMiddleware.before_model` 返回
  `{"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *summary, *preserved]}` ——
  这是给 `add_messages` **reducer** 消费的哨兵协议。
- `graph.py:333` 却把同一列表原样拷进 `llm_input_messages`(普通 last-write-wins 通道,无 reducer),
  `agent` 节点直接把它喂给模型。
- ✅ 已验证:`langchain_openai._convert_message_to_dict(RemoveMessage(...))` 抛
  `TypeError: Got unknown type` → OpenAI 兼容路径(含 DeepSeek)首次摘要触发后必崩。

### Bug 2:`llm_input_messages` 一旦写入即永久冻结模型输入
- `pre_model` 仅在 dangling 修复发生变化时写 `llm_input_messages`;summarize 节点仅在触发时写;
  **没有任何节点清空它**,且它被 checkpoint 持久化。
- 一旦置位,`agent` 节点此后每一步都优先读这个旧快照——新工具结果、新用户消息模型全看不见。
- 官方 `create_react_agent` 的约定是 `pre_model_hook` **每一步**都发 `llm_input_messages`;
  本图只在个别节点偶发写入,契约被违反。

**修法方向(二选一,见 §6 批次1)**:
A. 修胶水:pre_model 每步显式重算/清空 `llm_input_messages`,summarize 输出先解掉 RemoveMessage
   哨兵(直接给"summary+preserved"的最终列表);
B. 让摘要回归 `create_agent` 中间件循环托管(架构回摆,改动大,不推荐本轮做)。

---

## 2. 后端:死代码·无效开关·重复逻辑·遗留补丁

### 2.1 确认可直接删除 ✅
| 对象 | 位置 | 证据 |
|---|---|---|
| 整个 `message_utils.py`(131行) | `backend/app/chat/message_utils.py` | 全库 grep 零 import(app+tests) |
| `repair_model_request()` | `harness/steps/dangling_tool_call.py` | 零调用者;引用的还是旧 middleware 接口(`ModelRequest`) |
| 构建后即丢弃的 summarization middleware | `harness/graph.py:238-247`(make_pre_model_node 内) | 构建结果无人使用,make_summarization_node 里另建了一份 |
| `GraphBubbleUp` 再导出 shim | `harness/steps/clarify_gate.py`(import+`__all__`) | 模块内未使用;graph.py 直接从 `langgraph.errors` 导入 |

### 2.2 无读者的配置开关 🔎
- `dangling_tool_call_enabled`、`tool_safety_enabled`:`chat/runtime/config.py` 从环境变量读入、
  `subagents/tools.py` 里还显式传值,但 **graph.py 从不读取**——dangling 修复与 tool safety
  实际是无条件启用。要么让 graph 尊重开关,要么删掉开关与对应 env(推荐后者,减少假旋钮)。
- 反向问题:`clarify_gate_enabled`、`subagent_limit_enabled`、`subagent_max_concurrent`、
  `proactive_memory_extraction_enabled` 在 graph 里被读取,却**没有对应 env 装配入口**(不可配)。
  按需补 env 或接受硬编码并写进 AGENTS.md。

### 2.3 同一段"消息内容拍平成文本"逻辑存在 ≥5 份 🔎
分布:`message_utils.py`(将删)、`agent_adapter/projections.py::extract_reasoning_text`、
`chat/title_generation.py::normalize_content`、`harness/steps/clarify_gate.py::_message_text`、
`harness/steps/long_term_memory.py`(content_to_text/message_text)。行为还有微差
(str|None vs 空串兜底)。**动作**:收敛到 projections.py 一处(它是纯函数层、已有测试),
其余全部改 import;注意各处 None/空串语义差异要在收敛时统一并补测试。

### 2.4 记忆库里的场景硬编码补丁 🔎
`harness/memory/store.py::canonicalize_profile_memory`:
- 中文职业检测硬编码("研究生/硕士/博士"→一律记"研究生";专业硬编码"控制工程")——
  这是按 HARNESS_NOTES 示例场景打的测试特化补丁,真实用户数据会被错误规范化;
- `strip_memory_command_prefix` 多个正则叠补丁(包括一个从"中"字开始兜漏的 pattern);
- 读写两侧都做防御性二次规范化(重复)。
**动作**:去掉职业/专业硬编码(改为原文入库),前缀剥离收敛为一个可测的规则表;
对应的 `tests/test_harness_memory.py` 硬编码断言(肖峰/研究生/控制工程——疑似真实开发者信息)同步重写。

### 2.5 遗留参数与测试特化路径 🔎
- `steps/skills_preflight.py::uses_default_finder=False` 分支:仅 `tests/test_harness_steps.py:190`
  使用,graph 恒走默认路径——删分支+改测试。
- `steps/clarify_gate.py::run_triage(triage_fn=...)`:仅测试注入用。可保留(合理测试缝),
  但 docstring 要改(现在还说自己是从 middleware 抽出来的)。
- `steps/long_term_memory.py::build_turn_memory_content`:向后兼容 wrapper,仅测试引用——删+改测试。
- `append_memory_system_message` 的 `None` 分支:graph 恒传非空——删分支。
- `make_agent_node` 的 `base_system` 回退:两分支都从空 prompt 构建,冗余——合并。
- `build_slotflow_graph(model: str | BaseChatModel)`:str 路径会在 `bind_tools` 上炸,
  实际从未传 str——类型缩窄为 `BaseChatModel`。

### 2.6 文档性腐烂(低风险高价值)🔎
`clarify_gate.py`、`clarification.py`、`long_term_memory.py`、`harness/middleware/__init__.py`
等的 docstring 仍自称"从 SlotFlowXxxMiddleware 抽取/配合 middleware 使用";
`workspace/routes.py:168` 有 legacy/flat artifacts 兼容注释待评估是否还需要。
node+edge 重构已是既成事实,这批叙述统一改写,`app/harness/middleware/` 目录本身只剩 config,
考虑改名为 `harness/flags.py`(名实相副)或至少在 `__init__` 里说清"这里没有 middleware"。

### 2.7 其他确认为"保留不动"的 ✅
- `repository.py`:纯 SQLite CRUD,无规范化重复(审计时的怀疑被否定);
- ruff F401/F811/F841/ERA 全绿,无未用 import/变量层面的垃圾;
- 全库无真实 TODO/FIXME/HACK 注释欠账;
- checkpointer 工厂的手写 `aclose_checkpointer`:官方 saver 无公开 close(),FastAPI lifespan
  下手写是合理的,保留。

---

## 3. langchain/langgraph 内置替换映射(完整交付的研究,全文要点)✅

版本盘点(uv.lock=venv 已核对):langgraph 1.2.2 / langchain 1.3.2 / langchain-core 1.4.7 /
langchain-anthropic 1.4.6 …均为最新 major,只差 patch。**本节所有能力都在已装版本里,无需升级。**

| SlotFlow 手写机制 | 内置等价物 | 判定 |
|---|---|---|
| `RunnableCallable` 私有 API(graph.py:31,575 双 sync/async agent 节点) | `RunnableLambda(func, afunc=...)` | **换,drop-in**,去私有依赖 |
| `_slotflow_tool_safety_wrapper`(graph.py:484-533 + steps/tool_safety.py) | `ToolNode(handle_tool_errors=callable)`:官方已内置 GraphBubbleUp 先行 re-raise、error ToolMessage、未知工具校验 | **大部分可换**;仅"未注册 host-exec 工具"的定制文案需 `wrap_tool_call` 保留 |
| `write_todos` 工具(steps/todo.py:90) | `langchain.agents.middleware.todo.write_todos`(同名同 state 键同 Command 形状) | **可换**,但保留 `text`→`content` 别名 shim(真实模型行为踩出来的);官方还带更全的工具描述与系统提示段 |
| `todo_parallel_call_guard`(steps/todo.py:215) | `TodoListMiddleware.after_model`(逐字同逻辑) | 可改为直接委托官方(与 summarization 委托同法),或保留注明 parity |
| todo 催写/重注入启发式(steps/todo.py:119-199) | 无内置 | **保留手写**(产品行为) |
| dangling tool-call 修复 | **无内置**(全库 grep 零命中;ValidationNode 只验参) | **保留手写**,这是真实空缺 |
| 子代理并行削峰(steps/subagent_limit.py) | `ToolCallLimitMiddleware` 语义不合(按累计计数,非单条 AIMessage 内裁剪) | **保留手写** |
| `task_tool`/`subagent_list` | 无内置(deepagents/langgraph-supervisor 均未安装) | **保留手写** |
| clarify 前置门(interrupt 原语) | `HumanInTheLoopMiddleware` 不等价(只拦已发射的 tool call,resume 协议也不同) | **保留 raw interrupt()** |
| checkpointer 工厂 | 已在用官方 AsyncSqliteSaver/AsyncPostgresSaver | 保持 |
| `workspace_grep`(d04f69b) | `FilesystemFileSearchMiddleware` 跑在宿主 FS,不走沙箱抽象 | 保留手写 |
| 摘要节点复用 SummarizationMiddleware | 本就是官方件,但胶水违反哨兵契约 | **修胶水**(见 §1) |

顺带可选增强(现无对应物,不属本轮清理):`ModelRetry/ToolRetry/ModelFallback`、
`ModelCallLimitMiddleware`(防失控循环)、`ContextEditingMiddleware`、`AnthropicPromptCachingMiddleware`、
`add_node(retry=RetryPolicy())`。

---

## 4. 前端:死代码与交互问题

### 4.1 死代码 🔎(实施前跑 `pnpm typecheck` 复核)
| 对象 | 证据 |
|---|---|
| `chat-sidebar-context.tsx` 整文件 | 全 src 零 importer;功能已被 `DirectoryModal` 取代 |
| `chat-app.tsx` 的 `artifactPreview` state+handlers | fetch 结果无人渲染;`WorkspacePanel` 收 `selectedPath` 后自己拉取 |
| `artifact-panel.tsx` 的 `ArtifactWorkspacePanel`、`ArtifactWorkspaceToolbar` | 仅定义无使用;若删除,`ui/select.tsx` 亦随之无主 |
| `chat-stream.ts::getThread` | 零调用 |
| `chat-stream.ts::resolveChatStreamUrl` | 仅文件内使用,去 export |
| `useChatStream` 返回的 `events`+`appendEvent`、`startNewThread` | chat-app 未解构使用;`events` 数组每事件都在涨(白耗内存) |
| `hasTodoListForCurrentRunRef` | ❓ 审计标记疑似未用,删前需终确认 |
| `parseTodos` 的 legacy `text` 兜底 | ❓ 后端 `write_todos` 仍有 text 别名输入,但落库/事件侧已归一为 `content`;需确认事件流无 text 后再删 |

### 4.2 交互问题(用户原始抱怨的技术根源)🔎
1. **右侧面板没有独立开关**:面板只随"点开某个文件/artifact"打开——当前对话无产物、无上传时,
   **新加的终端功能不可达**(用户抱怨"终端交互怪"的直接原因)。
   → 动作:给右侧面板加常驻开关(如顶栏按钮),空态直接落到终端 tab。
2. **`handleOpenArtifactPanel` 名不副实**:实际打开的是工作空间目录 modal,不是面板——重命名。
3. **跨线程打开文件会静默切换会话**:`handleOpenWorkspaceFile` 对属于其他 thread 的文件会切走
   当前对话(sidebar modal 与右侧下拉两个入口都如此)——至少加确认或明示。
4. 左侧"工作空间"入口与右侧面板职责重叠、路径绕(用户"交互怪怪的"的另一半)——
   建议:左侧仅保留"全局文件目录"(浏览/搜索),打开动作统一路由到右侧面板;
   具体交互稿实施时再定,先做 1-3。

---

## 5. 测试套件(28 文件,8326 行)

### 5.1 结构性问题 🔎
- **无 conftest.py**:fixture 仅 `test_chat_repository.py` 一处;各文件自造帮手。
- **帮手重复**:`ToolAwareFake*` 假模型在 test_clarify_gate / test_harness_tools /
  test_agent_adapter / test_harness_sandbox 各有一份;`_bundle()`、`ProjectionChannel`、
  `_client`、`_run_context/_ctx/_task_call` 多处重复 → 建 conftest.py 收敛。
- **整文件级重复**:`test_subagent_limit.py` 与 `test_harness_steps.py` 的子代理限流用例基本相同;
  `test_clarify_gate.py` 与 `test_harness_steps.py` 平行覆盖 clarify_mode_enabled /
  is_fresh_user_turn / already_clarified / parse_triage → 择一保留(建议留 test_clarify_gate
  作为专题文件,steps 文件去重)。
- `test_harness_steps.py:61 _ctx` ❓ 疑似未用(clarify_gate 里同名在用,别删错文件)。

### 5.2 脆弱断言 🔎
- `test_harness_builder.py::test_harness_builder_passes_graph_boundary_arguments`:
  对系统提示词逐字 pin 了 10+ 个英文子串——收紧系统提示词的任何措辞改动都会碎;
  改为断言结构性锚点(段落标题/工具名)。
- `test_harness_memory.py`:硬编码"肖峰/研究生/控制工程"断言,与 §2.4 的硬编码互为因果,一起重写。
- `test_agent_adapter.py` 引用节点名 `"SlotFlowSummarizationMiddleware.before_model"` ❓:
  审计被杀前正要核对 graph 实际节点名——若已改名,这条测试在测一个不存在的过滤键。
- `test_harness_builder` 一处 docstring 还叫 `..._can_disable_builtin_middleware`(旧命名叙事)。
- `test_harness_sandbox.py` 末尾条件跑真 Docker 的用例未标 live/skip 标记,CI 环境差异下行为不定。

### 5.3 基线状态 ❓
原会话的后台 `uv run pytest -q -k "not live"` 被 429 打断未收割。
**本轮实施第一步就是重跑基线**,拿到当前红绿再动手。

---

## 6. 优先级矩阵与执行批次(=commit 划分)

| 批次 | 内容 | commit 主题 | 测试门 |
|---|---|---|---|
| 0 | 重跑 pytest 基线并存档 | (不 commit,结果记入 HANDOFF) | — |
| 1 | §1 两个 P0 bug(RemoveMessage 哨兵+llm_input_messages 冻结),补回归测试(fake OpenAI 格式转换器复现 TypeError;摘要后再来一轮对话可见新消息) | `fix(harness): summarization sentinel leak + llm_input_messages staleness` | 后端全绿 |
| 2 | §2.1 死代码删除 + §2.2 假开关清理 + §2.5 遗留参数/分支 + §2.6 docstring 群改 | `chore(backend): remove dead code, fake flags, legacy shims` | 后端全绿 |
| 3 | §2.3 文本拍平五合一 + §2.4 记忆库去硬编码(连带 §5.2 memory 测试重写) | `refactor(backend): unify message-text flattening; de-hardcode memory canonicalization` | 后端全绿 |
| 4 | §3 内置替换:RunnableLambda 换私有 API;ToolNode(handle_tool_errors) 换手写 safety wrapper(保留 wrap_tool_call 定制文案);todo 工具对齐官方+保留 text 别名 | `refactor(harness): adopt built-in RunnableLambda/ToolNode error handling/official write_todos` | 后端全绿 |
| 5 | §4 前端:删死码;右侧面板常驻开关+空态终端;重命名 handleOpenArtifactPanel;跨线程打开加确认 | `feat(frontend): reachable terminal panel + workspace UX fixes; drop dead code` | `pnpm typecheck`+`pnpm build` |
| 6 | §5 测试整备:conftest 收敛帮手;删除重复用例文件;修脆弱断言 | `test: consolidate fixtures, drop duplicated suites, unbrittle assertions` | 后端全绿 |
| 7 | 链路验证:scratch/harness 探针脚本模拟前端行为走真实 API 链路;产出两份最终文档(改动总结.md、API调用链路.md) | `docs: cleanup changelog + API call-chain reference` | `make verify` 全绿 |
| 8 | 提 PR(分支从 `refactor/langgraph-node-edge-graph` 切出) | — | PR CI |

每批次完成即更新 `HANDOFF_CROSS_SESSION_20260703.md`(用户要求的 429 保险)。

---

## 7. 证据链附录(接手者按图索骥)

- 主会话:`~/.claude/projects/-home-dell-code-SlotFlow/13a9eb55-….jsonl`(302 行);
  其中期综合(两个 P0 bug 验证)在最长的一条 assistant 文本里。
- 子代理原始记录:同名目录 `subagents/agent-*.jsonl`(8 份,映射见 §0 表)。
- 蒸馏摘要(thinking/搜索证据浓缩,~/tmp 重启即失,可用脚本再生):
  `/tmp/subagent_digests/{backend-stale-code,frontend-ux,test-staleness,langchain-research-run1,langchain-research-run2}.txt`
  再生脚本:`/tmp/distill.py`(蒸馏)、`/tmp/extract2.py`(取各代理最终文本)。
- langchain 研究完整原文:`/tmp/subagent_reports/ae5e6eb0acb2c2c2c.md`(§3 为其要点收编,
  含逐条源码行号佐证,实施批次 4 前建议重读原文)。
- 今晨 429 全灭的续做尝试:`b5080cab-….jsonl`;同任务另一份会话副本:`6c2f006b-….jsonl`。
