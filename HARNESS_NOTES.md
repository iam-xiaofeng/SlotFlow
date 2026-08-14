# SlotFlow Harness 工程笔记（Harness Engineering Notes）

> 配合 `AGENTS.md` 一起阅读。`AGENTS.md` 是仓库的「地图」（架构 / 约定 / 不变量），
> 本文是「工程日志」：我们遇到的**行为问题**、**试过的方案**、**结果**、**现在怎么做**、
> **实测结论**。给新对话（人或 AI）快速建立对 harness 现状的准确认知。
>
> 最近一次更新：2026-06-21（feature/custom-endpoint-routing；迭代 4 = HITL 改 interrupt/resume，见 §12）。
> 所有结论均经**真实 DeepSeek API 实测**，非纸面推断。

---

## 1. 我们要解决的问题（行为层面）

SlotFlow 功能已基本齐全（clarify 弹框、todo、subagent、长期记忆、skills/MCP、多 provider
reasoning 流）。但真实使用中，agent 的**行为**不达标：

1. **欠规约也硬答 + 脑补（最严重）**：请求说不清时，模型不调 `ask_clarification`，而是自己
   臆测一个方向就开干，产出经常不是用户想要的。
2. **不主动用 skills/MCP**：即使本地有相关 Skill 或 GitHub 上有现成 Skill，模型也很少去
   `skill_match` / `find-skills` 发现并使用。
3. **不主动拆分到 subagent**：明显可并行的任务（「分别调研 A/B/C 再对比」）也一把梳到底，
   不用 `task_tool` 委派。
4. **长期记忆使用率低**：模型很少主动 `memory_save` 用户的持久事实。
5. **不主动规划**：中等复杂度任务不写 `write_todos`，计划只在「脑子里」。

用户的核心判断（已验证正确）：**这些靠改提示词治不好，需要工程上的强约束。**

---

## 2. 为什么提示词不够（根因）

读 `harness/builder.py` 的 system prompt 与 `harness/middleware/*` 后定位到根因：

- 现有中间件几乎都是**「软」**的——注入上下文或转换消息，没有一个是**「硬门」**。
- **`clarification_middleware` 是被动的**：只在模型**自己决定**调用 `ask_clarification` 时，
  才用 `wrap_tool_call` 把它转成弹框。没有任何东西**强迫**模型去问。模型一旦选择「我猜得出来」
  就直接脑补，中间件根本没机会介入。这是「不问 + 幻觉」的结构性根源。
- **`skills_preflight_middleware` 只注入候选**：把命中的 Skill 作为上下文塞进去
  （`before_agent`），模型可无视。是「提示」不是「约束」。
- **mode 已分级**（`flash | pro | ultra` → thinking/plan/subagent 三个 flag），但 flag 只切
  功能开关，没切「约束强度」。

结论：**把强制逻辑下沉到 LangGraph 中间件，做成确定性的门（gate），并按 mode 分级。**
（这也正是 `AGENTS.md` 早就写下的方向：prompt 不够就升级到 middleware-level enforcement。）

---

## 3. 方案选择

给出三档（轻 / 中 / 重）后，选定**中档：分级强约束**，澄清判定用 **LLM triage**：

| mode  | 约束 |
|-------|------|
| flash | 维持现状（软提示） |
| pro   | + **澄清门**：欠规约 → 强制澄清，不让主模型瞎答 |
| ultra | + **技能优先**（命中已装 Skill → 首步 `skill_match`）+ **计划优先**（非平凡任务 → `write_todos`） |

判定方式：回答前跑一次**廉价结构化 triage**（一次小 LLM 调用），输出
`{actionable, clarification_type, question, options, needs_plan}`。

---

## 4. 实现：`SlotFlowClarifyGateMiddleware`

> ⚠️ **本节描述的是迭代 1–3 的实现（`jump_to=end` + 合成 AIMessage/ToolMessage + 投影扫历史）。
> 迭代 4（2026-06-21）已把 HITL 澄清整体改为 LangGraph 原生 `interrupt()/resume`,见 [§12](#12-迭代-42026-06-21hitl-澄清改用-langgraph-原生-interruptresume--完整-agent-链路)。
> 下面保留作历史对照；当前代码以 §12 为准。**

文件：`backend/app/harness/middleware/clarify_gate_middleware.py`
注册：`middleware/registry.py`，仅当 `clarify_gate_enabled`(默认开) + `mode∈{pro,ultra}` +
clarification 机制存在时挂载。只作用于**一次用户回合的首个 model step**。

- **澄清（pro + ultra）**：`before_model`（装饰 `@hook_config(can_jump_to=["end"])`）跑 triage；
  若 `actionable=false` 且本线程未澄清过，则返回
  `{"jump_to": "end", "messages": [AIMessage + clarification ToolMessage]}`。
  - clarification ToolMessage 由既有的 `build_clarification_payload` 生成，`source` 仍是
    `slotflow_clarification`，所以**投影层（`clarification_event_from_snapshot`）照常**把它变成
    `clarification.requested` 事件弹出选择框。
  - **模型完全不运行** → 无从脑补；`jump_to=end` 直接结束 → 没有第二次 model 调用。
- **技能/计划优先（仅 ultra）**：triage 判定 `actionable` 时把结果暂存进 `state.slotflow`，
  `wrap_model_call` 据此**注入一段强 system 指令**：
  - preflight 命中已装 Skill（`state.slotflow.skills_preflight.installed_matches` 非空）→
    「你的第一步必须调用 `skill_match`…」
  - 否则 `needs_plan=true` 且无 todos → 「你的第一步必须调用 `write_todos`…」
- **防循环**：本线程历史里已有 `ask_clarification` 的 ToolMessage 就不再发问。
- **fail-open**：triage 失败/异常一律放行，绝不因为门本身把正常 run 弄挂。

---

## 5. 真实 API 实测踩坑与修复（关键！勿回归）

> 用一个 in-process 探针（绕过前端，直接走 `build_agent_adapter().stream_events()`）打真实
> DeepSeek（`deepseek-v4-pro`，thinking 模式）实测。三个坑都是**纸面设计看不出来、只有真打才暴露**的，
> 全部来自「thinking 模式」这个主力 provider 的特性。

### 坑 1：triage 的输出污染了用户可见流
- **现象**：`{"actionable":...}` 这段 triage JSON 出现在用户看到的 `message.delta` 正文里。
- **原因**：triage 的 `model.ainvoke` 在 graph 执行期间发起，被 `astream_events` 当作本次 run 的
  一部分捕获，token 流被投影成 `message.delta`。
- **修复**：triage 调用加 `config={"callbacks": []}` 把它从父 run 的事件流里**摘出来**。

### 坑 2：DeepSeek thinking 模式拒绝强制 `tool_choice`
- **现象**：`400 "Thinking mode does not support this tool_choice"`。
- **影响**：原计划「ultra 强制 `tool_choice=skill_match / write_todos`」在主力 provider 上直接报错。
- **修复**：放弃强制 tool_choice，改成**注入强 system 指令**（provider 无关、仍是硬约束语气）。

### 坑 3：合成响应短路触发 reasoning_content 回传错误
- **现象**：`400 "The reasoning_content in the thinking mode must be passed back to the API."`
- **原因**：最初让 `wrap_model_call` 短路返回一个「合成的 ask_clarification AIMessage」。但它**没有**
  让 run 结束——graph 回灌模型再跑一次，历史里带着我这条「没有 reasoning_content 的 assistant 消息」，
  DeepSeek thinking 模式拒收。
- **修复**：澄清短路改走 `before_model` + `jump_to=end`（直接结束，无第二次 model 调用）；合成的
  AIMessage 兜底带 `reasoning_content=""` 以防被回传时校验失败。

### 坑 4：长 run 中途「Connection error」直接失败（前端实测发现）
- **现象**：ultra 长任务（多次 web_fetch + thinking，跑数分钟）前端报「Connection error」。
- **定位**：「Connection error.」正是 `openai.APIConnectionError` 的消息体。我们的 SSE 层处理是
  对的（异常被 `iter_business_events` 转成干净的 `run.error`），问题在**模型客户端零重试**——
  `runtime/models.py` 里 OpenAI 兼容 / Anthropic 客户端都是 `max_retries=0`，对 provider 的任何
  一次瞬时连接抖动/429/5xx 立刻整轮失败。
- **修复**：两个客户端 `max_retries: 0 → 2`（OpenAI SDK 自带指数退避，覆盖
  APIConnectionError/APITimeoutError/429/5xx）。长 run 容错显著提升。

> 另外踩到的两个**测试方法**坑（非 app bug，记录以免再犯）：
> - in-process 探针自己解析 `.env` 时没有去掉值两边的引号 → 误以为 `SLOTFLOW_MCP_CONFIG_JSON`
>   非法。真实 dotenv / `make dev` 会去引号。
> - 探针复用了固定 `thread_id`，SQLite checkpointer **跨次持久化**了上一次（失败）run 的消息，
>   污染了下一次。**实测一定要用全新 thread_id。**

---

## 6. 当前行为实测结果

每条 prompt 用全新 thread、真实 DeepSeek、走完整 adapter→graph→projection 路径。

| 内置能力 | 测试 prompt（mode） | 结果 | 说明 |
|---------|--------------------|------|------|
| **clarify** | 「帮我做个表格」(pro/ultra) | ✅ **可靠触发** | 弹出 `clarification.requested` + 合理选项，模型不脑补 |
| **memory** | 「请记住：我叫张伟…」(pro) | ✅ **触发** `memory_save` | 但这是**显式**「请记住」；**主动**记忆（无显式要求）未验证、大概率仍弱 |
| **todo** | 「实现计算器四函数+测试」(ultra) | ⚠️ **时有时无** | 大任务（三公司对比报告）会 `write_todos`(todo.updated=4)；中等任务（计算器）没写。受 triage `needs_plan` + 模型自由度影响 |
| **subagent** | 「分别调研特斯拉/比亚迪/小米再对比」(ultra) | ❌ **未使用** | 明显可并行，却自己顺序做完（用了 web_search/web_fetch/artifact_write，但**没用 `task_tool`**） |
| **skills** | 「专业分析这组销量并出图」(ultra) | ❌ **未自主发现** | 没有 `skill_match`/`find-skills`；ultra 技能指令只在「已装」Skill 时才推，**不会主动去搜** |

**一句话**：**clarify 与 memory(显式) 已达标**；**todo 半达标**；**subagent 与 skills 自主发现仍是真问题**
（与用户最初的抱怨一致）。

---

## 7. 仍存在的差距与方向

> **迭代 2（2026-06，feature/clarify-gate 续）**：已把 ultra 指令从「仅 plan」扩展到
> **skill 发现 + 计划 + subagent 委派**三合一（triage 增加 `needs_subagent`/`specialized`；
> skill 触发还兜底用「skills_preflight 是否跑过」这个更可靠的专业任务信号）。单测覆盖。
> **但实测暴露了硬限制**：

1. **subagent 强制委派**：实测「分别调研三家公司」这类任务，clarify-gate 会**先**判定欠规约
   去澄清（符合你「先假设但一定要问」的选择）；subagent 委派指令要到**澄清回答后的下一轮**
   （请求已明确）才注入。即：委派是「条件触发、可能延后一轮」，不是每次都立刻发生。
2. **skills 自主发现（点 2）—— 真正的硬骨头**：指令已注入（专业任务都会推 `skill_match` →
   `find-skills`/`search_skill_repos`，GitHub 已按 star 降序），**但指令是「软」的**：对模型自认
   能直接做的任务（如「分析这组销量并出图」），它仍会跳过 skill 发现直接 artifact_write。
   - 根因：DeepSeek thinking 模式**拒绝强制 `tool_choice`**，且「合成 skill_match 调用→执行→
     继续」会再次触发 reasoning_content 回传错误（见第 5 节坑 3）。所以**无法在 thinking 模式下
     确定性地强制工具调用**。
   - 可选路线（待定）：(a) 接受软指令（当前）；(b) 只在「确实装了相关 Skill」时才强推（高确定性、
     低召回）；(c) 为 skill 发现单独走一个 thinking-off 的子调用链;(d) 把候选 Skill 更醒目地塞进
     用户消息（preflight 已做，可再加强）。**这是产品权衡，需要拍板。**
3. **memory 主动性（点 1）**：仍只在显式「请记住」时存。方向：after-turn 抽取钩子 / mem0 类持久层。
   **尚未实现。**
4. **架构瘦身（点 3）**：**尚未开始。**
5. **clarify-gate 在 ultra 的取舍**：已按用户选择定为「先假设但一定要问」——保持 pro+ultra 都澄清,
   triage 产出「最佳猜测方向」作为选项,前端选择框自带自由文本兜底。

## 7.0 历史方向（迭代 1 原始差距，供对照）


---

## 8. 关键不变量（改动前必读，勿回归）

- triage 调用**必须** `config={"callbacks": []}`，否则污染用户流。
- **严禁**对 DeepSeek thinking 强制 `tool_choice`（会 400）；用 system 指令注入代替。
- 澄清短路**必须**走 `before_model` + `jump_to=end`，**不能**用 `wrap_model_call` 合成响应回灌模型。
- 合成 AIMessage 带 `reasoning_content=""` 兜底。
- 门只作用于**首个 model step**、单线程**不重复发问**、任何失败**fail-open**。
- 脚本化 fake-model 的 graph 执行测试必须 `SlotFlowMiddlewareConfig(clarify_gate_enabled=False)`
  （triage 会吃掉一条预设响应）。
- 详见 `AGENTS.md` 的「Clarify-gate」小节。

---

## 9. 如何复现实测（in-process 行为探针）

不依赖前端，直接驱动生产路径，逐能力 prompt-探测「agent 是否真的用了这个内置」：

```python
# 要点（throwaway 脚本，勿提交 / 勿上传远程）：
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime.adapter import build_agent_adapter

# 1) 手动 load .env（注意去掉值两边引号）
# 2) thread_id 每次用全新 nonce（避免 checkpointer 跨次污染）
# 3) request = ChatStreamRequest(message=..., mode="pro"/"ultra",
#                                model_name="deepseek-v4-pro", provider="deepseek")
# 4) bundle = build_run_config(thread_id=..., run_id=..., request=request)
# 5) async for ev in build_agent_adapter().stream_events(request=request, bundle=bundle): ...
#    统计 ev.event；tool.delta 的工具名在 data["tool_name"]；clarification.requested 看 data["question"]
```

判定标准：clarify 看有无 `clarification.requested`；todo 看 `todo.updated` 计数；
subagent 看 `tool_name` 是否含 `task`；memory 看是否含 `memory_save`；skills 看是否含
`skill_match`/`find`。

---

## 11. 迭代 3(2026-06-20):参考 DeerFlow 做减法 + 记忆主动化

### 背景结论(重要,纠正之前的假设)
深入读 DeerFlow(`/home/dell/code/deer-flow`)后确认:**DeerFlow 与 SlotFlow 是同一种架构**
——单个 LangChain `create_agent` ReAct 循环 + middleware,**不是**确定性 graph 节点/状态机。
所以「照 DeerFlow 改成图」是伪命题。真正差异只在三处,且都指向「更简单/更对」:
- 澄清:DeerFlow 只有一个机制(`ask_clarification` 工具 + `awrap_tool_call` 拦截 → `goto END`);
  SlotFlow 当时有两个(同样的拦截 + 377 行 ClarifyGate triage 门 + 软指令注入)。
- 记忆:DeerFlow `after_agent` → 去抖队列 → **后台 LLM 抽取**;SlotFlow 用手写中文正则抽取(脆)。
- 子代理:DeerFlow 有 `after_model` 并发上限守卫;SlotFlow 无。
- skills/MCP:SlotFlow 反而更强(自主 GitHub 发现)。DeerFlow 是预配置 + tool_search 延迟加载。

### 做了什么(四块,全部对齐 DeerFlow 的「简单」一侧)
1. **澄清瘦身** `clarify_gate_middleware.py` 377→~210 行:删 `wrap_model_call`/`_ultra_directive`
   及其 helper、triage stash、triage schema 里 needs_plan/needs_subagent/specialized 字段
   
   。
   **只保留**硬短路(triage 判不可做 → `before_model` + `jump_to=end` 直接弹澄清,模型不跑)。
   skill-first/plan/delegate 的引导**收敛到** `<slotflow-operating-procedure>` 提示词(本就在那、
   且对 DeepSeek thinking 本就是软的)。去掉了一个提示词冲突源。
2. **记忆主动化**:新增 `memory/extractor.py`(`SlotFlowMemoryExtractor`),`long_term_memory.py`
   的 `aafter_agent` 改为 **fire-and-forget 后台 LLM 抽取**(复用对话模型、`callbacks=[]`、延迟被
   隐藏因为不阻塞 run);删掉 preference/profile/topic 的正则分支,只保留 `请记住X` 同步快路径。
   记忆**本来就是跨对话全局的**(`store.search_memories` 用 `list_memories(limit=200)` 无 thread
   过滤,thread_id 只 +2 加分)——**不改作用域**,只换抽取方式。开关
   `proactive_memory_extraction_enabled`(脚本化 graph 测试需置 False)。
3. **子代理并发上限**:新增 `subagent_limit_middleware.py`(`after_model` 截断超额 `task_tool`
   到 `subagent_max_concurrent=3`,保留非 task 调用与 `reasoning_content`)。
4. **skills 发现**:`match_installed_skills` 加短 TTL 进程缓存(preflight 与 skill_match 同轮不重复
   扫盘;`skill_install` 调 `invalidate_skill_match_cache()`);`search_skill_repos` 结果新增
   `ecosystem_sources`(Anthropic/Codex/skills.sh)并在 docstring 说明 SKILL.md 是 Claude/Codex/
   Copilot 通用标准——**无需 Codex 专用工具**,同标准仓库装进来即可用。

### DeerFlow 参考实现位置(供以后查)
- 澄清:`backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py`(拦截→`Command(goto=END)`)。
- 记忆:`.../memory_middleware.py`(after_agent 入队)+ `agents/memory/{queue,updater}.py`(去抖 + LLM 抽取)。
- 子代理:`.../subagent_limit_middleware.py`(after_model 截断)+ `subagents/executor.py`(线程池)。

### 实测结果(真实 `deepseek-v4-pro`,scratch 脚本跑在 /tmp、已删、未提交)
- 澄清:`帮我分析一下这些数据`/`帮我做个网站` → 正确弹澄清(带最佳猜测选项 + 末尾自由输入框),模型不脑补;
  `把'你好世界'翻译成英文`、`一句话解释 TCP 三次握手` → **不**误触澄清,直接作答。决策质量两轮稳定。
- 记忆:`我是控制工程研究生,以后回答用中文且简洁` → 后台抽取保存(`extraction=llm`),且模型**自己也**调了
  `memory_save`(软提示在真实 DeepSeek 上生效);换一个全新 thread 检索能命中 → 跨对话主动记忆成立。
- 子代理/缓存:确定性,离线单测覆盖(`test_subagent_limit_middleware.py`、`test_harness_tools.py`)。

### 离线验收
`uv run ruff check app tests` 通过;`uv run pytest -q -k "not live"` **274 passed**。

### 不变量(沿用第 5/8 节,勿回归)
不强制 `tool_choice`;内部小调用(triage / memory 抽取)`config={"callbacks": []}`;澄清短路用
`before_model`+`jump_to=end`(合成 AIMessage 带 `reasoning_content=""`);模型客户端 `max_retries>=2`。

> ⚠️ 上一条「澄清短路用 `jump_to=end`」已被**迭代 4 取代**为 `interrupt()/resume`,见下节。

---

## 12. 迭代 4(2026-06-21):HITL 澄清改用 LangGraph 原生 `interrupt()/resume` + 完整 agent 链路

> 本节所有论断均**对照当前代码核实**(文件:行),并经**真实 `deepseek-v4-pro`(thinking)
> 实测**。给从 0 接手的人:读这一节即可理解当前 clarify 全貌与整条 agent 链路。

### 12.0 TL;DR
- 删掉 `SlotFlowClarificationMiddleware`(原 `clarification_middleware.py`,已删除)整个机制:
  `wrap_tool_call`(goto=END 回显)+ `wrap_model_call`(把已答澄清的工具结果重写成答案)。
- 澄清(工具路径 + 门路径)统一改为 **LangGraph 原生 `interrupt()`**;用户的回答通过
  `Command(resume=<答案>)` 注入,**天然成为工具结果 / 用户消息**,不再有「重写工具结果」这层 hack。
- `build_clarification_payload` 等纯函数从被删的中间件移到新模块 `app/harness/clarification.py`。
- 跨模型:`interrupt/resume` 是 graph 执行层机制,与 provider 无关(DeepSeek/OpenAI/Anthropic/中转站
  一视同仁),且**反而绕开** DeepSeek thinking 的 `reasoning_content` 回传坑(§5 坑 3)——因为模型发的是
  **真实**工具调用(带真 reasoning_content),graph 在工具执行处暂停,恢复后返回正常 ToolMessage。
- 顺手删了死脚手架工具 `slotflow_context_tool`(模块 11 时为证明「能绑工具」而留的 proof-of-concept,
  之前仍绑在主 agent + 每个子 agent;`builtins.py`/`tools/__init__.py`/`tools/registry.py` 已清理,
  相关测试改用 `ask_clarification`/自定义 echo 工具)。

### 12.1 「回答后澄清又自己弹出来」的真根因(代码核实)
用户多次报告:回答澄清后,模型给了完整回答,**同一个澄清又弹一次**。逐层查代码定位到**后端**
(前端只是忠实渲染后端事件),根因是**每个后续回合都从历史里重新派生澄清事件**:
1. `streaming.py` 旧逻辑对**每个** `values` 快照算 `clarification_event_from_snapshot(...)`,
   并在 run 末尾 `yield`。
2. `projections.py::clarification_event_from_snapshot` 逆序扫消息,命中**第一条**内容仍是澄清
   payload(`type=="clarification"`)的 `ask_clarification` ToolMessage 就发 `clarification.requested`。
3. 回答回合的 graph 状态(由 checkpointer 重建)里**仍留着上一回合那条澄清 ToolMessage**,其
   持久化内容**还是问题 payload**——因为旧的 `wrap_model_call` 重写只改「喂给模型的那次请求」,
   **从不改持久化状态**。
4. ⇒ 扫到旧 payload ⇒ 再发 `clarification.requested` ⇒ 前端再弹一次。**这就是 re-pop**。

这是典型「治表象」陷阱:之前加过「相似度抑制 / 一线程只澄清一次」都是补丁;真因是
**澄清事件该只在「图当前真的停在某个澄清上」时出现,而不是从历史里反复扒**。

### 12.2 迭代 4 的修法(代码核实)
- **工具路径** `app/harness/tools/builtins.py::ask_clarification_tool`:工具体内
  `answer = interrupt(build_clarification_payload(...))`,`return f"用户对该澄清问题的回答是:{...}"`。
  工具只调 `interrupt` 再返回,无其它副作用 → resume 重放安全。
- **门路径** `app/harness/middleware/clarify_gate_middleware.py::_clarify_via_interrupt`:triage 判不可做 →
  `answer = interrupt(payload)`;**resume 后把答案原样作为一条 `HumanMessage` 注入**
  (`{"messages":[HumanMessage(content=clarification_answer_text(answer))]}`),模型据此继续。
  - `abefore_model` 用 `except GraphBubbleUp: raise` **在** fail-open 的 `except Exception` 之前,
    否则 `interrupt()` 抛的 `GraphInterrupt`(是 `Exception` 子类)会被吞掉 → 暂停失效。已核实
    `GraphInterrupt` MRO:`GraphInterrupt → GraphBubbleUp → Exception`。
  - **代价(已知、良性)**:resume 时整个 `before_model` 节点**从头重放**,triage(`callbacks=[]`)会
    再跑一次;若它偶发翻成 actionable 而不再调 `interrupt`,resume 值会被**静默丢弃**(实测不崩,
    见 /tmp 探针),仅该次回答未进模型上下文。门内除 triage 外无副作用,可接受。
- **事件来源换成「待处理 interrupt」** `app/chat/agent_adapter/streaming.py`:
  - 进入回合先 `_pending_interrupt(graph, config)`(读 `graph.aget_state(config).interrupts`,
    无 `aget_state` 的测试桩降级为 None);**有待处理 interrupt ⇒ 这条用户消息就是答案**,用
    `Command(resume=request.message)` 恢复;否则 `build_agent_input(...)` 正常开新回合。
  - run 结束后 `_clarification_from_pending_interrupt(...)`:**仅当图现在真的停在一个 interrupt 上**
    才发 `clarification.requested`(payload = interrupt 的 value)。已答的澄清不留 pending interrupt ⇒
    **结构上不可能 re-pop**。`projections.py::clarification_event_from_interrupt` 取代了原扫历史函数。
  - `iter_projection_agent_events` 删掉了 `latest_clarification` 那条扫快照的支路。
- **注册/配置**:`middleware/registry.py` 去掉 `SlotFlowClarificationMiddleware`,门不再依赖它
  (改注释);删 `config.py::clarification_enabled` 与 env `SLOTFLOW_CLARIFICATION_MIDDLEWARE`。
- **前端零改动**:`chat-app.tsx::handleSelectClarification` 仍把选择当普通消息 `submitMessage` 发出;
  后端按「是否有待处理 interrupt」决定 resume 还是开新回合,前端无感知。

### 12.3 实测又抓出一个真 bug:门注入的答案被模型「回显」(已修)
首轮 live 跑 `介绍一个东西` → 答 `计算机深度学习` 后,助手回答**开头多了一段**
`针对澄清问题「…」，用户的回答是：…`。用 fake 模型探针(`/tmp/diag_stream2.py`)证明:**注入的
HumanMessage 不会被 messages 投影流式输出**——所以那段是**真实模型把我注入的「元包装」文案照抄**了。
修法:门注入时**不要加元包装**,直接注入用户原话(`HumanMessage(content=clarification_answer_text(answer))`),
和「用户直接发了这句」完全一致。再次 live 验证:元包装消失,**无 re-pop**,回答正确。
(模型可能仍轻度复述用户选择,属正常对话行为,非 bug。)

### 12.4 真实 API 实测结果(`deepseek-v4-pro` thinking,mode=ultra,走生产 `build_agent_adapter`)
脚本 `/tmp/live_clarify_test.py`(throwaway,内存 checkpointer 不碰真库;**不提交、不上传远程**):
- 回合 1 `介绍一个东西`:门 triage 判不可做 → `interrupt` → 事件
  `[run.prepared, state.snapshot, clarification.requested, run.finished]`,问题
  「你想让我介绍什么?」+ 4 个最佳猜测选项 + `其他（自己输入）`。
- 回合 2 `我选择 计算机深度学习`:检测到待处理 interrupt → `Command(resume=...)` → 流式输出完整
  深度学习介绍;**`clarification.requested` = False(无 re-pop)**。VERDICT: PASS。
- 用户也在真实 UI 上确认「bug 没了」。

### 12.5 完整 agent 链路(端到端,代码核实)
一次 `POST /api/chat/threads/{id}/runs/stream`(`chat/routes.py::stream_thread_run`)的全过程:

1. **接入/落库**:校验 thread、上传文件;存用户消息;`repo.create_run`;`build_run_config` 出
   `bundle`(`config={"configurable":{"thread_id"}}` + `RunContext`);`adapter.stream_events`。
2. **模型选择** `chat/runtime/models.py::create_chat_model`:provider 优先级 = 显式 > `run_context.
   model_provider` > 按 id 前缀推断(`claude-`→anthropic、`gpt-`/`o`→openai、否则 deepseek)。
   deepseek/custom 走**带 reasoning 桥接的 ChatOpenAI 子类**;DeepSeek v4 **thinking 默认开**,开关
   必须显式经 `extra_body={"thinking":{"type":"enabled"/"disabled"}}` 下发(漏传≠关)。`max_retries>=2`。
3. **graph 组装** `harness/builder.py::build_slotflow_harness_graph`:`create_agent`(单 ReAct 循环)
   + 工具(`tools/registry.py`)+ 中间件(`middleware/registry.py`)+ system prompt + sqlite checkpointer。
   无 `bind_tools` 能力的模型不挂工具(降级)。
4. **中间件链(生产 ultra + 有 memory_store 时的顺序,registry.py 核实)**:
   `Dangling(修悬空工具调用)` → `ToolSafety` → `Summarization(超阈值压上下文)` →
   `LongTermMemory(before_agent 检索注入 + aafter_agent 后台抽取)` → `SkillsPreflight(命中已装 Skill
   注入候选)` → `Uploads(把上传文件路径写进最新用户消息)` → `ClarifyGate(pro/ultra:首步 triage,不可做→
   interrupt 澄清)` → `Todo(plan)` → `SubagentLimit(subagent:截断超额 task_tool 到 3)` →
   `ArtifactDiscovery(收集本 run 产物)` → `RuntimeSummary`。
5. **思考/澄清/工具循环**:模型在 thinking 通道出 reasoning(投影按通道分流,见 `projections.py`),
   正文走 content 通道;要澄清就调 `ask_clarification`(或被门拦在首步)→ `interrupt` 暂停。
6. **skills/MCP 发现**(提示词 `<slotflow-extension-tools>` + preflight 引导):`skill_match`(先查已装,
   短 TTL 缓存)→ `find-skills`(注册表)→ `search_skill_repos`(GitHub,含 `ecosystem_sources`)→
   `skill_install`;MCP 用 `mcp_add_http` 等;按**能力/任务类型英文检索**,非字面主题词。
7. **子代理**:独立子任务 `task_tool` 并行,`SubagentLimit` 兜底并发上限;主 agent 汇总。
8. **产物**:用户可见交付物必须 `artifact_write`(唯一进产物面板的途径)。
9. **记忆**:`长期记忆`跨对话全局(`store.search_memories` 无 thread 过滤);回合末 fire-and-forget
   LLM 抽取持久事实(`memory/extractor.py`,`callbacks=[]`),`请记住X` 走同步快路径。
10. **流式投影/收尾** `streaming.py` + `routes.py`:v3 projections(messages/values/tool_calls)→
    `AgentEvent`(message.delta / tool.delta / todo.updated / state.snapshot);run 末按「待处理 interrupt」
    决定是否发 `clarification.requested`;`run.finished` 落库 assistant 消息或澄清消息、更新 run 状态。

### 12.6 更新后的不变量(勿回归)
- 不强制 `tool_choice`;内部小调用 `config={"callbacks": []}`;模型客户端 `max_retries>=2`(§5/§8 仍有效)。
- **HITL 澄清 = `interrupt()/resume`**:工具/门**只调 `interrupt` + 无其它副作用**(resume 会重放节点);
  门的 `abefore_model` 必须 `except GraphBubbleUp: raise` 在 fail-open catch 之前。
- 澄清事件**只能**来自「当前待处理 interrupt」(`graph.aget_state().interrupts`),**禁止**再从消息历史
  扫 `ask_clarification` ToolMessage 派生——那正是 re-pop 的根因。
- 门注入答案用**用户原话**的 `HumanMessage`,不要加「针对澄清问题…用户的回答是…」之类元包装(会被模型回显)。

### 12.7 离线验收
`uv run ruff check app tests` 通过;`uv run pytest -q -k "not live"` **274 passed**。新增/改动测试:
`test_harness_tools.py`(interrupt/resume 端到端 + 答后无 pending interrupt)、
`test_agent_adapter.py`(adapter 两回合:答后不 re-pop)、`test_clarify_gate_middleware.py`(门 interrupt 端到端)、
`test_harness_middleware.py`/`test_harness_builder.py`/`test_tool_registry.py`(去掉已删中间件/工具)。


---

## 13. 迭代 5(2026-06-30):`create_agent`+middleware → LangGraph node+edge graph

> 本节所有论断均对照当前代码核实(文件:模块),离线 `pytest -q -k "not live"` **273 passed**、
> `ruff check app tests` 通过。live 验证待阶段 F。给从 0 接手的人:读 §12 + 本节即可理解当前 harness。

### 13.0 TL;DR
- 把 LangChain `create_agent` 单 ReAct 循环 + 11 个 `AgentMiddleware` 整体迁移到 LangGraph 原生
  `StateGraph`（显式 node + edge）。`harness/builder.py` 不再调 `create_agent`，改调
  `harness/graph.py::build_slotflow_graph`。`AgentMiddleware` 子类与 `build_harness_middleware` 注册表
  全部删除；只保留 `SlotFlowMiddlewareConfig`（行为开关，被图节点消费）。
- 每个原中间件的可复用逻辑抽成 `harness/steps/*` 无状态纯函数，由具名节点直接调用，顺序由边显式
  保证（不再依赖 registry append 顺序）。
- HITL、记忆、子代理上限、reasoning 投影等不变量全部沿用 §12.6；SSE 事件契约与前端零改动。

### 13.1 为什么迁（根因，非跟风）
- §11 已确认 DeerFlow 也是 `create_agent`+middleware，「照 DeerFlow 改成图」是伪命题。但 SlotFlow 自己
  的诉求是：**链路严格按规定的路径运行 + 可可视化 + HITL/多 agent 协作有更清晰的扩展位**。middleware
  的 hook 桶 + registry append 顺序让流程藏在隐式约定里；`interrupt()` 靠 `abefore_model` 里
  `except GraphBubbleUp: raise` 在 fail-open 之前这种易错顺序。节点化后这些变成显式拓扑与线性节点。

### 13.2 目标拓扑（代码核实：`harness/graph.py::build_slotflow_graph`）
```
START → prepare → triage_gate → pre_model → SlotFlowSummarizationMiddleware → agent → post_model → route
                                                                                              ├─ tools → pre_model
                                                                                              ├─ pre_model（todo enforcement retry）
                                                                                              └─ finalize → END
```
节点职责（对应 steps 模块）：
- `prepare`（每回合一次）：`runtime_summary` / `uploads` / `skills_preflight` / 记忆检索(`long_term_memory.retrieve_memories`) / 产物基线(`artifact_discovery.artifact_baseline`)。
- `triage_gate`（仅首步，pro/ultra）：`clarify_gate.run_triage` → 不可做则 `clarify_via_interrupt`（`interrupt`+答案 `HumanMessage`）。
- `pre_model`（每步）：动态 `todo_reminder_update` / `repair_dangling_tool_calls` / skills preflight system 注入(`format_preflight`) / 记忆 system 注入(`append_memory_system_message`)。
- `SlotFlowSummarizationMiddleware`（独立节点，名字固定）：复用官方 `SummarizationMiddleware.abefore_model` 的 `RemoveMessage`+`lc_source` 逻辑。
- `agent`：`model.bind_tools(tools)` 调用，读 `state.llm_input_messages` + `state.system_prompt`。
- `post_model`：`todo_parallel_call_guard` + `todo_enforcement_update`，再由 `subagent_limit.cap_subagent_calls` 截断超额 `task_tool`。
- `route`：todo enforcer 控制消息 → `pre_model`；否则官方 `tools_condition` → `tools` / `finalize`。
- `tools`：官方 `ToolNode` + SlotFlow `tool_safety` wrapper（`wrap_tool_call`/`awrap_tool_call` 注入，error ToolMessage 格式不变）。
- `finalize`（每回合一次）：`artifact_finalize_update` / `explicit_save_update` / `maybe_schedule_extraction`（后台 LLM 抽取）。

state schema（`harness/state.py::SlotFlowAgentState`）新增节点间显式通道：
`llm_input_messages` / `system_prompt` / `retrieved_memories` / `artifacts_baseline`（替代隐式临时键）。
`messages`/`slotflow`/`todos` 不变。

### 13.3 关键设计点与踩坑（代码核实）
1. **summarization 必须是独立节点（根因级修复，非补丁）**：若把 summarization 埋在 `pre_model` 节点内，
   其内部 summary model call 会以 `pre_model` 节点名 stream；投影层 `is_summarization_node_name` 匹配
   `"SummarizationMiddleware"` 子串，识别不到 → 摘要文本泄漏进 `message.delta`（与 §12.3 模型回显同源：
   内部小调用与主链路共享事件流）。给 summarization 一个节点名固定为 `SlotFlowSummarizationMiddleware`
   的独立节点，投影层按节点名过滤其内部 stream，`context.compressing` 仍正常触发。**这是把根因（节点
   归属）修对，而不是在投影层加更多特判。**
2. **复用官方实现，不重写**：`ToolNode` / `tools_condition` / `SummarizationMiddleware`（含
   `RemoveMessage`+`lc_source` 标签）直接复用；`Send` 留给后续主图并行分支。
3. **agent 节点用 `RunnableCallable(func, afunc)` 包装**：`add_node` 不接受 `(sync, async)` tuple；
   `RunnableCallable` 同时承载两态。节点 `config` 参数须类型为 `RunnableConfig | None`（`from __future__
   import annotations` 会字符串化注解，触发 LangGraph 表层 UserWarning，行为无影响）。
4. **HITL 两条路径不变**：自愿工具 `ask_clarification` 在 `tools` 节点 `interrupt`；强制门在 `triage_gate`
   节点 `interrupt`。resume 检测仍由 `streaming.py:: _pending_interrupt` 读 `graph.aget_state().interrupts`，
   前端零改动。澄清事件仍只来自 pending interrupt（re-popup 根因修复保留）。
5. **`triage_gate` / `finalize` 只在首步/末步有效**：靠拓扑天然保证（`triage_gate` 内还判 fresh user turn +
  已澄清防循环；`finalize` 不在循环边上）。`prepare`/`finalize` 每回合一次 likewise 由拓扑保证。

### 13.4 测试形态迁移
- 中间件单测整体迁移到 `tests/test_harness_steps.py`（19 用例覆盖全部 steps 纯函数）。
- `test_harness_middleware.py` → `test_harness_graph_integration.py`（只留两个 graph 级集成测试）。
- `test_clarify_gate_middleware.py` → `test_clarify_gate.py`（steps + `build_slotflow_harness_graph` 端到端
  interrupt/resume，monkeypatch `run_triage` 强制 non-actionable）。
- `test_subagent_limit_middleware.py` → `test_subagent_limit.py`（`cap_subagent_calls` step）。
- `test_harness_memory.py` 中间件单测改为 `explicit_save_update`/`append_memory_system_message`/
  `retrieve_memories`/`aextract_and_save`/`build_memory_tools` 等 steps 测试。
- `test_agent_adapter.py` 的 summarization 过滤测试改为 node graph（3 条 response：turn1 回答、summary 文本、
  turn2 最终回答），验证 `context.compressing` 触发且 summary 不泄漏、最终答复正常流出。
- `test_harness_builder/skills/sandbox` 改为 monkeypatch `build_slotflow_graph` 并断言 `config_flags`
  （node+edge 行为开关）而非 middleware 名称列表。

### 13.5 更新后的不变量（勿回归，§12.6 仍有效，本节补充）
- 不强制 `tool_choice`；内部小调用 `config={"callbacks": []}`；模型客户端 `max_retries>=2`。
- **summarization 必须是节点名含 `SlotFlowSummarizationMiddleware` 的独立节点**，不能内联进 `pre_model`
  （否则摘要 stream 泄漏）。投影层 `is_summarization_node_name` 靠节点名过滤。
- **`interrupt()` 必须在节点函数体内直接调用**，不能被 `except Exception` 吞掉（`GraphInterrupt` 是
  `Exception` 子类）。`triage_gate` 节点沿用「让 `GraphBubbleUp` 先抛」的写法。
- 澄清事件只能来自 pending interrupt；门注入答案用用户原话 `HumanMessage`，不加元包装。
- state 节点间通道（`llm_input_messages`/`system_prompt`/`retrieved_memories`/`artifacts_baseline`）显式
  声明在 `SlotFlowAgentState`，不要再用 `__`-前缀临时键塞 state。
- 记忆层本次**未改**（仍 `harness/memory/store.py` 手写层）；mem0 重写是后续独立阶段（roadmap #1）。

### 13.6 离线验收
`uv run ruff check app tests` 通过；`uv run pytest -q -k "not live"` **273 passed**（测试净减 1，因合并了
重复的 async memory 注入用例；中间件单测迁移到 steps 测试后用例更聚焦）。

### 13.7 live 验证（阶段 F，真实 `deepseek-v4-pro` thinking，生产 `build_agent_adapter`）
throwaway 探针 `/tmp/live_probe.py`（已删、未提交）跑四项：
- **clarify**（pro「帮我做个表格」）：✅ 触发 `clarification.requested`，问题「您想制作什么内容的
  表格？」+ 4 个最佳猜测选项 + 末尾自由输入，模型不脑补（`run.prepared→state.snapshot→
  clarification.requested→run.finished`，无 message.delta）。
- **memory**（pro「请记住：我叫张伟…控制工程研究生」）：✅ 流式完整回答（数百条 message.delta），
  无报错、无 re-pop。
- **todo**（ultra「实现计算器四则运算+测试」）：gate 先判定欠规约 → 触发澄清（带计算器形态选项），
  符合「先问再做」设计；这是 gate 的正确行为，非回归。
- **subagent**（ultra「三公司对比」）：模型判断为简单事实问题，自述用 web_search 直接答，未触发
  task_tool——与 §6 记录的 subagent 自主委派仍是软行为一致（roadmap #2 主图并行分支后续做）。

**live 探针抓到一个真 bug 并已修**：`_slotflow_async_tool_safety_wrapper` 原为「返回协程的同步函数」，
ToolNode 的 `_arun_one` 会 `await self._awrap_tool_call(...)` → 把协程当 awaitable await 出
`"ToolMessage object can't be awaited"`（工具实际成功但 await 报错）。offline fake-model 测试不触发
真实工具执行路径未暴露；改为 `async def` 后 live 通过。这正印证「live 探针不可省」（§5 同源教训）。

### 13.7 迭代 6 补记（2026-06-30 续）：思考流原生 projection 死锁教训
尝试用 langgraph v3 `AsyncChatModelStream.reasoning`/`.text` 原生 typed projection 直接顺序
消费、替代手写队列交错，**实测死锁真实图**。根因：v3 projection channel 是**单消费者**
（`StreamChannel.__aiter__` 只能调一次，第二次 raise）+ **caller-driven pump**
（`_arequest_more` 驱动共享 graph pump，单 flight）。顺序 drain `.reasoning` 再 `.text` 时，
pump 被 reasoning 独占，text 数据到了也推不进，死锁。现有「并发 pump 两个 channel 进 queue
再交错输出」是绕开单消费者限制的**必要**做法，非冗余兼容代码。结论：保留现状；思考块延迟感
是 v3 单消费者约束下的必要缓冲代价，不能用「换原生 API」简单消除。todo 丢失根因与子代理
统一见 docs/refactor-plan.md §13。

---

## 14. 迭代 7（2026-06-30 续）：第三方中转站「能显示但用不了」——OpenAI SDK UA 指纹被 WAF 拦截

### 14.0 TL;DR
用户把 `https://metapi.lilililwan.xyz/v1` 配进 `.env` 的 `CUSTOM_BASE_URL`/`CUSTOM_API_KEY`，选择器里
`Custom ·` 下所有模型都**能显示**，但只有 deepseek 系列能用、其它（glm/kimi/qwen/minimax）一律 403
`Your request was blocked.`。根因不是 ChatDeepSeek、也不是模型，而是**第三方中转站前置的 Cloudflare WAF
按 OpenAI SDK 的 `User-Agent: AsyncOpenAI/Python <ver>` 指纹拦截**。发现探针用裸 httpx（中性 UA）能过 →
模型"显示"；真正跑对话的 LangChain/OpenAI SDK 客户端用被拦 UA → "用不了"。修法：`custom` 中转站路径
注入中性 UA（`SlotFlow/1.0`，可 `SLOTFLOW_RELAY_USER_AGENT` 覆盖），且**发现探针与 runtime 用同一个 UA**，
让"选择器里能显示 == 实际能调用"。

### 14.1 复现与逐变量定位（live，throwaway 脚本跑在 /tmp、未提交）
relay `/models` 列出 10 个模型（deepseek-v4-flash/pro、glm-5.1/5.2、kimi-k2.6/k2.7-code、qwen3.6-plus/
qwen3.7-max、MiniMax-M2.7/M3）。用 SlotFlow runtime 逐个 `astream`：

- `glm-5.2`/`kimi-k2.6`/`MiniMax-M3`/`qwen3.6-plus` → `PermissionDeniedError Your request was blocked.`（403）
- `deepseek-v4-pro`（custom provider）→ **同样 403**（说明不是模型特定）

抓 `httpx.AsyncClient.send` 实际发出的请求，发现 UA = `AsyncOpenAI/Python 2.40.0`、body = 标准 chat
completion。然后用 curl 逐变量对照（同一 relay、同一 key、同一 body，只换 header）：

| 发送的 User-Agent | 结果 |
|---|---|
| `AsyncOpenAI/Python 2.40.0`（+ 任意 x-stainless-\* 组合、流/非流、有/无 max_tokens） | **403** "Your request was blocked." |
| `python-httpx/0.28.1`（+ 全套 x-stainless-\* 保留） | **200** |
| `curl/8.5.0`（+ 全套 x-stainless-\* 保留） | **200** |
| `SlotFlow/1.0` | **200** |
| `SlotFlow-Relay/1.0 (local agent runtime)` | **200** |
| 空 UA | **200** |

**结论**：WAF 是对 OpenAI SDK 指纹 UA 的**黑名单**（任何非 `AsyncOpenAI/Python` 的 UA 都放行），不是白名单；
`x-stainless-*` 一系列 header 留不留都不影响（留着全套、只改 UA，照样 200）。决定 200/403 的**唯一**因素是
`User-Agent`。

### 14.2 "为什么只有 deepseek 系列能用"的真相（排除 ChatDeepSeek 嫌疑）
用户原猜想是 `ChatDeepSeek` 这个 API 的问题、其它应该走 langgraph 配的 openai api。**验证后不成立**：
用纯 `ChatOpenAI`（非 deepseek 类）打同一 relay，`glm-5.2` 和 `deepseek-v4-pro` **都 403**——因为
`ChatDeepSeek` 与 `ChatOpenAI` 底层**共用同一个 `openai.AsyncOpenAI` 客户端**，都注入被拦 UA。所以换类治不了。

---

## 15. 迭代 8（2026-07-02）：代码优化与简化——消除冗余、统一逻辑

### 15.0 TL;DR
全面诊断重构后的项目，发现并修复了多处冗余和可优化的地方：
1. **前端 todo 更新冗余**：`todo.updated` 和 `state.snapshot` 两个路径都在处理 todos 更新
2. **前端 todo 解析冗余验证**：后端已完整验证，前端不需要再次验证
3. **消息规范化逻辑分散**：多处重复实现消息规范化逻辑

### 15.1 问题诊断结果

#### Todo 功能状态 ✅
当时的全面检查不完整，后续 §20-§24 已推翻其中几条结论：
- 后端：`write_todos_tool` 现在在所有模式注册，模式不再决定工具是否存在。
- SSE 事件：`todo.updated` 仍从 `values` projection 的 snapshot 中提取，但 §24 起后端不再按 signature 去重；
  每个带 todos 的 values snapshot 都会输出一次，前端负责 UI 级签名去重。
- 前端：`ComposerTodoPanel` 是唯一 todo 面板，包含展开/折叠、进度显示等功能。

**注意**：不能再把"todo 不显示"默认归因于 Flash；Flash 也有 `write_todos` 工具。应检查工具注册、后置
enforcer、SSE `todo.updated`、前端签名/revision 和真实 checkpoint payload。

#### 前端冗余问题 ⚠️
**问题**：`frontend/src/hooks/use-chat-stream.ts` 中存在两个 todo 更新路径：
```typescript
// 路径 1: 专用事件
if (streamEvent.event === "todo.updated") {
  replaceTodos(parseTodos(streamEvent.data.todos));
}

// 路径 2: 状态快照中也处理（冗余）
if (streamEvent.event === "state.snapshot") {
  const nextTodos = latestTodos(streamEvent);
  if (nextTodos) {
    replaceTodos(nextTodos);  // 冗余！
  }
}
```

**当时修复**：移除 `state.snapshot` 中的 todos 处理（L446-449），只保留 `todo.updated` 专用事件。当时后端在
`streaming.py` 中通过 signature 去重；§24 已取消后端去重，改为每个带 todos 的 values snapshot 都输出
`todo.updated`，前端继续负责 UI 级签名去重。

#### 前端 todo 解析过度验证 ⚠️
**问题**：`frontend/src/hooks/use-chat-stream-helpers.ts:132-157` 的 `parseTodos` 函数做了完整的类型检查和验证：
```typescript
return value.flatMap((item) => {
  if (
    typeof item === "object" &&
    item !== null &&
    "content" in item &&
    "status" in item &&
    typeof item.content === "string"
  ) {
    const status = parseTodoStatus(item.status);
    return [{ content: item.content, status }];
  }
  return [];
});
```

**修复**：简化为直接信任后端数据：
```typescript
export function parseTodos(value: unknown): ChatTodo[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value as ChatTodo[];
}
```

后端已在 `projections.py:305-322` 的 `normalize_todos` 中完整验证，前端不需要重复。

#### 后端消息规范化逻辑分散 ⚠️
**问题**：消息规范化逻辑在多个文件中重复实现：
- `backend/app/chat/agent_adapter/projections.py:348-423`：完整实现
- `backend/app/chat/repository.py`：可能也有类似逻辑

**修复**：抽取为共享模块 `backend/app/chat/message_utils.py`，包含：
- `normalize_messages(messages)` - 列表规范化
- `normalize_message(message)` - 单条消息规范化
- `normalize_message_content(content)` - 内容规范化
- `extract_reasoning_text(message)` - 推理内容提取

其他模块（如 `projections.py`）保留自己的实现（因为有更多 LangGraph 特定的逻辑），但新代码应该使用 `message_utils`。

### 15.2 测试验证

所有修改通过测试：
- **后端测试**：278 passed, 1 skipped（278/280 通过率）
- **前端构建**：✓ Compiled successfully
- **Todo 功能测试**：全部通过
  - `test_todos_in_values_snapshot_become_todo_updated_event` ✓
  - `test_todo_reminder_reminds_when_todos_leave_context` ✓
  - `test_write_todos_tool_is_registered_with_command_return` ✓
  - `test_todo_parallel_call_guard_rejects_multiple_calls` ✓
  - `test_todo_parallel_call_guard_allows_single_call` ✓

### 15.3 修改文件清单

#### 前端
1. `frontend/src/hooks/use-chat-stream.ts` (-4 行)
   - 移除 `state.snapshot` 中的冗余 todos 处理（L446-449）

2. `frontend/src/hooks/use-chat-stream-helpers.ts` (-19 行)
   - 简化 `parseTodos()` 函数，移除冗余验证
   - 删除 `parseTodoStatus()` helper 函数

#### 后端
3. `backend/app/chat/message_utils.py` (+104 行，新建)
   - 统一的消息规范化工具模块
   - 可被其他模块复用，避免重复实现

### 15.4 性能与可维护性提升

**优化效果**：
- ✅ 前端 todo 更新逻辑从 2 个路径简化为 1 个
- ✅ 前端 todo 解析去除冗余验证，代码量减少 ~60%
- ✅ 后端消息规范化逻辑可复用，避免未来重复实现
- ✅ 所有测试通过，功能完整性得到保证

**代码质量**：
- 减少重复代码 ~30 行
- 统一数据验证职责（后端验证，前端信任）
- 更清晰的关注点分离

---

## 16. LiteLLM 迁移可行性分析（2026-07-02）

### 16.0 背景与动机
用户建议将项目的 model 统一换成 [LiteLLM](https://github.com/BerriAI/litellm)。LiteLLM 是一个统一的 LLM API 代理层，支持 100+ 模型提供商（OpenAI、Anthropic、DeepSeek、Azure、Vertex AI 等），提供统一的 OpenAI 兼容接口。

### 16.1 当前架构分析

**现有技术栈**：
```python
# backend/pyproject.toml
langchain>=1.3.2
langchain-anthropic>=1.1.0
langchain-deepseek>=1.1.0
langchain-mcp-adapters>=0.2.2
langchain-openai>=1.2.2
langgraph>=1.2.2
```

**现有模型适配层**：
- `app/chat/runtime/models.py`：
  - `create_anthropic_chat_model()` → `ChatAnthropic`
  - `create_openai_compatible_chat_model()` → `ChatOpenAI` / `ChatDeepSeek`
  - `build_openai_compatible_model_kwargs()` - 统一的 kwargs 构建
- `app/chat/model_catalog.py`：模型发现、探测、目录管理

**LangGraph 集成深度**：
- ✅ LangGraph 原生支持 LiteLLM：[文档](https://langchain-ai.github.io/langgraph/how-tos/model-providers/#litellm)
- ✅ 通过 `langchain-openai` 的 `ChatOpenAI` 类与 LiteLLM proxy 集成
- ✅ 支持所有 LangGraph 特性（streaming、checkpointing、interrupt/resume）

### 16.2 迁移方案

#### 方案 A：部分迁移（推荐）
**思路**：保留现有 LangChain 集成，将 LiteLLM 作为**可选的统一代理层**。

**架构**：
```
SlotFlow
├── 直接路径（现状保留）
│   ├── ChatAnthropic → api.anthropic.com
│   ├── ChatDeepSeek → api.deepseek.com
│   └── ChatOpenAI → api.openai.com
│
└── LiteLLM 路径（新增）
    └── ChatOpenAI(base_url="http://localhost:4000") → LiteLLM Proxy
        └── 路由到 100+ 提供商
```

**实现**：
1. 添加 `litellm[proxy]` 依赖（可选）
2. 新增环境变量：
   ```bash
   LITELLM_ENABLED=false  # 默认关闭
   LITELLM_BASE_URL=http://localhost:4000
   LITELLM_API_KEY=sk-litellm-...
   ```
3. 在 `model_catalog.py` 中添加 `litellm` provider：
   ```python
   if os.getenv("LITELLM_ENABLED") == "true":
       providers.append("litellm")
   ```
4. 在 `create_openai_compatible_chat_model` 中处理 `litellm` provider

**优势**：
- ✅ **零破坏性**：现有代码完全不受影响
- ✅ **渐进式**：用户可以逐步迁移部分模型到 LiteLLM
- ✅ **灵活性**：可以根据需求选择直连或通过 LiteLLM
- ✅ **测试简单**：现有测试无需修改

**劣势**：
- ⚠️ 需要额外运行 LiteLLM proxy 进程
- ⚠️ 增加一层代理（轻微性能开销）

#### 方案 B：完全迁移（激进）
**思路**：移除所有 provider-specific 包，统一使用 LiteLLM。

**架构**：
```python
# 移除
- langchain-anthropic
- langchain-deepseek

# 保留
langchain-openai  # 作为 LiteLLM 的客户端
litellm[proxy]

# 所有模型通过 LiteLLM
ChatOpenAI(base_url=LITELLM_BASE_URL, model="claude-3-5-sonnet")
ChatOpenAI(base_url=LITELLM_BASE_URL, model="deepseek-v4-pro")
```

**优势**：
- ✅ **统一代码路径**：一个 `ChatOpenAI` 处理所有模型
- ✅ **依赖更少**：减少 provider-specific 包
- ✅ **统一监控**：LiteLLM 提供统一的日志、成本追踪

**劣势**：
- ❌ **破坏性变更**：需要重写模型创建逻辑
- ❌ **必须依赖 LiteLLM**：无法直连官方 API
- ❌ **reasoning 模式风险**：LiteLLM 对 DeepSeek reasoning 的支持需要验证
- ❌ **测试工作量大**：所有 provider 测试需要重写

### 16.3 关键考量点

#### 1. Reasoning 模式支持 ⚠️
**现状**：SlotFlow 深度依赖 DeepSeek 的 reasoning 模式：
- `app/chat/runtime/models.py:87-96`：reasoning 检测与注入
- `app/chat/agent_adapter/projections.py`：reasoning content 提取

**LiteLLM 支持情况**：
- ✅ LiteLLM 支持 DeepSeek reasoning：[文档](https://docs.litellm.ai/docs/providers/deepseek#reasoning-models)
- ✅ 通过 `reasoning_effort` 参数控制
- ⚠️ 但需要验证 `langchain-openai` → LiteLLM 的 reasoning 透传是否完整

**风险**：如果 LiteLLM 的 reasoning 透传有问题，会导致核心功能失效。

#### 2. Custom Relay UA 问题
**现状**：我们刚刚修复了第三方 relay 的 WAF UA 拦截问题（§14）：
```python
# app/chat/runtime/models.py
if provider == "custom":
    kwargs["default_headers"] = {"User-Agent": RELAY_USER_AGENT}
```

**LiteLLM 场景**：
- LiteLLM proxy 本身不会被 WAF 拦截（它是服务端）
- 但如果 LiteLLM proxy 后面连接第三方 relay，需要配置 LiteLLM 的出站 UA

#### 3. MCP 工具集成
**现状**：
```python
langchain-mcp-adapters>=0.2.2
```

**兼容性**：
- ✅ MCP 工具是 LangChain tool 系统的一部分
- ✅ 与模型选择无关
- ✅ 完全兼容 LiteLLM

#### 4. LangGraph Interrupt/Resume
**现状**：核心 HITL 澄清功能依赖 LangGraph 原生 `interrupt()/resume`。

**兼容性**：
- ✅ LiteLLM 只是模型层
- ✅ LangGraph 的 checkpointing 和 interrupt 机制完全独立
- ✅ 完全兼容

### 16.4 推荐方案：**方案 A（部分迁移）**

**理由**：
1. **稳定性优先**：现有功能（reasoning、custom relay UA、MCP）已经过充分测试
2. **用户选择**：允许用户根据需求选择直连或 LiteLLM
3. **渐进式验证**：可以先用 LiteLLM 支持几个模型，验证无问题后再扩展
4. **零风险**：不影响现有用户

**实施步骤**：
1. **Phase 1**：添加 LiteLLM 作为可选 provider（环境变量开关）
2. **Phase 2**：文档说明如何启动 LiteLLM proxy
3. **Phase 3**：验证 reasoning 模式在 LiteLLM 下的完整性
4. **Phase 4**（可选）：如果验证通过，考虑将 LiteLLM 设为默认推荐

### 16.5 实施示例（Phase 1）

```python
# backend/pyproject.toml
dependencies = [
    # ... 现有依赖 ...
    "litellm>=1.50.0",  # 可选：仅当启用时需要
]

# backend/.env.example
# LiteLLM Proxy (可选 - 统一 100+ 模型提供商)
LITELLM_ENABLED=false
LITELLM_BASE_URL=http://localhost:4000
LITELLM_API_KEY=sk-litellm-master-key

# app/chat/model_catalog.py
def list_available_providers(config: ProviderApiConfig) -> list[ModelProvider]:
    providers: list[ModelProvider] = []
    
    # 现有 providers
    if config.anthropic_api_key:
        providers.append("anthropic")
    if config.deepseek_api_key:
        providers.append("deepseek")
    if config.openai_api_key:
        providers.append("openai")
    if config.custom_api_key:
        providers.append("custom")
    
    # 新增 LiteLLM provider
    if os.getenv("LITELLM_ENABLED") == "true":
        providers.append("litellm")
    
    return providers

# app/chat/runtime/models.py
def create_openai_compatible_chat_model(
    model_id: str,
    *,
    provider: ModelProvider,
    # ...
):
    if provider == "litellm":
        base_url = os.getenv("LITELLM_BASE_URL") or "http://localhost:4000"
        api_key = os.getenv("LITELLM_API_KEY") or "sk-litellm"
        
        return ChatOpenAI(
            model=model_id,
            base_url=base_url,
            api_key=api_key,
            streaming=True,
            **build_openai_compatible_model_kwargs(
                provider="litellm",
                thinking_enabled=thinking_enabled,
            ),
        )
    
    # 现有逻辑保持不变
    # ...
```

### 16.6 不推荐立即迁移的原因

1. **Reasoning 模式未验证**：需要先确认 LiteLLM + langchain-openai 对 DeepSeek reasoning 的完整支持
2. **现有方案已优化**：我们刚刚修复了 custom relay 的 UA 问题，现有方案运行良好
3. **增加复杂度**：LiteLLM proxy 需要额外的部署和维护
4. **测试成本**：完全迁移需要大量的回归测试

### 16.7 结论

**建议**：暂不迁移到 LiteLLM，理由：
- ✅ 现有方案功能完整且稳定
- ✅ 支持 Anthropic、DeepSeek、OpenAI、Custom relay
- ✅ Reasoning 模式完全可控
- ✅ 已解决第三方 relay 的 UA 拦截问题

**未来考虑**：
- 如果需要支持更多模型提供商（Azure、Vertex AI、Bedrock 等），可以考虑引入 LiteLLM
- 如果需要统一的成本追踪和监控，LiteLLM 是很好的选择
- 可以按**方案 A**的方式，将 LiteLLM 作为可选功能逐步引入

真正原因：用户 `.env` 里 `DEEPSEEK_API_KEY` 与 `CUSTOM_API_KEY` 是**两个不同的 key**、且 `DEEPSEEK_BASE_URL`
未设。所以选择器里的 `DeepSeek · deepseek-v4-pro` 走的是**官方 `api.deepseek.com`**（`resolve_model_provider`
按前端携带的 provider=deepseek → `create_openai_compatible_chat_model` provider=deepseek →
`DEEPSEEK_BASE_URL` 未设 → `PROVIDER_DEFAULT_BASE_URLS["deepseek"]`），**根本没碰中转站**，官方端点不拦 SDK
UA，所以能用。而中转站 `/models` 虽然也列了 deepseek-v4-flash/pro，但只要走 custom provider（挑 `Custom ·`
开头的）就一律 403，与模型无关。至于"openai api"：用户 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 都未设，OpenAI
provider 在目录里是 missing、无任何模型可选；glm/kimi/gpt 只存在于 `Custom` provider 下，只能走中转站。

### 14.3 修法（根因层，代码核实）
单一中性 UA 入口，发现探针与 runtime 共用，保证"显示 == 可用"：

1. `app/chat/model_catalog.py`：
   - 新增常量 `RELAY_USER_AGENT = os.environ.get("SLOTFLOW_RELAY_USER_AGENT") or "SlotFlow/1.0"`（可覆盖，
     应对更挑剔的 WAF 规则）。
   - 新增 `relay_request_headers(provider_env, *, content_json=False)`：在 `provider_headers`（Anthropic 仍
     `x-api-key`/`anthropic-version`、其余 `Authorization: Bearer`）之上，**仅 `custom` provider** 加
     `User-Agent: RELAY_USER_AGENT`。
   - `fetch_provider_model_ids`（GET /models）与 `probe_openai_compatible_chat_model`（POST
     /chat/completions）都改用 `relay_request_headers` → 探针用与 runtime 相同的 UA。
2. `app/chat/runtime/models.py::build_openai_compatible_model_kwargs`：`provider == "custom"` 时加
   `kwargs["default_headers"] = {"User-Agent": RELAY_USER_AGENT}`。`default_headers` 经
   `ChatDeepSeek.validate_environment`（`langchain_deepseek/chat_models.py:242`）与 `ChatOpenAI` 同样透传给
   底层 `openai.AsyncOpenAI`/`OpenAI` 的 `default_headers`，覆盖其默认指纹 UA。`deepseek`/`openai` 官方端点
   **不加** default_headers（默认 SDK UA 不被官方端点拦截）。

验证（/tmp 脚本，未提交）：runtime 逐个 `astream` `glm-5.2`/`kimi-k2.6`/`MiniMax-M3`/`qwen3.6-plus`/
`deepseek-v4-pro`（provider=custom），全部 **HTTP 200 且 UA=SlotFlow/1.0**，正常流式；官方 deepseek 路径
（provider=deepseek, DEEPSEEK_BASE_URL 未设）UA 仍为 `AsyncOpenAI/Python 2.40.0`（不受影响）。relay probe
抓到的 UA 也是 `SlotFlow/1.0`。

### 14.4 不变量与边界（勿回归）
- **只对 `custom` provider 加中性 UA**；deepseek/openai/anthropic 官方端点保持默认 SDK UA（避免无意中改变
  官方端点的鉴权/风控行为，且 Anthropic 用 `x-api-key` 不用 Bearer）。
- **发现探针与 runtime 必须同一 UA**，否则回到"能显示但不能用"的撕裂状态。`probe_openai_compatible_chat_model`
  的 `validate_models` 开关本身仍按 `CUSTOM_VALIDATE_MODELS` 默认 true，没动。
- `x-stainless-*` header **不删**：实测对结果无影响，且 `langchain_openai` 的 `default_headers` 无法真正删
  掉 SDK 注入的这些 header（设空串会被拼成 `python, ` 这种逗号连接垃圾值），删了反而有副作用。只覆盖 UA 这一个
  决定性 header。
- WAF 是黑名单不是白名单：`SlotFlow/1.0` 已 live-verified 放行；如遇更挑剔规则，用
  `SLOTFLOW_RELAY_USER_AGENT` 覆盖即可，无需改代码。
- 这不属于 graph/middleware 行为改动，是 runtime 的 HTTP 客户端指纹修正；`tests/test_provider_reasoning_contract.py`
  仍绿（reasoning 通道未动）。

### 14.5 离线验收
`uv run ruff check app tests` 通过。`tests/test_runtime.py`、`tests/test_model_catalog.py`、
`tests/test_provider_reasoning_contract.py` 全绿，新增两测试：
- `test_custom_provider_kwargs_override_relay_user_agent`：断言 custom kwargs 带
  `default_headers={"User-Agent": RELAY_USER_AGENT}`、UA 不含 `AsyncOpenAI`；deepseek/openai 不加 default_headers。
- `test_relay_request_headers_adds_neutral_user_agent_for_custom_only`：custom 带中性 UA + Bearer；
  deepseek/openai 不加 UA；anthropic 保留 `x-api-key`/`anthropic-version`、不加 UA、不加 Bearer。
- `test_relay_user_agent_env_override`：`SLOTFLOW_RELAY_USER_AGENT` 可覆盖默认 UA。


---

## 17. 迭代 10（2026-07-02）：交互链路修正 —— todo 可见性、首字延迟、snapshot 擦字、HITL 回显、目录滚动

### 17.0 触发症状

用户实测反馈五类交互问题：

1. 模型声称调用了 `write_todos`，但前端全程看不到 todo。
2. DeepSeek 官方 thinking 首字很快，SlotFlow 首字明显慢。
3. 正文流式期间已经出现很多字，结束后又被精简，前面很多内容消失。
4. thinking 期间有时正文先输出，随后 HITL；用户选项会以“我选择 A：...”形态被重复进正文。
5. Skills / MCP / Memory 目录和 Skills 子项列表内容多时不能滚动。

### 17.1 代码核实到的根因

- **todo 可见性当时不是后端事件缺失**：`agent_adapter/streaming.py::iter_projection_agent_events`
  已从 `values` projection 提取 `todo.updated`（§24 起不再后端 signature 去重）；`use-chat-stream.ts` 也处理
  `todo.updated`。问题在 UI 位置：`ComposerTodoPanel` 位于输入框上方，流式时用户视线通常在最新
  assistant 消息附近；输入框靠下或被内容推离注意区域时，用户会认为“没显示”。
- **首字慢的决定性阻塞之一是强制澄清门**：`graph.py::make_triage_gate_node` 在 pro/ultra 的新用户回合
  先调用 `run_triage(...)`，这会在主模型产生任何 token 之前多跑一次 LLM。这个设计能拦住“做个表格”这类
  欠规约请求，但对已经很长、信息完整、甚至明确说“不要问，直接做”的请求仍然先 triage，直接损害首字延迟。
  另一个已在工作树中的延迟点是 skills preflight：`skills_preflight.py::default_find_skills` 原来会走
  installable-skill 网络搜索；本次保持它的 `local_only=True`，让网络搜索只由模型显式调用 `skill_match` 时发生。
- **正文消失是 snapshot 覆盖 live stream**：后端 `chat/routes.py` 在 `run.finished` 时优先用
  `snapshot_message_content` 落库；前端 `use-chat-stream.ts` 收到 `state.snapshot` 时也用
  `latestAssistantContent(...)` 直接 `replaceAssistantContent(...)`。当 LangGraph snapshot 只保留更短的最终
  AIMessage，或者比 `message.delta` 滞后时，已经流给用户看的长正文会被短 snapshot 覆盖。
- **HITL 回显污染来自前端 resume 文本**：`chat-app.tsx::handleSelectClarification` 删除澄清卡片后，把
  `我选择 ${option.id}：${option.label}` 当普通用户消息发送。后端 resume detection 本来只需要
  `request.message` 作为 `Command(resume=<answer>)`，所以这个中文前缀会进入 graph，模型也更容易在正文里复述。
- **目录滚动是布局约束问题**：`directory-modal.tsx` 的居中弹窗固定高度且外层 `overflow-hidden`，内容列缺少
  `min-h-0`，滚动容器在 flex/grid 子项里可能拿不到可收缩高度；Skills 子 Skill 列表也需要自己的 max-height
  + overflow 容器。
- **OpenAI Responses reasoning summary 必须保留**：`test_provider_reasoning_contract.py` 已把
  `{"type":"reasoning","summary":[{"type":"summary_text","text": ...}]}` 固定为 reasoning 通道输入；当前
  `projections.py::extract_reasoning_from_content_block` 必须 flatten `summary[].text`，否则 gpt-5/o-series
  thinking 会静默丢失。

### 17.2 修法

1. **todo 面板保持单一展示位**：`write_todos` 的用户可见状态只进入输入框上方的
   `ComposerTodoPanel`。不要在 `MessageList` 里再加 inline 面板；这会产生两个视觉面板，让用户以为存在两套
   状态，并且和“输入框上方可折叠面板”这个交互预期冲突。实时性应通过 `todo.updated` → `todos` state →
   `ComposerTodoPanel` 的链路解决，而不是靠复制一个就近展示位。
2. **snapshot 只做合并，不擦 live stream**：
   - 前端新增 `mergeAssistantContent(current, incoming)`，`state.snapshot` 到达时只采用更长或前缀兼容的内容；
     较短且不兼容的 snapshot 不再覆盖已流式展示的正文。
   - 后端新增 `select_assistant_content(...)`，落库也按同一规则选择更完整内容，避免刷新后又回到短 snapshot。
   - reasoning 仍沿用既有 `mergeReasoningContent` / `select_assistant_reasoning_content` 长内容优先规则。
3. **HITL fixed-option resume 精简**：`handleSelectClarification` 现在发送 `option.label` 作为
   `request.message`；`option_id` / `option_label` 留在 metadata。后端仍按原协议把普通消息视为
   `Command(resume=...)`，但不会再把“我选择 A：”前缀喂给模型。
4. **首字延迟快路径**：`clarify_gate.py::should_skip_triage_model_call` 默认跳过普通短消息的 triage LLM；
   只有短且明显像欠规约产出任务的请求（例如“做个表格”）才在主模型前跑 triage。详细请求（≥120 字）、
   显式包含“不要问/无需确认/直接做/no clarification/don't ask/just do”等直接执行标记的请求也跳过 triage。
5. **目录滚动**：`directory-modal.tsx` 的内容列加 `min-h-0`，主体滚动区使用原生
   `overflow-y-auto overscroll-contain [scrollbar-gutter:stable]`；子 Skill 列表也加独立滚动容器。
6. **Responses reasoning summary 恢复**：`projections.py::extract_reasoning_from_content_block` 在
   `reasoning/thinking/text/content` 字符串回退后 flatten `summary[].text`，保持 OpenAI Responses API
   summary 形态不丢。

### 17.3 不变量与边界

- `message.delta` 是用户已看到的 live stream；`state.snapshot` 只能补全或纠正为更完整内容，不能把已展示文本
  缩短。除非未来能证明某类 snapshot 是权威“编辑后最终答案”，否则不要回到无条件替换。
- 强制 clarify gate 不能完全删除：它仍负责短、欠规约产出型请求的 HITL。快路径覆盖“普通短消息/明显详细/
  明确不要问”的请求，用确定性文本规则绕过额外 LLM 调用，避免把模型判断放到首字之前。
- `ask_clarification` 的 resume 值仍是用户答案本身；前端只是不再人为加“我选择 A：”前缀。固定选项 id 属于
  UI metadata，不应进入模型正文。
- todo 的真实状态源仍是 LangGraph `todos` state → `todo.updated`；前端只允许一个展示位置：
  `ComposerTodoPanel`。如果用户看不到实时更新，应修事件/状态/revision 链路，不要新增第二个 UI 面板。
- Skills preflight 的 prepare-node 路径保持 local-only；网络型 skill search 只能由模型显式调用工具触发，避免
  每轮 first-token 被外部请求拖慢。
- OpenAI Responses `summary[]` reasoning 是 provider 契约的一部分；改 projection 前必须保
  `tests/test_provider_reasoning_contract.py` 绿。

### 17.4 离线验收

新增/更新的回归点：

- `test_should_skip_triage_model_call_for_direct_or_long_requests`：长完整请求、显式“不要问/直接做”、普通短消息
  跳过 triage；“做个表格”仍不跳过。
- `test_select_assistant_content_keeps_longer_streamed_content_over_short_snapshot`：短 snapshot 不再覆盖更长的
  `message.delta` 正文。
- `tests/test_provider_reasoning_contract.py` 的 OpenAI Responses summary fixtures 继续要求
  `summary[].text` 进入 reasoning 通道。


---

## 18. ToolNode HITL interrupt 不变量（2026-07-02 当前代码）

### 18.0 代码核实

`harness/graph.py::_slotflow_tool_safety_wrapper` 和
`_slotflow_async_tool_safety_wrapper` 包住 `ToolNode` 的每次工具调用；普通工具异常会转为
`tool_execution_error` ToolMessage。但 `ask_clarification` 工具体内调用 LangGraph
`interrupt(...)`，该异常路径是 `GraphBubbleUp`，必须上抛给 LangGraph runtime 才能暂停图并让
`agent_adapter/streaming.py::_clarification_from_pending_interrupt` 发出 `clarification.requested`。

当前代码已经在两个 wrapper 的 `except Exception` 之前放行 `except GraphBubbleUp: raise`。这是
ToolNode 路径的硬不变量；不要把它合并回通用异常处理。否则 voluntary `ask_clarification` 会被包装成
普通工具错误，HITL 不会暂停。

### 18.1 验证边界

回归测试名：`test_ask_clarification_via_slotflow_tool_node_actually_interrupts`。它走真实 graph +
ToolNode + wrapper，验证 `ask_clarification` 能产生 pending interrupt，并在 resume 后继续执行。


---

## 19. 迭代 11（2026-07-02）：懒加载 Docker 代码执行沙箱

### 19.0 目标与取舍

后续 Skills 可能携带脚本、代码示例或需要执行的 helper。直接让模型在宿主机运行代码风险过高；但每轮都预先
创建容器又会拖慢首字。因此本轮实现一个**懒加载**代码执行沙箱：只有模型实际调用 `sandbox_exec` 时才碰 Docker。

没有采用“先复制整个 workspace 进容器、结束再搬回”的方案。SlotFlow 已有 `SlotFlowWorkspace` 边界和
`artifacts/<thread_id>/` 产物命名空间，复制会引入同步冲突和结束时机问题。当前实现用 Docker bind mount：
容器写入可写目录时，文件天然落在本地 workspace 中，不需要额外搬运。

### 19.1 代码链路

1. `chat/runtime/config.py::load_sandbox_config_from_env` 读取代码执行相关环境变量：
   `SLOTFLOW_CODE_EXECUTION_ENABLED`、`SLOTFLOW_DOCKER_SANDBOX_IMAGE`、
   `SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS`、`SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED`。
2. `harness/tools/registry.py::build_harness_tools` 在 workspace tools 后、network tools 前注册
   `build_sandbox_tools(...)`，并把当前 `thread_id` 与 `skills_root` 传入。
3. `harness/tools/sandbox.py::build_sandbox_tools` 创建 `LazyDockerSandbox` 对象，但不启动容器；
   `sandbox_exec(command, timeout_seconds)` 被实际调用时才执行。
4. `harness/sandbox/docker.py::LazyDockerSandbox._ensure_started` 第一次执行时运行
   `docker run -d --rm ... sleep 3600`，后续同一个工具实例复用该容器；进程退出时通过 `atexit` 尝试
   `docker rm -f` 清理。
5. `sandbox_exec` 内部用 `docker exec -w /workspace/work <container> sh -lc <command>` 执行命令，返回
   `ok/exit_code/stdout/stderr/timeout_seconds/mounts/source` JSON；Docker 不可用或启动失败时返回结构化错误，
   不让普通回答链路崩掉。

### 19.2 文件边界

所有路径都复用 `SlotFlowWorkspace.resolve_path`，避免另开一套路径校验规则：

- `/workspace/uploads` → 本地 `uploads/`，只读。用户上传文件只能读取，不能被容器修改。
- `/workspace/artifacts` → 本地 `artifacts/<thread_id>/`，可读写。用户可见输出必须写这里；因为是 bind mount，
  文件立即回到本地 workspace，并会被既有 artifacts 列表/预览链路发现。
- `/workspace/work` → 本地 `.sandbox/<thread_id>/`，可读写。用于临时代码、脚本、包实验和中间结果；不直接展示给用户。
- `/workspace/skills` → 已安装 Skills 根目录，只读且仅当 `skills_root` 存在时挂载。Skill helper 脚本可被读取/执行，
  但不会在容器内修改安装目录。

默认网络为 `--network bridge`，默认镜像为 `python:3.12`，默认超时 120s，使模型可在沙箱内用
`python -m pip install ...` 安装常见 Python 依赖。需要离线执行时，显式设置
`SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED=false`；这和普通 `web_search/web_fetch` 的网络工具边界分开。

### 19.3 不变量

- Docker 容器必须懒创建：构造 `LazyDockerSandbox` 或注册工具不能运行 Docker 命令。
- 代码/脚本执行走 `sandbox_exec`，不要重新引入宿主机 `subprocess` 工具。
- 用户上传保持只读；当前线程 artifacts 和 scratch 才可写。
- 需要展示给用户的文件写 `/workspace/artifacts`，不要写 `/workspace/work` 后再让前端猜。
- Docker 失败是工具结果错误，不是 graph 崩溃。

### 19.4 验证

- `test_lazy_docker_sandbox_starts_only_on_first_exec` 用 fake runner 验证：构造阶段不调用 Docker；第一次
  `exec` 才 `docker run` + `docker exec`；uploads/skills mount 带 `readonly=true`；artifacts/work 可写；
  本地 `artifacts/<thread>` 和 `.sandbox/<thread>` 会创建。
- `test_sandbox_exec_tool_is_disabled_by_config` 验证 `SLOTFLOW_CODE_EXECUTION_ENABLED=false` 对应配置下不注册工具。
- `test_registry_exposes_key_tools_per_category` / `test_registry_orders_workspace_then_network_then_customization`
  验证 `sandbox_exec` 进入 registry 且位于 workspace 与 network 之间。


---

## 20. 迭代 12（2026-07-02）：todo 可视化面板在 Flash 下没有被调用

### 20.0 症状

用户在输入框中要求“测试你的 todo 功能”，界面只看到模型在正文里解释 `write_todos`，没有出现输入框上方的
实时 todo 面板。截图里的模型选择为 Flash 系列，说明这不是单纯前端 CSS 问题，而是工具可用性问题。

### 20.1 根因

`harness/tools/registry.py` 原来只在 `features.plan_enabled` 为真时注册 `write_todos`：
`todo_tools = [write_todos_tool] if features.plan_enabled else []`。Flash 模式下 `plan_enabled=False`，
所以模型根本没有 `write_todos` 工具可调用；它只能在 reasoning/正文中“模拟”或描述 todo 流程。

前端 `ComposerTodoPanel` 仍在 `chat-composer.tsx` 渲染链路上，但 `todos.length === 0` 时返回 `null`。没有真实
工具调用 → 没有 `todos` state → 面板自然不可见。这就是“面板有，但没被调用”的实际机制。

另一个交互问题是旧逻辑在流结束后自动折叠 composer todo 面板；即使中途显示过，结束时也容易让用户误以为
没有可视化面板。

### 20.2 修法

1. `write_todos` 在所有模式注册。Flash 不再因为 `plan_enabled=False` 失去工具；显式测试/要求 todo 时可以触发
   真正的 `Command(update={"todos": ...})`。
2. 当时 Pro/Ultra 仍依赖 `pre_model` 注入 `SLOTFLOW_TODO_SYSTEM_PROMPT` 和 builder 静态提示；§24 已删除这些
   prompt 级约束，改为 `post_model` 节点检查后路由回 `pre_model`。
4. 前端 `ComposerTodoPanel` 有 todo 时默认展开，流结束后不自动折叠；用户仍可手动折叠。
5. 前端在 `state.snapshot` 分支也读取 `latestTodos(...)`，作为 `todo.updated` 中间事件之外的兜底同步。

### 20.3 不变量

- `write_todos` 是真实 UI 状态工具，不应只在 Pro/Ultra 可用；模式只决定“是否主动鼓励规划”，不决定用户显式要求
  todo 时工具是否存在。
- 面向用户的 todo 状态必须进入 `ComposerTodoPanel`，不能让模型用正文表格替代，也不能在消息区复制第二个
  todo 面板。
- 完成后仍保留输入框上方 todo 面板，除非用户手动折叠或切换/重置线程。

### 20.4 验证

- `test_registry_exposes_write_todos_even_in_flash_mode` 覆盖 Flash 模式仍注册 `write_todos`。
- 前端 `pnpm typecheck` 覆盖 `state.snapshot` todo fallback 与 composer 面板改动。


---

## 21. 迭代 13（2026-07-02）：todo 面板重复与更新感修正

### 21.0 症状

用户实测发现 todo UI 仍然不对：

1. 页面同时出现消息区的“任务进度”卡片和输入框上方的 `To-dos` 折叠面板。
2. 用户期望只有输入框上方那个可折叠面板。
3. 面板看起来不是每次 `write_todos` 后都实时推进。

### 21.1 根因

第 17 轮把“看不到 todo”的问题误判成“展示位置离用户视线太远”，于是新增了
`message-list-parts.tsx::InlineTodoProgress` 并在 `MessageList` 中渲染第二个面板。这不是根因修复：
真实状态源仍然只有 LangGraph `todos` state，但两个视觉面板会让用户以为存在两套状态，且违反“输入框上方
可折叠 todo 面板是唯一展示位”的交互设计。

实时性问题在前端 revision 语义上也有缺口：`use-chat-stream.ts::replaceTodos` 只在本轮第一次出现非空 todo
时递增 `todoRevision`。后续 `pending → in_progress → completed` 的状态变化虽然会 `setTodos(...)`，但不会更新
revision；`ComposerTodoPanel` 依赖 revision 触发展开/更新提示时，就表现为“不是每次工具调用都推进”。

### 21.2 修法

1. 删除 `InlineTodoProgress` 组件和 `MessageList` 的 `todos` 传参，todo 只在 `ComposerTodoPanel` 展示。
2. `replaceTodos` 增加 `todoSignatureRef`，按完整 todo 列表签名去重；每次签名变化都 `setTodos` 并递增
   `todoRevision`。
3. 新消息发送、切换线程、重置线程、新建线程时同时清空 `todos`、`todoRevision`、`todoSignatureRef` 和
   `hasTodoListForCurrentRunRef`，避免旧任务列表残留到下一轮。
4. `AGENTS.md` 和本文件都把 todo UI 不变量改为“单一 composer 面板”，避免后续再按旧结论恢复第二展示位。

### 21.3 不变量

- `write_todos` 的唯一用户可见面板是输入框上方的 `ComposerTodoPanel`。
- `todo.updated` 和 `state.snapshot` 都可以更新 `todos`，但前端必须按签名去重；重复事件不能造成闪烁，真实状态变化必须递增 revision。
- 不要通过正文表格或消息区卡片模拟 todo 状态；如果面板没实时更新，应修 SSE/状态/revision 链路。


---

## 22. 迭代 14（2026-07-02）：todo 只显示勾、不显示内容的根因修复

### 22.0 症状

用户再次实测 todo：输入框上方的 `To-dos` 面板出现了进度 `4/4` 和四个完成图标，但每一行没有任务文字。
模型正文还在解释“右侧 todo 面板实时更新”，这说明问题不是 Flash 模式，也不是面板不存在，而是状态内容没有被
正确渲染进唯一的 composer todo 面板。

### 22.1 真实数据复现

直接读取本地运行产生的 `backend/.slotflow/checkpoints.sqlite3`，用 LangGraph 的
`JsonPlusSerializer` 解码最近线程 `thread_306516daebe2` 的 `writes.channel='todos'`，真实 state 是：

```python
[
  {"status": "completed", "text": "🔍 搜索 AI Agent 框架最新动态（LangGraph / CrewAI / AutoGen）"},
  {"status": "completed", "text": "📊 整理三大框架核心对比维度"},
  {"status": "completed", "text": "✍️ 生成技术要点汇总报告"},
  {"status": "completed", "text": "📝 输出最终总结"},
]
```

也就是说模型确实逐步调用了 `write_todos`，并且任务文字存在；但字段名是 `text`。当时后端
`agent_adapter/projections.py::normalize_todos` 只读取 `content`，前端
`hooks/use-chat-stream-helpers.ts::parseTodos` 又只是 `return value as ChatTodo[]` 的类型断言，最终 UI
拿到的对象没有 `content` 字段。`ComposerTodoPanel` 渲染 `todo.content`，所以只剩状态图标和计数，看起来像
“只有几个勾”。

根因不是 CSS、不是滚动、不是模式选择，而是 todo item schema 在三段链路里不一致：

1. `write_todos` 工具签名是 `todos: list[dict[str, Any]]`，暴露给模型的 JSON schema 只有
   `additionalProperties: true`，没有明确告诉模型必须用 `content`。
2. 模型按常见/官方习惯用了 `text` 字段。
3. 后端 projection 和前端 parser 没把 `text` 归一到 `content`。

### 22.2 修法

1. `harness/steps/todo.py::Todo` 改为 Pydantic model，公开 schema 只展示 `content` + `status`；同时用
   `validation_alias=AliasChoices("content", "text")` 兼容旧/模型已发出的 `text`。
2. `write_todos_tool` 函数签名改为 `todos: list[Todo]`，让 LangChain tool schema 不再是任意 object；工具返回
   `Command(update={"todos": normalized_todos})` 前统一 `model_dump()` 成 `{content, status}`。
3. `agent_adapter/projections.py::normalize_todos` 兼容 `content` 和 `text`，把 SSE `todo.updated` 永远输出为
   `{content, status}`。
4. `frontend/src/hooks/use-chat-stream-helpers.ts::parseTodos` 不再做裸类型断言，逐项校验并兼容 `text`，避免旧
   checkpoint / 异常事件再次让 UI 只有图标没有文字。
5. 当时曾在 `SLOTFLOW_TODO_SYSTEM_PROMPT` 追加字段形状提示；§24 已删除这个 prompt 兜底。字段形状只保留在
   tool schema、tool validation、projection normalization 和 frontend parser 边界。

### 22.3 验证

- 用真实 checkpoint 的 raw `text` todos 过修复后的 `normalize_todos`，输出包含完整 `content` 文本。
- `tests/test_harness_steps.py::test_write_todos_tool_is_registered_with_command_return` 验证工具 schema 暴露
  `content`、不暴露 `text`，但旧 `text` 入参会归一成 `content`。
- `tests/test_agent_adapter.py::test_todos_in_values_snapshot_become_todo_updated_event` 验证 `text` snapshot
  也能输出 `todo.updated` 的 `content`。
- `pnpm typecheck` 通过前端 parser 和 UI 类型检查。

### 22.4 不变量

- 对外/前端统一只认 `{content, status}`；`text` 只能作为兼容输入存在于 tool validation、projection normalization
  和 frontend parsing 边界。
- `parseTodos` 不能再用裸类型断言；SSE 是跨边界数据，必须做运行时校验。
- 看到“只有勾没有文字”时，优先检查真实 checkpoint/SSE payload 字段名，不要先改 CSS 或再加 UI 面板。


---

## 23. 迭代 15（2026-07-02）：删除父 Skill 后子 Skill 残留

### 23.0 症状

用户在 Skills 面板删除“大 Skill”（父 Skill）后，里面的小 Skills 仍然留在面板里。这个问题通常出现在两类安装：

1. 新的 registry 安装：主 Skill 在 `skills/<parent>/`，依赖 Skill 被复制到
   `skills/<parent>/dependencies/<child>/`，配置里 `child.parent=<parent>`。
2. 老的/迁移前安装：同一个 `package_url` 的多个 Skill 仍是 `skills/<parent>/`、`skills/<child>/`
   平级目录；`list_skills` 会通过 `infer_missing_dependency_parents()` 把子项归到父项下。

### 23.1 根因

`backend/app/skills/routes.py::delete_skill` 原来只做了：

1. `find_skill_by_name(root, skill_name)` 找到当前要删的 Skill。
2. `shutil.rmtree(skill.skill_dir)` 删除这个 Skill 自己的目录。
3. `store.remove_skill_tree_config(skill.name)` 删除配置里的父子项。

这只删除了“当前目录”。如果子 Skill 是平级 legacy 目录，配置被删后物理目录还在；下一次
`load_enabled_skills(root, enabled_names=None)` 会重新扫描所有 `SKILL.md`，于是子 Skill 又以顶层/孤儿项形式出现。
如果子 Skill 在 nested `dependencies/` 下，删除父目录会物理删除它，但扫描缓存也可能保留旧结果到 TTL 过期。

所以根因不是前端分组，而是后端删除没有以“Skill tree”为物理删除单位，也没有在删除后显式清
`harness.skills.registry` 的 scan cache。

### 23.2 修法

1. `delete_skill` 先调用 `skill_tree_for_delete(...)` 收集要删除的完整树：
   - 被点选的父 Skill；
   - 配置中 `parent` 递归指向它的子/孙 Skill；
   - 物理目录位于父 Skill 目录下的 nested child Skill。
2. 删除前逐项检查 protected 和路径边界，避免删到 `skills_root` 外或半删 protected 子项。
3. 用 `minimal_delete_dirs(...)` 去掉已经被父目录覆盖的 nested 子目录，只对最小目录集合执行 `shutil.rmtree`。
4. `SlotFlowSkillsConfigStore.remove_skill_tree_config` 改为递归删除配置树，不再只删一层 direct child。
5. 删除后从 `runtime_config.enabled_skills` 移除整棵树的名字，并调用 `invalidate_skill_scan_cache()`，再
   `refresh_runtime_skills_config(...)`。

### 23.3 验证

- `test_delete_parent_skill_removes_installed_dependency_skills`：模拟 skills CLI 返回父 + child，确认删除父后
  `skills/<parent>/dependencies/<child>` 不会在 `/api/skills` 里残留。
- `test_delete_parent_skill_removes_legacy_same_package_children`：模拟旧式同 `package_url` 的平级父/子目录，
  先触发 `infer_missing_dependency_parents()`，再删除父，确认父目录和子目录都被物理删除，列表里也都消失。

### 23.4 不变量

- Skills 面板里的父子关系不是纯前端视觉分组；`SkillRecord.parent` 表示删除/配置生命周期上的树关系。
- 删除父 Skill 必须删除整棵树的配置和物理目录。只删父配置或只删父目录都会留下可被扫描器重新发现的孤儿 Skill。
- 修改 skills 磁盘目录后必须清 scan cache；不要依赖 `skills_root` mtime 捕捉 nested 目录变化。


---

## 24. 迭代 16（2026-07-02）：todo 从 prompt 约束迁移到 post_model 节点策略

### 24.0 症状

用户确认 composer 上方的 todo 面板终于能显示后，又指出上一次修法仍有一个设计问题：为了让模型更容易调用
`write_todos`，我们把太多要求写进了静态 prompt，包括 builder 里的“显式测试 todo 时调用 write_todos”和
`SLOTFLOW_TODO_SYSTEM_PROMPT` 里的字段形状提示。这会让 todo 行为继续依赖模型是否服从提示词；一旦模型正文先答、
忘记更新状态、或把 todo 进度写进回答，前端状态仍可能滞后。

### 24.1 根因

根因是边界放错了：todo 是 SlotFlow 的运行时 UI 状态，不应该只靠 prompt 约束模型“记得更新”。提示词能提升概率，
但不能保证图状态发生变化，也不能在每次模型步结束后检查实际 state。真正可靠的边界应该在 LangGraph 节点层：
`agent` 产生 AIMessage 后，`post_model` 能看到最新 AIMessage、tool_calls 和当前 `state.todos`，因此它才是判断
“是否需要创建/更新 todo 并回环给模型”的正确位置。

另外，后端 streaming 原来对 `todo.updated` 做 signature 去重。这个优化降低了事件量，但也让“每个 values snapshot
都输出当前 todo 状态”这个调试/同步契约不成立；前端已经有签名去重，后端再去重没有必要。

### 24.2 修法

1. 删除静态 todo prompt 入口：
   - `harness/steps/todo.py` 删除 `SLOTFLOW_TODO_SYSTEM_PROMPT`；
   - `harness/graph.py::make_pre_model_node` 不再把 todo prompt 拼进 `system_prompt`；
   - `harness/builder.py` 删除“测试/展示 todo 时调用 write_todos”的扩展工具提示；
   - `harness/builder.py` 删除 operating procedure 中“Plan the work with write_todos”的 prompt 级约束。
2. 保留 schema 级修复：`write_todos_tool` 仍用 Pydantic `Todo` 暴露 `{content,status}`，并用
   `validation_alias=AliasChoices("content", "text")` 兼容旧输入。字段正确性属于工具 API contract，不是 prompt
   兜底。
3. 新增 `harness/steps/todo.py::todo_enforcement_update`，由 `post_model` 每次模型调用后执行：
   - 如果最新 AIMessage 已经调用 `write_todos`，交给 `ToolNode` 正常执行；
   - 如果还有其它 tool_calls，先让工具执行，不抢路由；
   - 如果没有 todos，且当前 Pro/Ultra 请求看起来需要进度管理（长任务、代码/修复/分析/报告等任务词，或显式
     todo 请求），追加 `HumanMessage(name="slotflow_todo_enforcer")`，要求模型先调用 `write_todos`；
   - 判断“请求是否需要 todo”前先剥掉 latest user message 开头的 `<slotflow-...>` 注入块，避免 uploads 等长上下文把普通工具读取误判成长任务并造成回环；
   - 如果已有 todos 但未全部 completed，而模型试图直接回答且没有调用 `write_todos`，追加同名控制消息，要求先更新
     当前状态；
   - 同一次 write_todos 之后最多注入一次 enforcer，避免模型拒绝工具调用时形成无限回环。
4. `harness/graph.py::route_after_model` 增加 todo enforcer 分支：最新消息是
   `slotflow_todo_enforcer` 时路由回 `pre_model`，否则才走 `tools_condition` → `tools/finalize`。这使 todo 约束成为
   图级后置策略，而不是静态 prompt。
5. `chat/agent_adapter/streaming.py` 移除 backend todo signature 去重；每个 values snapshot 只要含 todos 就输出一次
   `todo.updated`。前端 `use-chat-stream.ts::replaceTodos` 仍按签名去重，避免重复 identical event 造成 UI 闪烁。

### 24.3 当前不变量

- todo 创建/更新由 graph 的 `post_model` 节点兜底；不要再把 todo 可靠性修成 builder/system prompt 里的硬约束。
- tool schema、projection normalization、frontend parser 可以约束/归一字段形状；不要靠自然语言提示模型不要用 `text`。
- `write_todos` 仍在所有模式注册；主动强制创建 todo 只在 `plan_enabled` 且请求看起来需要计划时触发，或用户显式要求
  todo 时触发。
- `todo.updated` 是状态同步事件，不是“仅状态变化事件”；后端每个带 todos 的 values snapshot 都可以发，UI 层负责
  去重和 revision。

### 24.4 验证

- `tests/test_harness_steps.py::test_todo_enforcement_requests_initial_list_for_planned_work` 覆盖无 todos 的计划任务会注入
  `slotflow_todo_enforcer` 并通过 `route_after_model` 回到 `pre_model`。
- `tests/test_harness_steps.py::test_todo_enforcement_requests_status_update_for_incomplete_todos` 覆盖已有未完成 todos 时，
  模型直接回答会被要求先更新状态。
- `tests/test_harness_steps.py::test_todo_enforcement_does_not_loop_after_existing_enforcer` 覆盖拒绝/遗漏工具调用时不会无限回环。
- `tests/test_harness_steps.py::test_todo_enforcement_ignores_slotflow_injected_context_for_complexity` 覆盖内部注入块不会把简单请求误判成 todo-worthy。
- `tests/test_agent_adapter.py::test_identical_todo_snapshots_emit_each_todo_updated_event` 覆盖重复 identical values snapshot 也会
  输出两次 `todo.updated`。


---

## 25. 迭代 17（2026-07-02）：系统提示词加入时效性查证与记忆使用策略

### 25.0 症状

用户指出 DeepSeek 容易直接用训练数据回答，而不是先确认当前日期、判断是否需要联网或查其它来源；同时模型在任务开始前不会稳定判断是否要查已有长期记忆，任务结束后也不会稳定判断是否要保存新记忆。

### 25.1 根因

这不是工具缺失：`web_search`/`web_fetch` 已经在 harness tools 中，长期记忆也已有三条路径：prepare 检索、`memory_list`/`memory_save` 等显式工具、finalize 后台抽取。问题在系统 prompt 的默认决策策略不够明确：模型知道“能搜索/能记忆”，但没有被明确要求把“信息是否时效敏感”和“本轮是否需要记忆检索/保存”作为每轮任务的判断步骤。

### 25.2 修法

1. `harness/builder.py::build_system_prompt` 在 `<slotflow-runtime>` 中加入 `current_utc_date=<date>`，让模型有明确当前日期锚点，而不是只依赖训练截止时间。
2. 新增 `<slotflow-freshness-policy>`：
   - 回答前用当前日期约束时效性判断；
   - 对新闻、价格、法律、发布版本、API/模型能力、排名、可用性、公司/产品状态、日程、天气、统计数据，以及用户问 latest/current/today/now 的问题，不能只用训练数据；
   - 优先用 `web_search`/`web_fetch`、workspace/upload/MCP 等权威来源；
   - 对稳定定义、基础数学、工作区本地代码这类不随时间明显变化的问题，可以不联网；
   - 多个来源明显冲突时必须告诉用户冲突和不确定性。
3. 扩展 `<slotflow-long-term-memory-status>`：
   - 任务开始时先判断已有记忆是否可能影响回答；prepare 已自动检索相关记忆，但若用户偏好、过往项目、个人资料或历史决策可能相关且注入记忆不足，模型应调用 `memory_list`；
   - 任务结束后判断是否有稳定偏好、profile、项目上下文或事实要保存/修正，使用 `memory_save`/`memory_update`/`memory_delete`；
   - 明确不要保存一次性临时任务细节。

### 25.3 不变量

- 时效性策略是 system prompt 级行为约束，不是前端或工具层强制；不要让每个问题都无条件联网，否则会增加首字延迟和无意义搜索。
- 当前日期使用 SlotFlow 后端 `utc_now().date()` 写入 prompt；如果后续要展示本地时区，应新增明确的 timezone 字段，而不是让模型猜。
- 记忆策略应补充而不是替代图节点：prepare/pre_model/finalize 仍负责检索注入和后台抽取，模型工具调用负责需要显式判断的 `memory_list`/`memory_save`/`memory_update`/`memory_delete`。

### 25.4 验证

- `tests/test_harness_builder.py::test_harness_builder_passes_graph_boundary_arguments` 断言系统 prompt 包含 `current_utc_date=`、`<slotflow-freshness-policy>`、训练数据限制、来源冲突披露、`memory_list` 和 `memory_save` 策略。


---

## 26. 迭代 18（2026-07-02）：Docker 沙箱可用性与危险执行工具收口

### 26.0 症状

用户实测发现模型好像不能在 Docker 沙箱中实际工作，怀疑依赖安装不可用；同时要求把原本比较危险的操作，例如 bash，限制成只能在 sandbox 中使用。

### 26.1 根因

上一版沙箱实现了懒加载和 bind mount，但默认运行环境偏“最小”：`python:3.12-slim`、30 秒 timeout、默认 `--network none`。这对纯 Python 小脚本足够，但模型一旦需要 `pip install`、执行带 bash 的 helper、或跑较慢依赖安装，就会失败或超时。更重要的是，工具注册层还没有显式过滤 extra tools / MCP tools 中可能出现的宿主机执行工具；即使 SlotFlow 自己没有提供 `bash`，外部 MCP 仍可能暴露同名/类似工具，造成绕过 `sandbox_exec` 的风险。

当前运行环境没有 Docker CLI（`docker: command not found`），所以真实容器测试在本机只能 skip；这也说明工具层必须把 Docker 不可用作为结构化工具错误返回，而不是让 graph 崩溃。

### 26.2 修法

1. `harness/sandbox/config.py` 默认改为更可工作的执行环境：
   - `docker_image="python:3.12"`，包含 Python/pip/bash 和更常见的基础能力；
   - `docker_timeout_seconds=120`，给依赖安装和脚本启动留出时间；
   - `docker_network_enabled=True`，默认允许容器联网安装依赖。需要离线时设置 `SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED=false`。
2. `chat/runtime/config.py::load_sandbox_config_from_env` 不再把 Docker 网络默认硬编码为 false，而是跟随 `SlotFlowSandboxConfig().docker_network_enabled`。
3. `harness/sandbox/docker.py::_ensure_started` 增加：
   - `--init`，避免子进程僵尸；
   - `PYTHONUNBUFFERED=1`；
   - `PIP_DISABLE_PIP_VERSION_CHECK=1`；
   - 默认 `--network bridge`，显式关闭时才是 `none`。
4. `harness/tools/sandbox.py::sandbox_exec` docstring 明确：shell/bash/python/node/npm/pip、生成脚本、Skill helper、依赖安装和包实验全部必须走 `sandbox_exec`；需要 Python 依赖时用 `python -m pip install ...` 在沙箱内安装。
5. `harness/tools/registry.py` 新增 `filter_unsafe_host_execution_tools` / `is_unsafe_host_execution_tool`，过滤 extra tools 与 MCP tools 中的宿主机执行入口，例如 `bash`、`shell`、`terminal`、`python_repl`、`run_command`、`execute_command`、`pip`、`npm`。`sandbox_exec` 本身不被过滤。
6. `harness/builder.py` 系统提示词增加执行边界：所有 shell/bash/python/node/npm/pip 命令、生成脚本、依赖安装和 Skill helper 只能用 `sandbox_exec`；不要用 host shell/terminal/MCP execution tools。

### 26.3 不变量

- 模型可见的代码执行入口只能是 `sandbox_exec`。如果后续接入新的 MCP/extra tools，registry 仍必须过滤宿主机执行类工具。
- 容器文件系统不持久；持久的是 bind mount：`/workspace/artifacts` 和 `/workspace/work`。全局 pip 安装不持久；需要复用依赖时应安装到 `/workspace/work` 里的 venv 或 target 目录。
- 用户可见文件写 `/workspace/artifacts`；`/workspace/work` 是持久 scratch，不直接进 UI。
- Docker 不存在/未启动是 `sandbox_exec` 的结构化错误结果；不能让 graph 崩溃。

### 26.4 验证

- `tests/test_harness_sandbox.py::test_sandbox_config_defaults_support_dependency_installation` 覆盖默认镜像、timeout、网络策略。
- `tests/test_harness_sandbox.py::test_lazy_docker_sandbox_starts_only_on_first_exec` 覆盖 `--init`、`bridge`、env、mount 和懒加载。
- `tests/test_harness_sandbox.py::test_lazy_docker_sandbox_uses_no_network_when_disabled` 覆盖显式离线时 `--network none`。
- `tests/test_harness_sandbox.py::test_sandbox_exec_tool_returns_structured_error_without_docker` 覆盖 Docker 不可用时工具返回结构化错误。
- `tests/test_harness_sandbox.py::test_lazy_docker_sandbox_runs_real_container_when_docker_is_available` 是真实 Docker smoke test；当前机器无 Docker CLI，所以 pytest skip。
- `tests/test_tool_registry.py::test_registry_filters_unsafe_host_execution_extra_tools` 覆盖 extra tools 中的 `bash`/`python_repl` 被过滤。
- `tests/test_harness_mcp.py::test_build_harness_tools_filters_unsafe_mcp_execution_tools` 覆盖 MCP 暴露 `bash` 时也被过滤。


---

## 27. 迭代 19（2026-07-02）：沙箱执行状态流式展示

### 27.0 症状

用户指出初始化镜像、执行代码、安装依赖时前端长时间没有输出，体感像卡死，也缺少“当前到底在做什么”的安全感。

### 27.1 根因

`sandbox_exec` 的 Docker 启动和 `docker exec` 发生在 ToolNode 内部，是阻塞工具执行。现有前端只展示 `message.delta`、`context.compressing`、`todo.updated` 等事件；如果模型已经发起工具调用但 Docker 正在拉镜像/启动/执行命令，模型正文不会继续流出，UI 就没有任何进度反馈。

直接在工具里用 `langgraph.config.get_stream_writer()` 不是当前依赖下的稳妥修法：实测 `astream_events(version="v3")` 的 projection 只暴露 `messages`、`values`、`lifecycle`、`subgraphs`，没有 `custom` channel；而 SlotFlow 不能为了自定义事件退回普通 `astream(stream_mode="custom")`，否则会破坏现有 v3 typed reasoning/content 投影。

### 27.2 修法

1. 新增业务/SSE 事件 `tool.status`：
   - `chat/agent_adapter/events.py::AgentEventName`
   - `chat/sse.py::SseEventName`
   - `frontend/src/lib/sse-parser.ts::ChatStreamEventName`
2. `chat/agent_adapter/projections.py::tool_status_event_from_tool_call` 从 LangGraph `tool_calls` projection 识别 `sandbox_exec`，生成：
   - `tool_name="sandbox_exec"`
   - `phase="running"`
   - `message="正在初始化 Docker 沙箱并执行代码"`
   - 截断后的 `command`
3. `chat/agent_adapter/streaming.py::iter_projection_agent_events` 在转发 `tool.delta` 之前先转发 `tool.status`。这是当前 v3 projection 下最早的稳定时机：模型已经决定调用 `sandbox_exec`，ToolNode 还未完成阻塞执行。
4. 前端 `use-chat-stream.ts` 处理 `tool.status`，把状态挂到当前 streaming assistant message；完成、取消或错误时清理。
5. `message-list-parts.tsx` 在 assistant 气泡里渲染 `ToolStatusIndicator`，显示“沙箱 / 正在初始化 Docker 沙箱并执行代码 / 命令”，让用户看到长耗时工具正在运行。

### 27.3 不变量

- `tool.status` 是 UI 进度提示，不是工具结果；真实 stdout/stderr/exit_code 仍由 `sandbox_exec` 的 ToolMessage JSON 返回给模型。
- 当前事件从 `tool_calls` projection 发出，因此描述应保持为“正在初始化 Docker 沙箱并执行代码”，不要假装知道 Docker 已经完成 pull、容器已启动或命令已进入某个更细阶段。
- 不要切换掉 LangGraph v3 projection 协议；reasoning/content 正确分流优先级高于更细粒度的工具内部状态。
- 若未来 LangGraph v3 暴露 custom projection channel，可以再把 Docker `_ensure_started` / `docker exec` 的细粒度阶段接入 `get_stream_writer()`，但必须保持 `tests/test_provider_reasoning_contract.py` 绿色。

### 27.4 验证

- `tests/test_agent_adapter.py::test_sandbox_tool_call_becomes_tool_status_event` 覆盖 `sandbox_exec` tool call 到 `tool.status` 的数据形状。
- `tests/test_agent_adapter.py::test_langgraph_event_adapter_emits_sandbox_tool_status_before_tool_delta` 覆盖流式层先发 `tool.status`、再发 `tool.delta`。
- `tests/test_sse.py::test_agent_event_to_sse_event_keeps_tool_status` 覆盖 SSE 层原样转发新事件。


---

## 28. 迭代 20（2026-07-02）：Docker Engine 安装入口与危险工具拦截提示

### 28.0 症状

用户希望 Docker 不存在时不要只告诉用户“docker: command not found”，而是把安装 Docker Engine 的能力纳入 SlotFlow；同时强调模型只能在 Docker 沙箱里运行危险命令/代码/脚本，否则应直接报错并提醒模型改用脚本/沙箱。

### 28.1 根因

前一版已经过滤 extra/MCP 中的 `bash`、`python_repl`、`run_command` 等宿主机执行工具，但仍有两个缺口：

1. Docker Engine 是 `sandbox_exec` 的宿主机前置依赖；Docker 不存在时，模型没有可调用的“检查/安装 Docker”入口，只能把错误转述给用户。
2. 被过滤的危险工具通常不会出现在模型 tool schema 中，但如果模型/旧上下文仍发起 `bash` 这类未知工具调用，ToolNode wrapper 只返回通用 `unknown_tool`，没有明确告诉模型“这是被安全策略拦截，应该改用 `sandbox_exec` 或 Docker 安装入口”。

直接暴露宿主机 bash 或让模型传任意安装脚本不可接受：Docker 尚未安装时当然不能在 Docker 里安装 Docker；但宿主机安装又是高权限、可改变系统状态的操作，必须是固定流程、默认禁用、用户显式允许，而不是任意命令执行。

### 28.2 修法

1. 新增 `harness/sandbox/docker_engine.py::DockerEngineSetup`，读取 `/etc/os-release` 生成 `host`
   信息（`os_id`、`id_like`、`pretty_name`、`install_manager`、`install_supported`），提供三种受控动作：
   - `check`：检查 `docker` CLI 与 daemon，并返回宿主机信息；
   - `install_script`：按检测到的 Linux 发行版返回固定安装脚本（Debian/Ubuntu=`apt`，
     Fedora/RHEL-like=`dnf`，Arch-like=`pacman`）；
   - `install`：仅在 `SlotFlowSandboxConfig.allow_host_docker_install=True` 且
     `confirm_host_install=True` 时执行检测到的固定命令序列：包管理器安装 Docker 包、
     `systemctl enable --now docker`、`usermod -aG docker <user>`；不接受模型传入脚本或任意命令。
2. `SlotFlowSandboxConfig` 增加 `allow_host_docker_install`，环境变量为
   `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL`，默认 `true`，但 `install` 仍必须收到 `confirm_host_install=true`，并且只能在用户明确要求安装 Docker Engine 后调用。设置为 `false` 时，`install` 只返回错误、hint 和固定脚本，不改宿主机。
3. `harness/tools/sandbox.py::build_sandbox_tools` 注册 `docker_engine_setup`。它与
   `sandbox_exec` 同属沙箱/执行边界工具，但职责是宿主机 Docker 前置依赖的受控安装/检查，不是代码执行。
4. 新增 `harness/tools/host_execution.py` 集中维护危险宿主机执行工具名/片段；
   `harness/tools/registry.py` 和 `harness/steps/tool_safety.py` 共用同一检测逻辑。
5. `harness/steps/tool_safety.py::build_unknown_tool_error_message` 在未知工具名属于危险宿主机执行入口时，返回
   `unsafe_host_execution_tool` ToolMessage，明确提示模型用 `sandbox_exec`，Docker 缺失时用
   `docker_engine_setup`。
6. 系统提示词更新：`sandbox_exec` 失败且 Docker 不可用时先调用
   `docker_engine_setup(action='check')` 查看宿主机信息，需要手动命令时调用 `install_script`，
   只有用户明确要求安装且 host install 开关已启用时才调用 `install` + `confirm_host_install=true`。

### 28.3 不变量

- `docker_engine_setup` 是固定 host setup 入口，不是通用 host shell；不得增加任意 `command`/`script` 参数。不同宿主机的区分由它读取 `/etc/os-release` 完成，不需要另加“系统信息工具”。
- 不允许静默安装 Docker；即使 `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL` 默认为 true，模型也必须在用户明确要求后传
  `confirm_host_install=true`。如果 sudo 需要密码，工具会失败并返回手动脚本，不能交互式索要密码。
- 代码、包安装、Skill helper、用户脚本仍只能用 `sandbox_exec`；`docker_engine_setup` 只解决 Docker Engine 前置依赖。
- 危险工具过滤和未知工具错误提示必须使用同一工具名检测逻辑，避免 registry 过滤与 ToolNode 错误信息分叉。

### 28.4 验证

- `tests/test_harness_sandbox.py::test_docker_engine_setup_tool_returns_install_script_when_host_install_disabled` 覆盖默认不改宿主机。
- `tests/test_harness_sandbox.py::test_docker_engine_setup_check_reports_missing_docker` 覆盖 Docker 缺失检查结果。
- `tests/test_harness_sandbox.py::test_docker_engine_setup_install_script_matches_detected_host` 覆盖 Fedora/RHEL-like 主机生成 `dnf` 脚本而不是 apt 脚本。
- `tests/test_harness_sandbox.py::test_docker_engine_setup_install_uses_fixed_commands` 覆盖开启自动安装后只执行检测到的固定命令序列。
- `tests/test_harness_steps.py::test_tool_safety_redirects_unsafe_unknown_host_tool_to_sandbox` 覆盖未知 `bash` 调用返回 `unsafe_host_execution_tool` 并提示 `sandbox_exec` / `docker_engine_setup`。
- `tests/test_tool_registry.py::test_registry_exposes_key_tools_per_category` 覆盖 `docker_engine_setup` 在工具注册表中存在。


---

## 29. 迭代 21（2026-07-02）：Skills preflight 与长期记忆的上下文边界修复

### 29.0 症状

用户说“我研究的方向是小目标检测与跟踪”，但长期记忆里混入了 `installed_matches` 以及 Skill 描述 JSON。早期尝试加一个针对 `installed_matches` 尾巴的清洗器，这是错误方向：它只处理当前字符串形状，一旦 preflight JSON 改字段、换顺序、换上下文块，污染仍会复发。

### 29.1 根因

`harness/steps/skills_preflight.py::skills_preflight_update` 原来把 `<slotflow-skills-preflight>` 块 prepend 到最新 `HumanMessage.content`。这个设计把“SlotFlow 内部检索结果”伪装成了“用户说过的话”。随后两个记忆入口都会读取最新用户消息：

1. `long_term_memory.build_turn_memory_candidate` / `explicit_save_update` 用最新 user+assistant turn 做显式“记住”提取；
2. `long_term_memory.build_extraction_conversation` 把最新 user+assistant turn 交给后台 LLM 记忆抽取。

因此 memory 层看到的不是纯用户事实，而是“内部 Skill 元数据 + 用户事实”。在 memory 层清洗 `installed_matches` 是表象修补；真正边界应是：内部检索上下文不得写入 `HumanMessage.content`。

### 29.2 修法

1. 删除 `harness/memory/sanitize.py` 以及所有 `strip_slotflow_context_blocks` 调用/测试，不再做字段名或尾巴形状清洗。
2. `harness/steps/skills_preflight.py::skills_preflight_update` 只把 finder 结果写入 `state.slotflow.skills_preflight`，不返回 `messages`，不修改 `HumanMessage.content`。
3. `harness/graph.py::make_pre_model_node` 在每次模型调用前读取 `state.slotflow.skills_preflight`，用 `format_preflight` 拼入当前 step 的 `system_prompt`。这样模型仍能看到 installed Skills 提示，但 memory extraction 读取的 user message 保持为用户原文。
4. `harness/builder.py` 的提示词从“preflight 注入 latest user message”改为“preflight 作为 internal system context”。
5. `format_preflight` 明确标注这是 SlotFlow internal context，不是 user profile/preference；这是系统上下文语义说明，不是污染修复的主要机制。

### 29.3 不变量

- 不要在 memory 层针对 `installed_matches`、`results`、Skill 描述 JSON 或其它尾巴形状写硬清洗；这些都是下游症状。
- Skills preflight、tool discovery、runtime summary 等内部上下文必须走 `slotflow` state / `system_prompt`，不能写入 `HumanMessage.content`。
- 用户上传文件路径仍由 `uploads_update` 注入到用户消息，这是模型读取上传文件的显式输入契约；它不是 Skill 元数据，不能用这次规则一刀切删除。
- 任何未来新增的模型内部上下文，都先问“后续 memory / artifact / replay 会不会把它当作用户说过的话”；如果会，就必须放到 state/system，而不是 user message。

### 29.4 验证

- `tests/test_harness_steps.py::test_skills_preflight_stores_result_without_mutating_user_message` 覆盖 preflight 只写 `state.slotflow.skills_preflight`，不返回 `messages`，原 HumanMessage 保持纯用户文本。
- `tests/test_harness_steps.py::test_skills_preflight_format_is_internal_system_context` 覆盖 preflight system block 的 internal context 标记与 installed skill 元数据仍可被模型读取。
- 删除旧的 sanitizer 测试，避免把错误的表象修法固化成 contract。


---

## 30. 迭代 22（2026-07-02）：Docker 沙箱 idle 生命周期

### 30.0 症状

用户指出沙箱不应该每次都像重新下载/长期占用资源：理想行为是项目级镜像下载一次，后续只是在需要时打开容器，不操作一段时间就自动关闭，避免浏览器/服务异常退出后容器一直跑。

### 30.1 根因

Docker 镜像本身已经由 Docker daemon 按 image tag/layer 全局缓存；`docker run python:3.12 ...` 只有本机没有该镜像时才会拉取，后续同项目/同宿主机不会重复下载同一镜像。真正的问题在 SlotFlow 容器生命周期：`LazyDockerSandbox` 只在第一次 `sandbox_exec` 懒启动容器，之后只通过 `atexit` 在后端进程退出时清理。由于 runtime 每轮会创建 graph/tool closure，异常中断或长时间无新命令时，容器可能一直存在到进程退出。

### 30.2 修法

1. `SlotFlowSandboxConfig` 新增 `docker_idle_timeout_seconds`，默认 600 秒，环境变量 `SLOTFLOW_DOCKER_SANDBOX_IDLE_TIMEOUT_SECONDS`。
2. `LazyDockerSandbox.exec` 在每次命令开始前取消旧 idle timer，命令结束后重新安排 idle close。这样连续命令不会被中途关闭；最后一次命令后开始计时。
3. `LazyDockerSandbox.close` 线程安全化：取消 idle timer、清空 container id，然后执行 `docker rm -f <container>`。
4. `sandbox_exec` 返回 payload 增加 `idle_timeout_seconds`，方便前端/日志知道当前回收窗口。

### 30.3 不变量

- 镜像下载交给 Docker daemon 缓存，不在 SlotFlow 自己实现“下载缓存”。不要在每次 `sandbox_exec` 前显式 `docker pull`。
- 容器仍然懒创建；注册工具、构造 graph、创建 `LazyDockerSandbox` 都不能触碰 Docker。
- 容器仍按当前 thread 挂载 `/workspace/artifacts` 和 `/workspace/work`，避免跨会话写错产物目录。项目级复用的是 Docker image cache，不是把所有 thread 强行塞进同一个容器挂载。
- idle close 是资源保护边界；手动 `close()`、后端退出 `atexit` 和 idle timer 都必须安全地重复调用。

### 30.4 验证

- `tests/test_harness_sandbox.py::test_runtime_loads_sandbox_config_from_env` 覆盖 idle timeout env 解析。
- `tests/test_harness_sandbox.py::test_sandbox_config_defaults_support_dependency_installation` 覆盖默认 idle timeout 为 600 秒。
- `tests/test_harness_sandbox.py::test_lazy_docker_sandbox_starts_only_on_first_exec` 覆盖执行后返回 idle timeout 并安排 timer。
- `tests/test_harness_sandbox.py::test_lazy_docker_sandbox_closes_after_idle_timeout` 覆盖 idle timer 触发后 `docker rm -f`，下一次执行重新 `docker run` 打开容器。


---

## 31. 迭代 23（2026-07-02）：右侧工作区用户终端

### 31.0 症状

Docker Engine 安装这类宿主机 setup 任务需要用户可见、可操作的终端。让 agent 暴露任意宿主机 bash 不符合沙箱边界；但只让 agent 输出命令又让用户需要切到外部终端，交互割裂。

### 31.1 根因

SlotFlow 之前只有 agent 工具和产物预览：

- agent 工具层必须继续禁止宿主机 shell/code execution，只允许 `sandbox_exec` 和受控的 `docker_engine_setup`；
- 右侧工作区面板只能预览产物/上传文件，没有人类手动执行宿主机命令的位置。

因此正确边界不是把 terminal 变成 model tool，而是提供一个**用户操作的 host PTY**，只由前端按钮打开，agent 不可调用。

### 31.2 修法

1. 新增 `backend/app/terminal/routes.py`：`/api/terminal/ws` WebSocket 打开一个 PTY shell，发送 `ready` / `output` / `exit` JSON 文本事件，接收 `input` / `resize` JSON 文本事件。
2. `main.py` 注册 terminal router。`SLOTFLOW_TERMINAL_CWD` 可指定终端 cwd，`SLOTFLOW_TERMINAL_SHELL` 可指定 shell；默认 cwd 是后端进程 cwd，默认 shell 是 `$SHELL` / bash / sh。
3. `frontend/src/lib/chat-stream.ts::resolveTerminalWebSocketUrl` 把现有 API base URL 转成 ws/wss URL，local browser 默认直连 `127.0.0.1:8000`。
4. `workspace-panel.tsx` 增加“文件 / 终端”切换。终端连接状态提升到 `WorkspacePanel` 生命周期：第一次点击“终端”才建立 WebSocket，之后切回文件或关闭/重开右侧面板都不主动断开；只有页面/component unmount、连接错误或用户点重连时才关闭旧连接。
5. 终端输出用 `@xterm/xterm` + `@xterm/addon-fit` 渲染。根因是 PTY shell 会输出 ANSI/OSC 控制序列（颜色、标题、bracketed paste、光标控制），普通 `<div>` 只能把这些字节当文本显示，所以会出现 `]3008...`、`[?2004h` 这类“乱码”。这里不能加硬清洗，否则会破坏真实终端行为；正确边界是用终端模拟器解释控制序列，并在清空时调用 xterm buffer clear/reset，而不是清 React 文本状态。

### 31.3 不变量

- 这个终端是 human-operated UI，不加入 agent tool registry，不出现在模型 tool schema，不绕过 `sandbox_exec` 的模型执行边界。
- 用户终端可以执行宿主机命令，因此 UI 文案必须把它标成 Host terminal，避免和 Docker sandbox 混淆。
- 终端连接打开后保持常开；切回文件视图、关闭面板只是隐藏 UI，不关闭 WebSocket。
- 前端不要用普通文本节点显示 PTY 字节流，也不要硬过滤 `installed_matches` 式地清洗局部尾巴；真实终端输出必须交给 xterm 解释。
- 生产部署如果前端不直连后端，需要反向代理支持 `/api/terminal/ws` WebSocket upgrade。

### 31.4 验证

- `tests/test_terminal.py::test_terminal_helpers_resolve_cwd_and_shell_command` 覆盖 cwd/shell helper。
- `tests/test_terminal.py::test_terminal_input_message_writes_to_fd` 覆盖 input 消息写入 fd。
- `tests/test_terminal.py::test_terminal_websocket_sends_ready_event` 覆盖 FastAPI WebSocket ready 事件。

## 32. 迭代 24(2026-07-03~04):审计驱动的全库大扫除+真机链路验证

> 背景:对话 13a9eb55 派出的 4 个审计 subagent 多数死于 429,其发现由跨系统会话
> 从过程记录法证式重建为 `SUBAGENT_AUDIT_REPORT_20260703.md`,随后按批次实施。
> 本节按"问题→机制→修法→验证"记录 harness 相关的全部变更。进度与断点见
> `HANDOFF_CROSS_SESSION_20260703.md`。

### 32.1 P0×2:摘要哨兵泄漏 + llm_input_messages 冻结(e0b1c55)

- **机制1**:summarize 节点把官方 SummarizationMiddleware 返回的
  `[RemoveMessage(REMOVE_ALL_MESSAGES), *summary, *preserved]` 原样写进
  `llm_input_messages`。该哨兵协议只有 `messages` 通道的 add_messages reducer 懂;
  `llm_input_messages` 是普通 last-write 通道,agent 节点原样喂模型 →
  OpenAI 兼容序列化(含 DeepSeek)遇 RemoveMessage 抛 TypeError,摘要一触发即崩。
  离线测试全用 fake model 不做消息格式转换,所以从未暴露。
- **机制2**:`llm_input_messages` 只在 dangling 修复/摘要触发时偶发写入,无人清理,
  且被 checkpoint 持久化;一旦置位,agent 永远读旧快照,新工具结果与新用户消息
  全部对模型隐身(官方 pre_model_hook 约定是每步发射,这里只偶发写)。
- **修法**:pre_model 每步从 messages 重算投影(todo 提醒同帧并入);summarize
  写 llm_input_messages 前剥离哨兵。回归测试 2 例,先对修复前代码验证为红。

### 32.2 记忆链路 LLM 化(d6c1056 + 0e216d4 增强)

- 决策(用户委托):前置澄清已是小模型(run_triage),正则只作省调用快通道——保持;
  检索不加阻塞式前置判定(为省几百 token 注入多花一次首 token 前 LLM 调用不划算),
  保持"top-5 相关记忆注入 + memory_list/search 工具自取";保存侧 LLM 是唯一语义
  改写者。
- store 删除整个正则语义改写层(约120行:字段化/强加前缀/抢主语/抽生日)——它们会把
  memory_save 工具与后台抽取器里 LLM 已写好的内容再揉一遍("用户是控制工程硕士"
  被揉成"用户资料:是控制工程硕士。")。normalize 收窄为剥指令前缀+压空白+补句号+限长。
- 显式"请记住X":正则只负责探测显式指令,内容交小模型改写(可拆多条,首条携带
  source_run_id 去重键);模型不可用回退存剥前缀原文,显式请求绝不丢失;显式保存
  发生时跳过同轮后台抽取防近似重复。真机验证:extraction=llm_rewrite,
  "请记住:我喜欢简洁的中文回答。"→"用户喜欢简洁的中文回答。"
- (并行会话增强)抽取提示词禁止保存"对助手的指令/内部工具名",后置
  _INSTRUCTION_MARKERS 硬过滤,防测试指令串台进长期记忆。

### 32.3 tool.status 的 live 真相(ed39d9b + 08679ae)

- 探针发现 artifact_write 执行对前端完全隐形;推广 tool_status_event_from_tool_call
  到全部工具后 live 依旧为零 → 逐层排查:**v3 顶层 run_stream.tool_calls 投影通道
  在 live 从不产出**(既往 sandbox_exec 的"Docker 启动中"提示在 live 也从未真正
  出现过;单测走的是注入该通道的假流)。
- 修法:工具调用的 live 唯一来源是每条消息的 `.tool_calls` 子投影
  (AsyncChatModelStream typed projection)。typed_message_delta_channels 增加第三路泵,
  messages 分支拦截合成 tool.status;ToolCallChunk 逐分片产出(一次调用几十片),
  按"每条消息每工具名一次"去重;ask_clarification/write_todos 有专属 UI 跳过。
  顶层通道分支保留兼容测试注入。
- FE 配套:工具后正文恢复流式时清除滞留 running 芯片(此前会挂到 run 结束)。

### 32.4 Docker 沙箱:本机根因与持久容器重设计(0e216d4 + 环境)

- **用户实测"agent 硬是用不了 docker 也装不了"的根因链**:本 WSL 无 systemd
  (PID1 非 systemd)→ docker.service enabled 但永远没人拉起 → check 提示
  `sudo systemctl enable --now docker` 在此环境死路 → install 流程同样卡在
  systemctl 步骤;外加 Docker Hub 直连超时(网络),镜像也拉不动。
- **代码**:DockerEngineSetup 新增 ensure_daemon()(systemctl→service→直接
  `sudo -n nohup dockerd` 三级回退+就绪轮询),check 在"已装未跑"时自动尝试拉起,
  新增 action="start";LazyDockerSandbox 重写为**持久具名共享容器**
  `slotflow-sandbox-<workspace哈希>`:无 --rm、sleep infinity、空闲只 stop 不 rm
  (内容/已装依赖跨对话保留,磁盘最省),stopped→docker start 秒级复用,
  守护进程不可达时先 ensure_daemon 再重试一次;线程隔离:exec 锁定
  `-w /workspace/work/<thread>` 并注入 HOME 与 SLOTFLOW_THREAD_ARTIFACTS,
  防对话串台(用户确认目录级隔离足够)。
- **环境(本机一次性)**:/etc/wsl.conf 启用 [boot] systemd=true(重启后守护进程
  自启);/etc/docker/daemon.json 配置三个国内 registry mirror;python:3.12 预拉取。
- 真机:真实容器测试通过;探针沙箱轮 `python -c "print(6*7)"` → 42 回传 ✓。

### 32.5 流式正文两处真机踩坑(a008ca3,WSL 会话)

- 纯 reasoning 消息(模型把回答全写进思考、正文为空)曾被 repr 成
  `[{type: reasoning, ...}]` 直接当回复展示 → normalize_message_content
  的正文通道禁止 repr 兜底,抽不出文本返回空串。
- `<slotflow-todo-reminder>` 等内部注入块被模型复读进回复 →
  strip_slotflow_context_blocks 在正文通道剥内部标签块(routes 持久化侧同剥)。

### 32.6 真机探针方法论(scratch/harness/probe_full_chain.py)

- 模拟前端每步:目录→建线程→SSE 逐帧(事件名契约/唯一 run.finished/finished 后
  无事件/prepared 最前)→流式与落库一致性(前缀关系)→思考不漏正文→标题→多轮
  可见上一轮→显式记忆(llm_rewrite)→artifact 工具链(文件落盘+可读)→沙箱
  (容器执行回传)→澄清门(触发/选项/选择后完成)。**46/46 全 PASS**。
- 已知误报陷阱:workspace 列表接口是逐层目录需下钻;模型目录按 providers[].models。

### 32.7 并行会话事故与规约

- WSL 侧续起的会话(6c2f006b)与 Windows 会话同时写同一棵树:它把对方未提交的
  沙箱改动连同自己的增强提交(0e216d4/a008ca3),随后另一方基于陈旧内容的写入
  又踩掉其增强;靠 `git checkout HEAD -- <files>` + 关键符号核验恢复共存。
- **规约:同一时间只允许一个会话写这棵树**;交接一律经
  HANDOFF_CROSS_SESSION_*.md,接手前先 `git log` 对时间线。

## 33. 迭代 25(2026-07-06):subagent 三层角色库 + todo/Skills/工作区前端修正

### 33.1 背景与设计目标

用户要求把 subagent 提示词借鉴 `https://github.com/msitarzewski/agency-agents`,
但不能把 150+ 具体角色全部暴露给父模型。原因很明确:父模型如果一次看到全部角色
提示词,上下文会被提示词库吞掉,角色选择反而变差。目标是保留 SlotFlow 现有 6 个
功能型 subagent,新增 6-8 个领域分类层,具体角色提示词文件化存储,只在真正委派给
子 agent 时按需加载一个角色。

### 33.2 代码实况(已按仓库规约读代码核对)

- Layer 1 功能角色仍来自 `backend/app/harness/subagents/config.py` 的
  `DEFAULT_SUBAGENT_PROFILES`: `researcher`, `analyst`, `planner`, `coder`,
  `reviewer`, `writer`。这是主模型选择 `task_tool(agent_name=...)` 的第一层。
- Layer 2 领域分类由 `backend/app/harness/subagents/role_catalog.py` 的
  `DEFAULT_ROLE_DOMAINS` 定义: `engineering`, `design`, `finance`, `market`,
  `sales`, `product`, `research`, `specialized`。每个 domain 只包含描述、
  division 列表和 sample role metadata。
- Layer 3 具体角色提示词来自
  `backend/app/harness/subagents/agency_agents/roles/*/*.md`。本次从
  `msitarzewski/agency-agents` 本地克隆复制,保留 upstream `LICENSE` 与
  `divisions.json`;当前 `find ... -maxdepth 2 -name '*.md'` 计数为 220。
- `SubagentRoleCatalog.domains()` 只返回 domain 摘要、role_count 和前 5 个
  sample_roles,不返回 `prompt` 字段。父模型通过 `subagent_list` 看到的是
  compact metadata,不是完整提示词库。
- `SubagentRoleCatalog.resolve(domain, role_name, task, context, expected_output)` 在
  `task_tool` 执行时解析一个角色。显式 `role_name` 优先;没有 `role_name` 但有
  `domain` 时,用 task/context/output 的词项对 domain 内角色做轻量匹配。单个角色
  prompt 最多注入 `MAX_ROLE_TEMPLATE_CHARS=12000`。
- `backend/app/harness/subagents/tools.py::build_subagent_system_prompt` 仅对子 agent
  系统提示词追加 `<slotflow-agency-role>` 块,包含 id/name/domain/division/path/
  description 和具体 prompt。父图不读也不总结完整角色库。
- `backend/app/harness/builder.py` 的 `<slotflow-operating-procedure>` 现在明确告诉
  主模型:需要委派且 role fit 重要时先调用 `subagent_list`,选择 Layer 1 的
  `agent_name`,再按需把 `domain` 和 `role_name` 传给 `task_tool`;不要要求查看或总结
  全量角色库。

### 33.3 为什么这样放边界

这个设计把“角色选择”和“角色执行”拆开:

1. 父模型只需要足够信息来决定是否委派、委派给哪个功能角色、是否需要领域角色。
2. 具体行业/岗位提示词只影响被委派的子 agent,不会污染父模型的全局行为。
3. `subagent_list` 是发现/选择工具,`task_tool` 是加载/执行边界;这与现有
   `task_tool` 子图隔离、`subagent_limit` 并发上限兼容。
4. 即使 agency-agents 后续继续扩展,父模型上下文仍稳定,因为暴露面只随 domain
   摘要和少量 sample metadata 增长。

这不是主图并行 subagent 的最终形态;`task_tool` 仍是工具调用边界,§Roadmap 的
`Send`+`merge` 主图并行分支仍可后续推进。当前改动先解决“如何选对专业角色且不把
提示词库塞爆上下文”的问题。

### 33.4 相关前端交互修正

- Skills 目录删除硬编码推荐 Skills 区域。`directory-modal.tsx` 不再包含
  `RECOMMENDED_SKILLS` / `RecommendedSkillsSection`;空状态只提示用右上角安装或上传。
- `ComposerTodoPanel` 的自动展开改为只看 `todoListKey`。`use-chat-stream.ts`
  继续用完整 todo signature 去重状态更新,但 `todoListKey` 只由 todo content 列表
  生成;因此 pending/in_progress/completed 的状态推进不会把用户手动折叠的面板重新
  弹开。只有内容列表真正变化(等价于新 todo list)才自动展开。
- 右侧工作区 reader/preview 扩展了常见格式: `.tsx/.jsx/.css/.sql/.graphql/.svg`
  等源码文本,`.drawio`,`.xlsx/.xlsm`,`.pptx`;旧二进制 `.xls/.ppt` 只给 media type
  和 unsupported-binary 说明,不做脆弱解析。
- 前端视觉层做了轻量 polish:主工作区背景层次、composer/消息/工作区面板/目录卡片
  的短动效和 hover 反馈。动效通过 `prefers-reduced-motion` 降级。

### 33.5 当前验证结果

- `cd backend && uv run pytest tests/test_harness_subagents.py tests/test_harness_tools.py -q`
  → 20 passed。
- `cd frontend && pnpm typecheck` → passed。
- `python3 -m py_compile backend/app/harness/sandbox/readers.py
  backend/app/harness/subagents/role_catalog.py backend/app/harness/subagents/tools.py`
  → passed。

尚未做 live 模型探针:本轮改动的 subagent 角色选择依赖模型主动调用 `subagent_list`
和 `task_tool(domain, role_name)`,离线测试覆盖了工具输出、角色解析和子 agent prompt
注入,但真实模型是否稳定选择合适 role 仍需前端人工验证或后续 live probe。

## 34. 迭代 26(2026-07-06):subagent 三层架构审查 + Docker 产物发布 + 新消息顶部锚定

### 34.1 三层 subagent 架构审查结论

用户追问“三层 subagents 架构是不是最优,主 agent 会不会被搞晕”。按当前代码核对后,
结论是:三层方向是对的,但初版还缺一个减压阀。

保留三层的理由:

1. Layer 1 仍是 6 个功能型 profile(`researcher/analyst/planner/coder/reviewer/writer`),
   主模型先按任务形态选 worker,不需要先理解 220 个行业角色。
2. Layer 2 只有 8 个 domain,主模型看到的是 compact metadata,不是完整 prompt body。
3. Layer 3 的 agency-agents markdown 只在 `task_tool` 子图执行时加载一个角色模板,不会污染
   父模型的长期行为或占用父模型上下文。

初版风险:

- `subagent_list` 每个 domain 暴露前 5 个 sample role。这个数量不大,但 sample role 不是
  搜索接口;主模型如果没看到想要的角色,可能猜 `role_name`,也可能因为中文任务关键词和英文
  role metadata 不匹配而不传 Layer 3。
- 自动 `resolve(domain, task, context, expected_output)` 只做轻量英文词项匹配。中文任务
  只传 domain 时可能选不到具体 role。选不到 role 不会坏掉,但会退化成“功能 profile +
  domain 文字”,专业角色增益变弱。

本轮修正:

- 新增 `SubagentRoleCatalog.search(query, domain, max_results)`,只返回 bounded metadata,
  不读取/返回 prompt body。
- 新增 `subagent_role_search` 工具。主模型需要精确职业角色时,先用 `subagent_list` 选 Layer 1/2,
  再用 `subagent_role_search(query, domain)` 获取 1-20 个候选,最后把一个 `role_name`/id 传给
  `task_tool`。
- 搜索 query 无命中时回退到该 domain 的稳定短名单,避免中文/非英文任务返回空列表,让主模型仍有
  可选候选。
- `harness/builder.py` 的 operating procedure 收紧为:通常只传 Layer 2 `domain`;只有精确角色
  真重要时才调用 `subagent_role_search`。这比让主模型在 `subagent_list` 的 samples 里硬猜更稳。

判断:这个架构不是最终的“最优”形态,但在当前 `task_tool` 子图边界下是合理的局部最优。真正的下一步
不是继续给父模型塞更多角色信息,而是做确定性 role router 或主图级 `Send(subagent)×N` 并行分支。
当前版本不会因为 220 个角色被搞晕,因为父模型看不到 220 个 prompt;剩余风险是模型是否主动调用
`subagent_role_search`,这仍是 prompt/tool-use 软行为,需要 live 探针或前端人工验证。

### 34.2 Docker 产物发布工具

用户要求“加上能把 docker 中产生的文件复制到产物中的工具”。代码边界核对:

- `LazyDockerSandbox` 已把 `/workspace/artifacts` bind mount 到 host workspace 的 `artifacts/`,
  所以模型如果一开始就写 `/workspace/artifacts/<thread>/...`,文件已经在产物面板可发现。
- 真正缺口是脚本经常在当前 scratch workdir(`/workspace/work/<thread>`)或 `/tmp` 里先生成二进制/
  图表/中间文件,之后没有受控方式把单个文件发布到当前线程的 artifact folder。

本轮新增:

- `LazyDockerSandbox.copy_to_artifacts(source_path, artifact_path, overwrite)`:
  - `source_path` 允许相对当前线程 workdir、`/workspace/work/<thread>`、`/tmp`、或当前线程 artifact
    folder。
  - `artifact_path` 只能落到当前线程 artifact folder;绝对路径也必须在当前线程
    `/workspace/artifacts/<thread>` 内。
  - 只复制单个文件,不复制目录。
  - 用 `max_write_bytes` 做大小上限。
  - 默认拒绝覆盖,除非 `overwrite=true`。
  - 复制在容器内通过 `docker exec ... sh -lc 'cp ...'` 完成,不引入宿主机 shell 写工具。
- `build_sandbox_tools` 暴露 `sandbox_artifact_copy`。它和 `sandbox_exec` 一样在 Docker 不可用时返回
  结构化 JSON 错误,不让 graph 崩。
- `tool_status_event_from_tool_call` 增加 `sandbox_artifact_copy` 的 `tool.status`,前端能看到
  “正在发布 Docker 文件到产物”。
- `harness/builder.py` 系统提示更新:直接文本/内容产物仍用 `artifact_write`;Docker 内已经生成的文件
  用 `sandbox_artifact_copy` 发布到产物面板。

安全边界:这不是宿主机 copy 工具,也不是任意 docker cp。源路径被限制在容器内的当前线程 workdir、
`/tmp` 或当前线程 artifacts;目标被限制在当前线程 artifact folder。继续禁止模型使用 host
shell/terminal/MCP execution tools。

### 34.3 新消息顶部锚定

用户要求“当用户发送一个新消息时,用户的消息不要显示到最下面了,直接移到最上面”。

根因在前端滚动策略:旧 `message-list.tsx` 在 assistant 首个 token 出现时调用
`scrollViewportToBottom("smooth")`,之后如果 near-bottom 就继续 auto-follow。这样新 user bubble
通常出现在底部附近,assistant 输出继续把视口拉到末尾,不符合“新一轮从问题往下读”的体验。

本轮改法:

- 保持消息数组时间顺序,不重排消息。
- 新增 `scrollUserMessageToTurnTop`,在发送后流式期间把最新 user bubble 锚到 viewport 顶部附近。
- 首个 assistant token 不再无条件滚到底部;如果有最新 user message,继续锚 user bubble。
- 流式期间加一个临时底部 spacer,让“最新 user message 接近顶部”在 assistant 内容还很短时也成立。
  `scrollViewportToBottom` 改为滚到真实 `messagesEndRef`,不是滚到 spacer 末端,避免底部空白影响跳转。
- 用户手动滚动后,`userScrollIntentRef` 会阻止自动锚定刷新,避免生成完成时强行抢滚动。

### 34.4 当前验证结果

- `cd backend && uv run pytest tests/test_harness_sandbox.py tests/test_harness_subagents.py
  tests/test_tool_registry.py tests/test_harness_tools.py tests/test_harness_builder.py
  tests/test_agent_adapter.py -q` → 98 passed。
- `cd backend && uv run ruff check app tests` → passed。
- `cd frontend && pnpm typecheck` → passed。
- 真实 Docker 探测:在临时 workspace 中用 `sandbox_exec` 生成 `docker-output.txt`,再用
  `copy_to_artifacts(source_path="docker-output.txt", artifact_path="probe/docker-output.txt")`
  发布;结果 `copy_ok=true`,host 文件
  `artifacts/copy-probe/probe/docker-output.txt` 存在且内容为 `artifact from docker`。

仍需人工验证:前端新 turn 锚定属于视觉交互,单元/类型测试只能保证代码路径和类型正确,不能替代在浏览器里
确认“用户消息出现在顶部、assistant 从下面长出来、手动滚动不被抢回”。

## 35. 迭代 27(2026-07-07):fresh clone bootstrap / Docker hardening / env template

### 35.1 背景

用户要求最后检查 `bootstrap.sh`:新 clone 仓库的人不管 Linux 版本如何,最好运行一次就能把
SlotFlow 所有环境配好,尤其 Docker 不要脆弱;同时新增一个可提交的 `.env_example`,列出所有可配置
功能,默认开启功能,但模型 URL/API key 用注释给新用户填写。

这不是模型推理链路的算法改造,但它直接影响两个 harness 边界:

1. `sandbox_exec` / `sandbox_artifact_copy` 的宿主机前置依赖是 Docker Engine。如果 bootstrap 没把 Docker
   装好或启动好,模型会在第一轮真实代码执行时失败。
2. `docker_engine_setup` 是模型唯一允许触碰宿主机 Docker setup 的受控工具。它的安装脚本和
   `bootstrap.sh` 必须同一套支持范围,否则“首启脚本能装”和“运行时补救工具能装”会分叉。

### 35.2 bootstrap.sh 代码实况

按代码核对后,`bootstrap.sh` 现在的顺序是:

1. 安装/验证 Makefile 依赖:`make`, `curl`, `git`, Python/build tools, `fuser`/`psmisc`。
   支持 apt/dnf/yum/pacman/apk/zypper;Homebrew 是非 Linux 便利分支。
2. 安装 `uv`。
3. 从 `frontend/package.json` 的 `packageManager` 解析 pnpm 版本,安装 Node 22 + pnpm;已有 Node>=20 时只补
   pnpm,否则用 Volta。
4. `backend/uv sync` 与 `frontend/pnpm install --frozen-lockfile`。
5. 如果 `backend/.env` 不存在,复制 `backend/.env_example`;已存在则不覆盖,避免覆盖本机密钥。
6. Docker setup:
   - Docker CLI 不存在时按 apt/dnf/yum/pacman/apk/zypper 安装 Docker Engine。compose 包不是
     SlotFlow 的硬依赖,所以 apt/dnf/yum/apk/zypper 分支都有“带 compose → 只装 docker”的 fallback。
   - 将当前用户加入 `docker` 组。这里允许交互 sudo,因为用户正在主动运行 bootstrap;但脚本仍明确提示
     需要重新登录后非 sudo Docker 才对后端进程生效。
   - WSL 且 PID1 非 systemd 时,编辑 `/etc/wsl.conf` 设置 `[boot] systemd=true`。如果 `[boot]` 已存在但没有
     systemd,Python 分支会合并写入,不是简单追加重复段。
   - 启动 daemon 的回退链路是 systemctl → service → rc-service → `nohup dockerd`。daemon 探测/启动用
     `sudo -n` 非交互,避免后台路径卡住;需要权限时失败并打印明确 warning。
   - 预拉镜像时优先当前用户 `docker`,只有当前用户不能访问 daemon 时才走 root 通道。
   - 直连 Docker Hub 拉取失败后才写 registry mirrors;写入时若已有 `registry-mirrors` 则不改。没有 mirrors 时
     先备份现有 `/etc/docker/daemon.json`,再用 Python 合并 JSON,避免覆盖用户已有 Docker daemon 配置。

无法承诺“任何 Linux 100% 自动成功”的原因也写进文档:企业镜像源、无 sudo、rootless Docker、Docker Desktop
外部 daemon、发行版改包名、被防火墙阻断 Docker Hub 等都不是仓库脚本能完全控制的状态。现在的目标是:
常见 Linux/WSL 自动配置;不能自动配置时失败清楚,并让运行时 `docker_engine_setup` 返回同一类固定安装脚本。

### 35.3 docker_engine_setup 同步

`backend/app/harness/sandbox/docker_engine.py` 同步扩展:

- `SUPPORTED_INSTALL_MANAGERS = {apt, dnf, yum, pacman, apk, zypper}`。
- `/etc/os-release` 识别 Debian/Ubuntu/Linux Mint/Pop/Raspbian、Fedora、RHEL/CentOS/Amazon Linux、Rocky/Alma,
  Arch/Manjaro/EndeavourOS、Alpine、openSUSE/SLES。RHEL-like 机器优先检查实际是否有 `dnf`,否则用 `yum`。
- `ensure_daemon()` 的启动链路与 bootstrap 一致:systemctl → service → rc-service → direct dockerd。非 root 时
  用 `sudo -n`,root 进程不再硬编码 sudo。
- install script 和自动 install 都只执行固定命令,仍没有任意 shell/script 参数。`install` 仍必须同时满足
  `SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=true` 和 `confirm_host_install=true`,且只应在用户明确要求安装 Docker
  Engine 后调用。

安全边界不变:`docker_engine_setup` 只处理 Docker Engine 前置依赖,不成为通用宿主机 shell。模型运行代码、
装包、执行 Skill helper 仍只能用 `sandbox_exec`;已生成文件发布到产物面板仍用 `sandbox_artifact_copy`。

### 35.4 `.env_example` 与新增 env flags

新增 `backend/.env_example`,与 `make dev` 的后端启动路径一致。内容包括:

- provider key/base URL:DeepSeek/OpenAI/Anthropic/custom relay 都写出,但 API key 与 base URL 默认注释。
- discovery/runtime provider knobs:`CUSTOM_MODELS`, `CUSTOM_VALIDATE_MODELS`, `SLOTFLOW_RELAY_USER_AGENT`。
- FastAPI/frontend URL 说明。Next.js 不自动读取 `backend/.env`,所以 frontend-only 变量只作为注释说明。
- chat/checkpointer/system prompt/storage 路径。
- harness feature flags,默认全部开启。
- skills/memory/MCP/workspace/network/Docker sandbox/terminal/title/bootstrap knobs。

为了让这个示例不是“写了但代码不读”,本轮补了 `load_middleware_config_from_env()` 的缺口:

- `SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION` → `proactive_memory_extraction_enabled`
- `SLOTFLOW_CLARIFY_GATE` → `clarify_gate_enabled`
- `SLOTFLOW_SUBAGENT_LIMIT` → `subagent_limit_enabled`
- `SLOTFLOW_SUBAGENT_MAX_CONCURRENT` → `subagent_max_concurrent`

这些变量只改变已有 graph 行为开关,不新增新节点或新工具。默认值仍与 dataclass 默认一致:主动记忆抽取开,
澄清门开,subagent 并发限制开,并发上限 3。

### 35.5 验证

本轮验证:

- `bash -n bootstrap.sh` → passed。
- `SLOTFLOW_SKIP_SYSTEM_PACKAGES=1 SLOTFLOW_SKIP_DOCKER=1 ./bootstrap.sh` → passed,不覆盖既有
  `backend/.env`。
- `cd backend && uv run pytest tests/test_harness_sandbox.py tests/test_runtime.py -q` → 47 passed。
- `cd backend && uv run ruff check app tests` → passed。
- `make verify` → backend `332 passed, 1 skipped`, frontend `pnpm typecheck` passed, frontend
  `pnpm build` passed。
- `git diff --check` → passed。

局部测试覆盖点:

- Docker 安装脚本仍返回受控固定命令。
- Fedora/dnf、Alpine/apk、openSUSE/zypper、CentOS/yum 分支可生成 install script。
- runtime `.env` 中新增 harness flags 能映射到 `SlotFlowMiddlewareConfig`。

真实新机器 Docker 安装不可在当前已配置工作站完整模拟,所以 bootstrap 的跨发行版覆盖主要依赖语法检查、
分支单测、跳过系统包/Docker 的 bootstrap 跑通,以及对固定命令的代码审计。

## 36. 迭代 28(2026-07-07):web_search DuckDuckGo TLS 回退 + 新消息顶部锚定补强

### 36.1 web_search SSL 握手失败

用户复测 `web_search` 搜索 `"China economy"` 仍返回 SSL 握手失败。按当前代码核对,根因不是工具没注册,
而是 `harness/tools/network.py::search_web` 只有一个搜索入口:
`https://lite.duckduckgo.com/lite/?q=...`。在本机真实网络下复现结果:

- `https://lite.duckduckgo.com/lite/?q=China+economy` -> `UNEXPECTED_EOF_WHILE_READING`
- `https://html.duckduckgo.com/html/?q=China+economy` -> 同样握手失败
- `https://duckduckgo.com/html/?q=China+economy` -> 同样握手失败
- `https://www.bing.com/search?q=China+economy` -> 200,可返回 HTML

所以这不是模型调用方式问题,也不是证书校验开关应该默认放松的问题。默认禁用 TLS verify 会降低
`web_fetch`/`web_search` 的安全边界,且不能解释为什么 Bing 正常。修复方向是让搜索工具具备搜索源回退。

本轮代码改动:

- `SEARCH_PROVIDERS` 替代单个 `SEARCH_URL`,顺序为 Bing HTML -> DuckDuckGo Lite。
- `search_web(...)` 对每个 provider 调 `fetch_url(..., include_raw=True)`,第一个返回可解析结果的 provider
  直接返回;失败或无结果时继续尝试下一个 provider。
- 所有 provider 都失败时,返回 `attempts=[{provider,error},...]`,让模型/调试者能看到具体是哪一层失败。
- `extract_search_results` 先抓常见结果标题块 `<h2><a ...>`,再退回普通 `<a>` 扫描。
- `normalize_search_result_url` 继续解 DuckDuckGo `/l/?uddg=...`,并新增 Bing `/ck/a?u=...` 解码。Bing 的
  `u=` 常见形态是 `a1` 前缀 + URL-safe base64;解码后返回真实目标 URL,不把 Bing 跳转 URL 暴露给模型。
- 解析结果会过滤 Bing/DuckDuckGo 自身导航页,避免把 Images/Videos/News 等搜索引擎内部入口当成答案来源。

这保持了网络工具的原有安全边界:`fetch_url` 仍只允许 HTTP/HTTPS,默认阻断私网/localhost,仍走
`SlotFlowSandboxConfig` 的超时和最大字节限制。搜索源回退只改变公共搜索入口和 HTML 解析,没有引入
任意代理、无证书校验或宿主机命令。

### 36.2 新消息没有显示到屏幕最上面

上一轮已经把新 turn 从“跟随底部”改为“锚定最新 user bubble”,但用户复测仍看到新消息在底部。再次读
`frontend/src/components/chat/message-list.tsx` 后,实际根因是滚动几何条件不满足:

- 旧代码只加了固定 `h-[58vh]` 底部 spacer。
- 当 assistant 输出还很短、最新 user bubble 高度较小、视口较高时,`scrollHeight - clientHeight`
  的最大滚动值不够大,浏览器根本不允许把最新 user bubble 滚到视口顶部。
- 另外,程序化 `scrollTo` 触发的 scroll 事件会进入同一套 near-bottom/manual intent 判定,后续流式输出有机会
  把视口重新带回底部。

本轮补强:

- 底部 spacer 改为按真实 viewport 高度与最新 user bubble 高度计算:
  `viewport.clientHeight - userBubbleHeight + 24`。这样即使 assistant 尚未输出,最大滚动距离也足够把
  user bubble 放到顶部附近。
- spacer 只在 `isStreaming && latestUserMessageId` 时存在,并且不再做 height 过渡。这里要优先保证锚定正确,
  否则动画期间 `maxScrollTop` 仍不足。
- `scrollUserMessageToTurnTop` 连续两帧校准,第一帧处理新 DOM,第二帧处理 spacer/内容高度变化后的最终几何。
  新 turn 首次锚定用 `behavior="auto"`,避免用户先看到消息停在底部再平滑滑到顶部。
- 新增 `programmaticScrollUntilRef`,程序化滚动后的短窗口内忽略 scroll 事件对手动滚动意图的影响,避免
  自己的 `scrollTo` 把 auto-follow 状态写坏。
- assistant 首 token 到来时,如果存在最新 user message,继续锚定 user bubble,不滚到底部。

消息数组仍保持时间顺序,没有把 DOM 顺序反转;只是滚动视口位置改变。这符合用户说的“新发送的消息显示到
屏幕最上面”:屏幕从本轮问题开始往下读,assistant 从下面长出来。

### 36.3 验证

本轮验证:

- `cd backend && uv run pytest tests/test_network_tools.py tests/test_tool_registry.py tests/test_harness_builder.py -q`
  -> 19 passed。
- `cd backend && uv run ruff check app tests` -> passed。
- `cd frontend && pnpm typecheck` -> passed。
- `cd frontend && pnpm build` -> passed。
- `git diff --check` -> passed。
- 真实 `web_search("China economy")` 调用 -> 返回 5 条结果,`provider="bing"`,不再出现 DuckDuckGo SSL
  握手错误。

尝试补 Playwright 浏览器验证:用临时 spec 拦截聊天 API/SSE,但本仓库未安装 `@playwright/test`,
`pnpm dlx playwright` / `pnpm dlx --package @playwright/test playwright test ...` 都无法让项目内 spec
解析 `@playwright/test` import。没有把 Playwright 依赖加入仓库,临时 spec 和 `test-results` 已删除。仍需
人工在浏览器里确认:实际发送新消息后,最新用户消息应立即贴近聊天视口顶部,assistant 内容从其下方流出;
用户手动滚动后不应被完成态强行拉回。

## 37. 迭代 29(2026-07-09):docx 产物预览 500 根因修复

用户反馈最新对话产物里的 `.docx` 打不开,右侧产物面板显示 `read artifact failed: 500`。按当前代码链路复核:
前端预览调用 `GET /api/workspace/artifacts/read`,后端 `workspace/routes.py::read_artifact` 只做路径白名单
(`artifacts/` / `uploads/`)后调用 `SlotFlowWorkspace.read_file`,真实解析在
`harness/sandbox/readers.py::read_workspace_file` / `extract_docx_text`。

本机最新产物是
`backend/.slotflow/workspace/artifacts/thread_5b41a6b1208a/Nature_Review_End-to-End_Autonomous_Driving.docx`,
大小 `1,355,271` bytes。直接调用 `extract_docx_text(path)` 可以成功抽出 `12,612` 字符,并且
`zipfile.ZipFile(...).testzip()` 返回 `None`,说明 `word/document.xml` 和 ZIP 包本身不是导致预览 500 的根因。
真正失败点是 `SlotFlowWorkspace.read_file(...)` 在解析格式前先调用 `_assert_readable_file`,而默认
`SLOTFLOW_WORKSPACE_MAX_READ_BYTES` / `DEFAULT_MAX_READ_BYTES` 是 `1,048,576` bytes。这个含图片 docx 的包体
超过 1 MiB 后抛 `WorkspaceFileTooLargeError`;路由之前只捕获 `WorkspacePathError`,所以 FastAPI 把它表现成
500,前端只看到泛化的 `read artifact failed: 500`。

修复:

- `SlotFlowWorkspace.read_file` 先通过 `detect_workspace_file_extension` 判断文件类型,再做大小检查。
- `.docx` / `.xlsx` / `.xlsm` / `.pptx` 这类 Office Open XML ZIP 包使用
  `OPENXML_PREVIEW_MAX_READ_BYTES = 25 * 1024 * 1024` 与用户配置的 `max_read_bytes` 取较大值作为预览包体上限。
  原因是这类生成报告经常因为嵌入图片超过普通文本 1 MiB 限额,但预览 reader 只读取 XML 文本部件,不是把图片
  二进制内联给模型或前端。
- `read_text()` 和普通文本/Markdown/源码预览仍受 `SLOTFLOW_WORKSPACE_MAX_READ_BYTES` 控制;没有放宽任意文本读取。
- `workspace/routes.py::read_artifact` 现在捕获 `WorkspaceFileTooLargeError` 并返回 HTTP 413,让真正超限的文件成为
  可理解的客户端错误,不再变成 500。

验证:

- 真实产物走修复后的 `SlotFlowWorkspace.read_file('artifacts/thread_5b41a6b1208a/Nature_Review_End-to-End_Autonomous_Driving.docx')`
  返回 `kind=document`,media type 为
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`,size `1,355,271`,content 长度 `12,612`,warning `None`。
- 新增回归测试 `test_read_artifact_previews_large_docx_with_embedded_media`:构造一个超过 1 MiB、带 `word/media/image1.png`
  的 docx,`/api/workspace/artifacts/read` 返回 200 且抽出文档文本。
- 新增回归测试 `test_read_artifact_returns_413_for_oversized_plain_text`:超过 1 MiB 的 Markdown 预览返回 413,证明普通文本限额
  没有被绕开且不再 500。
- `cd backend && uv run pytest -q tests/test_uploads.py::test_read_artifact_previews_large_docx_with_embedded_media tests/test_uploads.py::test_read_artifact_returns_413_for_oversized_plain_text tests/test_harness_tools.py::test_workspace_read_extracts_docx_pdf_and_image_metadata`
  -> 3 passed。

注意:当前环境没有 `python-docx` / LibreOffice,所以本轮验证的是 SlotFlow 产物面板预览链路与 ZIP/XML 结构校验,不是完整
Word/Office 渲染兼容性测试。如果用户下载后仍被桌面 Office 拒绝打开,下一步应单独审查生成器写入的 OOXML relationship、
content types、图片 MIME/尺寸与 Word 严格兼容性。

## 38. 迭代 30(2026-07-09):DeepSeek thinking 工具循环必须回传 reasoning_content

用户在长任务“生成到一半”时前端报错:

`Error code: 400 - {'error': {'message': 'The `reasoning_content` in the thinking mode must be passed back to the API.', 'type': 'invalid_request_error', ...}}`

这类错误发生在 DeepSeek thinking mode 的多步 ReAct 链路里:第一轮模型返回 assistant 消息和 tool call,SlotFlow
执行工具后要把 `assistant(tool_calls=...) + ToolMessage(...)` 一起发回模型继续生成。DeepSeek 的 thinking mode
要求上一条 assistant 的 `reasoning_content` 也必须随 assistant 消息原样回传,否则下一次 `/chat/completions` 请求会被
400 拒绝。

代码核对后的真实机制:

- `langchain_deepseek.ChatDeepSeek._convert_chunk_to_generation_chunk` 会从流式
  `choices[].delta.reasoning_content` 解析出 `AIMessageChunk.additional_kwargs["reasoning_content"]`。
- SlotFlow 的 `_SlotFlowChatDeepSeek._convert_chunk_to_generation_chunk` 已经把它桥到
  `{"type":"reasoning","reasoning": ...}` content block,所以前端 reasoning 流能看到思考。
- `AIMessageChunk` 合并会把多段 `additional_kwargs["reasoning_content"]` 拼成完整字符串,所以 state 里的
  `AIMessage.additional_kwargs` 有足够信息。
- 但请求序列化走的是 `langchain_openai.chat_models.base._convert_message_to_dict`,它只写 OpenAI 标准字段
  `content` / `tool_calls` / `function_call` / `audio`,不会把 DeepSeek 私有的 `reasoning_content` 写回 payload。
- `langchain_deepseek.ChatDeepSeek._get_request_payload` 只把 assistant list content 压成字符串,也没有补
  `reasoning_content`。于是工具执行后的第二次模型请求丢字段,触发 DeepSeek 400。

修复在 `chat/runtime/models.py` 的 SlotFlow DeepSeek 子类里完成:

- 新增 `inject_reasoning_content_into_deepseek_payload(payload, messages)`。
- `_SlotFlowChatDeepSeek._get_request_payload(...)` 先调用父类构造 OpenAI-compatible payload,再用
  `self._convert_input(input_).to_messages()` 与 `payload["messages"]` 按顺序对齐。
- 对每个 source `AIMessage`,如果 `additional_kwargs["reasoning_content"]` 是非空字符串且 payload role 是
  `assistant`,就把同名字段注入 payload message。
- 这只影响实际携带 reasoning 的 assistant 消息;普通 DeepSeek flash / thinking off / custom relay 不产生该字段时不会额外写入。
  `custom` 仍不发送 DeepSeek 的 `extra_body.thinking` 开关,但如果某个 OpenAI-compatible relay 本身返回 DeepSeek-like
  `reasoning_content`,后续 tool loop 也能把它回传。

为什么不在 projection 层修:projection 只负责把 LangGraph v3 流映射成前端 `AgentEvent`,不参与下一轮 provider 请求。
真正丢字段的位置是 provider payload 序列化边界,所以必须在 `ChatDeepSeek` 子类的 `_get_request_payload` 修,否则
前端/状态快照/工具消息怎么改都只是症状补丁。

验证:

- 新增 `test_deepseek_payload_passes_back_reasoning_content_after_tool_call`:构造
  `HumanMessage -> AIMessage(tool_calls, additional_kwargs={"reasoning_content": ...}) -> ToolMessage`,直接调用
  `DeepSeekChatModel._get_request_payload(...)`,断言 assistant payload 同时包含 `tool_calls` 和原始
  `reasoning_content`。
- 保留并通过 `test_deepseek_chat_model_preserves_reasoning_stream_delta`,确认流式 delta 仍进入 v3 reasoning content block。
- `cd backend && UV_CACHE_DIR=../.slotflow/uv-cache uv run pytest -q tests/test_runtime.py::test_deepseek_chat_model_preserves_reasoning_stream_delta tests/test_runtime.py::test_deepseek_payload_passes_back_reasoning_content_after_tool_call`
  -> 2 passed。

仍需真实长任务人工复测:选择 DeepSeek pro/ultra 且 thinking on,让模型至少调用一次工具后继续生成;预期不再出现
`reasoning_content ... must be passed back` 400。如果 live API 仍报错,下一步要抓实际 payload,检查是否存在某个 LangGraph
节点替换/裁剪了 AIMessage 且没有保留 `additional_kwargs`。

## 39. 迭代 31(2026-07-09):async 边界审计第一阶段——路由 I/O 与长期记忆 SQLite 出线程池

用户要求审计“该 async 的地方是否异步”并逐批优化。第一、二批先处理不改变模型语义但会阻塞 FastAPI
事件循环的本地 I/O 边界。

### 39.1 路由层文件/CLI 操作

原状:FastAPI route 是 `async def`,但里面直接执行本地同步文件写入/读取/删除或 Skills CLI 安装:

- `uploads/routes.py::upload_file` 在 `await file.read(...)` 后直接 `SlotFlowUploadStore.save_upload`,后者写上传文件和
  metadata JSON。
- `workspace/routes.py::read_artifact` 直接 `SlotFlowWorkspace.read_file`,可能解析 `.docx`/`.xlsx`/`.pptx` ZIP、PDF、图片
  metadata;`list_artifacts`/`delete_artifact` 也直接碰文件系统。
- `skills/routes.py` 的 upload/install/update/reorder/delete 直接执行 `Path.write_bytes`,扫描 Skill 文件,
  `subprocess.run` 调 skills.sh CLI,以及 `shutil.rmtree`/`move`/`copytree` 类操作。

修复:这些 route 仍保持 async API,但慢的同步边界改走 `starlette.concurrency.run_in_threadpool`。这不是把底层 store
改成真正 async,而是把本地阻塞 I/O 从 event loop 让到 Starlette 线程池。补了 spy 测试确认关键边界确实经过线程池:

- `test_upload_file_persists_via_threadpool`
- `test_read_artifact_uses_threadpool_for_structured_preview`
- `test_install_skill_runs_registry_install_in_threadpool`

### 39.2 ChatRepository / MemoryStore SQLite

原状:`SQLiteChatRepository` 是同步 sqlite3,但已使用 `check_same_thread=False` + `RLock`,适合先以 threadpool 方式接入
async route,不用一次性重写成 `aiosqlite`。`SlotFlowMemoryStore` 也是同步 SQLite,并且长期记忆检索/显式保存处在
LangGraph async run 的 prepare/finalize 生命周期里。

修复:

- `chat/routes.py` 中 thread 创建/列表/搜索/读取/删除、stream run 初始化、SSE 完成/失败/取消时的 message/run 写入、
  标题生成前后的 repo 读取/更新都改为 `run_in_threadpool`。
- `memory/routes.py` 的 CRUD 改为 `run_in_threadpool`。
- `harness/steps/long_term_memory.py` 新增 `aretrieve_memories(...)`,内部用 `asyncio.to_thread(retrieve_memories, ...)`。
- `harness/graph.py::prepare` 从 sync node 改成 async node,这样长期记忆检索能 `await aretrieve_memories`。其余 prepare
  的 runtime summary/uploads/skills preflight 仍是快速本地处理,后续如发现大目录/大文件扫描再独立外移。
- `aexplicit_save_update` 和后台 `aextract_and_save` 的 `memory_store.add_memory` 改为 `asyncio.to_thread`,避免 LLM
  抽取完成后的 SQLite 写入占用 event loop。

为什么不是马上重写 async repository:当前 SQLite 仓库 API 被路由、测试和标题生成链路广泛使用;直接迁到 async repository
会扩大改动面。先用线程池保护 event loop,保留同步 store 的锁/事务语义,是本轮低风险优化。未来若多用户并发增加,再把
ChatRepository/MemoryStore 抽成真正 async 接口。

验证:

- `cd backend && UV_CACHE_DIR=../.slotflow/uv-cache uv run ruff check app/chat/routes.py app/memory/routes.py app/harness/graph.py app/harness/steps/long_term_memory.py tests/test_chat_routes.py tests/test_harness_memory.py` -> passed。
- `cd backend && UV_CACHE_DIR=../.slotflow/uv-cache uv run pytest -q tests/test_chat_routes.py::test_chat_thread_creation_uses_threadpool_for_repository tests/test_harness_memory.py::test_async_memory_retrieval_uses_threadpool tests/test_harness_memory.py::test_memory_routes_use_threadpool_for_store tests/test_harness_memory.py::test_explicit_save_update_saves_latest_turn tests/test_harness_memory.py::test_harness_graph_runs_long_term_memory_middleware_async` -> 5 passed。

## 40. 迭代 32(2026-07-09):async 边界审计第二阶段——工具层双 sync/async 实现

继续处理用户要求的“每批优化”。第三批目标是模型工具层:这些工具本身仍要支持测试/脚本直接 `.invoke()`,但在
LangGraph async graph 的工具节点里不能把 Docker subprocess、同步 httpx、工作区递归读文件这类阻塞操作压在事件循环上。

修复:

- `harness/tools/workspace.py`
  - `workspace_list` / `workspace_read` / `workspace_tree` / `workspace_search` / `workspace_grep` /
    `artifact_list` / `artifact_write` 改为 `StructuredTool.from_function(func=..., coroutine=...)`。
  - 同步 `func` 保持原行为,所以现有 `.invoke()` 单测和脚本不需要重写。
  - async `coroutine` 统一 `await asyncio.to_thread(func, ...)`,让 async ToolNode 路径把本地文件扫描、Office/PDF
    解析、artifact 写入等同步工作放到 worker thread。
  - `workspace_search` 增加候选处理上限:每次最多解析前 1000 个排序候选路径,再按 `max_results` 返回命中。这样大工作区
    不会在一次工具调用里无边界地解析所有文件。
- `harness/tools/network.py`
  - `web_fetch` / `web_search` 改为双 sync/async `StructuredTool`。
  - 同步实现继续使用既有 `httpx.Client`、SSRF/私网阻断、Bing -> DuckDuckGo Lite 回退解析。
  - async 实现用 `asyncio.to_thread` 包住同步实现,避免工具网络请求占用 event loop。
- `harness/tools/sandbox.py`
  - `sandbox_exec` / `sandbox_artifact_copy` / `docker_engine_setup` 改为双 sync/async `StructuredTool`。
  - Docker start/run/copy/host setup 仍走原 `LazyDockerSandbox` / `DockerEngineSetup` 逻辑,但 async graph 路径通过
    `asyncio.to_thread` 执行 subprocess 边界。

为什么不用一次性重写成纯 async HTTP/Docker:网络和 Docker helper 已经有较完整的同步安全边界、测试和错误包装。
本轮优先保证 async graph 不被阻塞,同时保留 `.invoke()` 兼容性;后续若需要更细粒度取消/超时控制,可以再把网络层改成
`httpx.AsyncClient`,Docker 层改成 asyncio subprocess。

验证:

- 新增 `test_workspace_tool_async_path_uses_threadpool`,断言 `workspace_read.ainvoke(...)` 经 `asyncio.to_thread`。
- 新增 `test_sandbox_tool_async_path_uses_threadpool`,mock `LazyDockerSandbox.exec`,断言 `sandbox_exec.ainvoke(...)` 经
  `asyncio.to_thread` 且不触碰真实 Docker。
- 新增 `test_network_tool_async_path_uses_threadpool`,断言 `web_fetch.ainvoke(...)` 经 `asyncio.to_thread`。
- 新增 `test_workspace_search_caps_candidate_scan`,构造 1001 个文件并把命中放在第 1001 个排序候选,确认一次搜索不解析超过
  1000 个候选。
- `cd backend && UV_CACHE_DIR=../.slotflow/uv-cache uv run ruff check app/harness/tools/workspace.py app/harness/tools/network.py app/harness/tools/sandbox.py tests/test_harness_tools.py tests/test_network_tools.py` -> passed。
- `cd backend && UV_CACHE_DIR=../.slotflow/uv-cache uv run pytest -q tests/test_harness_tools.py::test_workspace_tool_async_path_uses_threadpool tests/test_harness_tools.py::test_sandbox_tool_async_path_uses_threadpool tests/test_harness_tools.py::test_workspace_search_caps_candidate_scan tests/test_network_tools.py::test_network_tool_async_path_uses_threadpool tests/test_harness_tools.py::test_workspace_read_extracts_docx_pdf_and_image_metadata tests/test_network_tools.py tests/test_tool_registry.py` -> 17 passed。

## 41. 迭代 33（2026-07-13）：模型运行时统一为 ChatLiteLLM

### 41.1 目标与边界

本轮只替换模型厂商协议与 reasoning/thinking 兼容层，不改 Agent 编排层。以下边界保持原样：

- `harness/graph.py` 的 LangGraph `StateGraph`、节点、边和 ReAct 循环。
- `harness/state.py`、checkpointer、clarification interrupt/resume。
- tools、todo、memory、skills、MCP、sandbox、subagent、summarization。
- `RuntimeBackedAgentAdapter` 的每次 run 装配职责及现有 SSE 业务事件。

原因：`ChatLiteLLM` 是 `BaseChatModel`，负责模型调用、消息转换和流式 chunk 标准化；它不会执行工具、维护图状态或替代 LangGraph。删除 graph/runtime adapter 会把复杂编排重新散落到路由层，属于重复造轮子。

### 41.2 依赖审计

采用 `langchain-litellm==0.7.0`，锁文件解析到 `litellm==1.92.0`。在写代码前检查了已发布 wheel 的实际实现，而不是猜测 API：

- `ChatLiteLLM._convert_delta_to_message_chunk` 读取 `delta.reasoning_content`，保留到 `AIMessageChunk.additional_kwargs`，并生成 `thinking` content block。
- LangChain 1.4.7 的 `AIMessageChunk.content_blocks` 会把该结果暴露为标准 `reasoning` block，供 LangGraph v3 `.reasoning` typed projection 使用。
- `ChatLiteLLM._convert_message_to_dict` 会把已有 assistant message 的 `reasoning_content` 写回后续请求，覆盖 DeepSeek thinking + tool-result 继续生成所需的回传协议。
- `bind_tools` 标准化 OpenAI schema tool definitions/tool choice；流式转换生成标准 `ToolCallChunk`。
- usage 转换包含 cache token details 和 reasoning token details。

因此删除直接依赖 `langchain-openai`、`langchain-anthropic`、`langchain-deepseek`，也删除 SlotFlow 的 `_SlotFlowChatDeepSeek` 子类、stream bridge、DeepSeek payload 注入函数和三套模型构造器。所有 provider 最终只构造 `ChatLiteLLM`。

LiteLLM 1.92.0 的 Python metadata 是 `>=3.10,<3.14`。项目运行范围改为 Python 3.12/3.13（`requires-python = ">=3.12,<3.14"`）；本轮使用 uv 管理的 CPython 3.13.14 验证。没有绕过依赖的 Python 版本约束。

### 41.3 运行链路

新的模型调用链：

```text
RunContext.model_provider
-> runtime/models.py::create_chat_model
-> build_litellm_model_kwargs
-> ChatLiteLLM(BaseChatModel)
-> LiteLLM provider transport
-> LangGraph agent node / bind_tools / ToolNode
-> LangGraph v3 reasoning/text/tool_calls projections
-> SlotFlow AgentEvent / SSE
```

catalog 继续向前端返回原始 model id 和 provider provenance；内部通过 `custom_llm_provider` 选择 LiteLLM transport，不要求 UI 改 model id：

- `deepseek` -> LiteLLM `deepseek`
- `openai` -> LiteLLM `openai`
- `anthropic` -> LiteLLM `anthropic`
- `custom` -> LiteLLM `openai` + `CUSTOM_BASE_URL` + neutral User-Agent

### 41.4 Thinking 策略

厂商响应解析全部交给 LiteLLM。SlotFlow 只保留不可避免的能力策略：

- DeepSeek thinking on：`reasoning_effort=high` 且 `extra_body.thinking.type=enabled`。
- DeepSeek thinking off：显式 `extra_body.thinking.type=disabled`，因为该模型默认会思考。
- Anthropic thinking on：`thinking={type: enabled, budget_tokens: 4096}`，`max_tokens=8192`。
- OpenAI o-series/gpt-5 thinking on：`reasoning_effort=high`。
- custom relay：不发送未知厂商 thinking 参数，只使用 LiteLLM 对实际返回字段的标准化。

`agent_adapter/projections.py` 不再解析 OpenRouter 私有 `additional_kwargs.reasoning` 或 OpenAI Responses `summary[]` 等厂商原始结构；只处理 LangGraph typed channel、标准 reasoning/thinking block，以及 LiteLLM 的 canonical `additional_kwargs.reasoning_content` fallback。

### 41.5 dotenv 安全边界

LiteLLM 在默认 `LITELLM_MODE=DEV` 时会在 import 阶段执行 `dotenv.load_dotenv()`。这与 SlotFlow 的配置边界冲突：SlotFlow 只应接收 OS/uvicorn 显式提供的环境，模型依赖不应自行读取 `backend/.env`。

完整离线测试第一次执行时暴露了该问题：前序测试触发 LiteLLM dotenv 后，`SLOTFLOW_TITLE_MODEL_ENABLED` 和真实 provider credentials 进入进程，后续标题测试出现随机模型生成标题，证明本应离线的测试触发了真实 title request。该调用不作为有效 live 验证。

根因修复是在导入 `langchain_litellm` 之前强制 `LITELLM_MODE=PRODUCTION`。新增回归测试锁住该设置，并用完整路由测试顺序确认 `.env` 不再被依赖隐式加载。不要删除或下移这条设置，否则会重新引入测试付费调用和配置来源不确定性。

### 41.6 离线验证结果

- `tests/test_provider_reasoning_contract.py` 使用 fake `litellm.acompletion` 驱动真实 `ChatLiteLLM.astream()`，验证 reasoning block、tool-call chunk 和 SlotFlow channel 映射，不调用 provider API。
- `tests/test_runtime.py` 验证 DeepSeek/OpenAI/Anthropic/custom 都构造 `ChatLiteLLM`，thinking policy、custom relay headers、provider provenance 和 tool-result 后 reasoning 回传保持正确。
- `cd backend && uv run pytest -q -k "not live"` -> `344 passed, 1 deselected`。
- `cd backend && uv run ruff check app tests` -> passed。
- `git diff --check` -> passed。

尚未执行显式 live reasoning + tool-call smoke test。需要人工授权真实 provider 调用后，至少验证一次 thinking on、一次 thinking off，以及 thinking on 下调用工具后继续生成；在此之前不能把前述意外 title request 当成链路验证。
完整仓库验证补充：`PATH="$HOME/.volta/bin:$PATH" make verify` 通过；后端 `344 passed, 1 skipped`，前端 `pnpm typecheck` 通过，Next.js production build 通过。首次直接执行 `make verify` 时非交互 WSL PATH 缺少 `~/.volta/bin`，不是代码或依赖失败。
## 42. 迭代 34（2026-07-14）：LiteLLM 元数据目录 + OpenAI Responses bridge

### 42.1 为什么继续删除第一阶段策略

§41 完成了 `ChatLiteLLM` 统一，但 SlotFlow 当时仍维护四类原生 provider、model id
前缀推断、官方 base URL、DeepSeek thinking body、Anthropic thinking budget 和 OpenAI
reasoning model 判断。这些代码虽然比三个原生 LangChain client 小，仍属于厂商兼容层；
LiteLLM 升级后 SlotFlow 仍要同步修改，违背本轮“不重复维护 provider/reasoning 兼容”的目标。

本轮把边界收紧为：SlotFlow 只保留 Agent 可用模型筛选、每次 run 的 catalog provenance、
custom relay URL/key，以及统一 thinking 开关。所有 native provider 名称、模型清单、凭据检测、
function-calling/reasoning 能力和协议转换来自 LiteLLM 的公开 API/metadata。

### 42.2 代码核实后的 LiteLLM / ChatLiteLLM 能力

实现前直接读取了锁定依赖的源码（`langchain-litellm==0.7.0`、`litellm==1.92.0`）：

- `ChatLiteLLM._generate/_agenerate/_stream/_astream` 调用 LiteLLM
  `completion/acompletion`，自身没有单独的 Responses client。
- LiteLLM `main.py::responses_api_bridge_check` 支持逐模型 `responses/` 前缀，也提供
  `route_all_chat_openai_to_responses` 全局开关。
- 全局开关会命中所有 `custom_llm_provider="openai"` 请求，包括 custom relay；因此不能使用，
  否则会错误假设中转站实现 `/responses`。
- `openai/responses/<model>` 先由 `get_llm_provider` 解析为 official OpenAI，再由
  `responses_api_bridge_check` 标记 `mode=responses`。bridge 内部调用 `litellm.aresponses()`，
  然后把 Responses 文本、reasoning summary、function calls 和 usage 转回标准
  `ModelResponseStream`，正好供 `ChatLiteLLM` 和现有 LangGraph 消费。

所以最终选择是：官方 `openai` provider 逐模型加 `openai/responses/` 路由；custom relay
保持 Chat Completions。这样满足 Responses 优先，同时不新增 SlotFlow Responses 事件解析器，
也不污染非 OpenAI provider。

### 42.3 模型目录与 provider 路由

`chat/litellm_provider.py` 现在是唯一 LiteLLM 边界：

1. import 前设置 `LITELLM_MODE=PRODUCTION`，禁止依赖隐式读取 `backend/.env`。
2. 设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，只使用随包发布的 model map；模型目录升级跟随
   PyPI lock 更新，不在请求时从 GitHub 刷新。
3. `configured_native_provider_names()` 调用公开
   `get_valid_models(check_provider_endpoint=False)` 检测当前进程已配置的 provider，并与
   `models_by_provider` 交集关联。
4. `agent_models_for_provider()` 将 model id 规范为 `provider/model`，只保留
   `get_model_info(...)["mode"] == "chat"` 且 `supports_function_calling(...)` 的模型。
5. `model_catalog.discover_model_catalog()` 通过 `asyncio.to_thread` 生成 native catalog，避免
   LiteLLM metadata/endpoint 工作阻塞 FastAPI event loop。

`ModelProvider` 的 API/前端类型改成开放字符串，不再限制为 DeepSeek/OpenAI/Anthropic/custom。
原生模型必须使用 catalog 返回的 provider-qualified id；旧的手写 id-prefix 推断被删除，
缺少 provenance 时只调用 `litellm.get_llm_provider`。这不是保留兼容层：无法被 LiteLLM 解析的
裸 model id 直接失败，调用方应使用 catalog id。

custom 是唯一例外：`CUSTOM_API_KEY` + `CUSTOM_BASE_URL` 明确声明 OpenAI-compatible relay；
模型发现调用 LiteLLM `get_valid_models(check_provider_endpoint=True, custom_llm_provider="openai")`，
`CUSTOM_MODELS` 仍可在 relay 不支持 model listing 时显式列出 id。旧的 SlotFlow
`/chat/completions` availability probe 和 `CUSTOM_VALIDATE_MODELS` 被删除，不再自维护额外探测协议。

### 42.4 Thinking 与 Responses 运行链路

native runtime 不再读取 `DEEPSEEK_*`/`OPENAI_*`/`ANTHROPIC_*` 映射表，也不传 SlotFlow
硬编码 base URL/API key；`ChatLiteLLM(model="provider/model")` 交给 LiteLLM 读取各 provider
标准环境配置。custom 仍显式传 relay key/base/neutral User-Agent。

统一 thinking 开关只做一次 capability 查询：

```text
litellm.get_supported_openai_params(model=provider_qualified_id)
└─ 包含 reasoning_effort
   ├─ thinking_enabled=true  -> reasoning_effort=high
   └─ thinking_enabled=false -> reasoning_effort=none
```

不支持该统一参数的模型不接收 SlotFlow thinking 参数；没有 DeepSeek、Anthropic、Gemini、
Bedrock 或 Mistral 分支。provider 具体怎样把 `reasoning_effort` 转成 thinking budget/config，
以及怎样解析返回值，全部由 LiteLLM 负责。

官方 OpenAI 运行链是：

```text
catalog id openai/<model>
-> runtime id openai/responses/<model>
-> ChatLiteLLM.acompletion
-> LiteLLM Responses bridge
-> litellm.aresponses (/responses)
-> LiteLLM ModelResponseStream
-> ChatLiteLLM AIMessageChunk
-> LangGraph v3 projection -> SlotFlow SSE
```

### 42.5 测试与未验证项

新增/更新离线契约覆盖：

- LiteLLM 环境检测只暴露已配置且有 Agent-capable 模型的 native provider。
- catalog 可自动加入 Gemini/Mistral 等开放 provider，并过滤非 chat/无 function calling 模型。
- custom endpoint discovery、`CUSTOM_MODELS`、错误脱敏和 missing 配置。
- reasoning on/off 只产生统一 `high`/`none`；不支持模型没有 SlotFlow thinking 参数。
- official OpenAI runtime id 为 `openai/responses/<model>`；custom relay 不加该前缀。
- 原生 DeepSeek/OpenAI/Anthropic/Gemini/Mistral 都构造同一个 `ChatLiteLLM` 类型。
- 既有 fake transport reasoning/tool-call/usage 投影契约继续覆盖，不访问真实 provider。

验证结果：`cd backend && uv run ruff check app tests` 通过；`PATH="$HOME/.volta/bin:$PATH" make verify` 通过，
后端为 `347 passed, 1 skipped`，前端 `pnpm typecheck` 和 Next.js production build 均通过；`git diff --check` 通过。

没有执行真实付费 provider 请求，因此本轮只证明 LiteLLM 1.92.0 的 bridge 选择与转换契约，
不声称完成 live Responses/reasoning/tool-call 验证。真实 smoke test 仍需用户明确允许 API 费用后执行。
## 43. 迭代 35（2026-07-14）：DeepSeek 工具续轮拒绝 `reasoning` content block

### 43.1 用户实测错误

真实 DeepSeek thinking 请求在工具调用后的下一轮报错：

```text
litellm.BadRequestError: DeepseekException
messages[2]: unknown variant `reasoning`, expected `text`
```

这证明 §41/§42 的离线契约仍缺一层：测试确认了顶层 `reasoning_content` 会回传，却没有构造
LangChain canonical `{"type":"reasoning"}` content block 并检查最终 provider payload。

### 43.2 根因（对照锁定依赖源码）

`langchain-litellm==0.7.0` 的完整链路是：

1. 流式 `delta.reasoning_content` 被保存到 `AIMessage.additional_kwargs["reasoning_content"]`。
2. 同一 reasoning 还被注入 `content` 的 `{"type":"thinking"}` block；LangChain
   `content_blocks` 将其标准化显示为 `{"type":"reasoning"}`。
3. `ChatLiteLLM._convert_message_to_dict` 发送历史 assistant message 时会过滤
   `thinking`、`redacted_thinking`、tool blocks，但没有过滤 canonical `reasoning`。
4. LiteLLM `DeepSeekChatConfig._transform_messages` 调用
   `handle_messages_with_content_list_to_str_conversion`；该函数只抽取 block 的 `text`。
   如果 list 只有 reasoning block，抽取结果为空，代码不会把 content 改成空字符串，原 list
   被原样发送。
5. DeepSeek chat schema 的 content-part union 只接受 `text`，因此在模型生成前直接反序列化失败。

PyPI 最新 `langchain-litellm==0.7.0`、`litellm==1.92.0` 以及检查时的
`langchain-ai/langchain-litellm` main 都有相同缺口；当前没有可升级的已发布修复。

### 43.3 修复边界

唯一 LiteLLM 边界 `chat/litellm_provider.py` 现在导出一个最小
`ChatLiteLLM` subclass，只覆盖 `_create_message_dicts`：

- 先调用上游 serializer，保留其 tool/message/attachment 行为。
- 只处理 assistant 的 list content。
- 移除 `reasoning`、`thinking`、`redacted_thinking`，以及包裹这些类型的
  `non_standard` block。
- 保留标准 text/media block。
- content 被全部过滤时写成空字符串。
- **不删除**顶层 `reasoning_content`，所以 LiteLLM/DeepSeek thinking-mode 工具续轮仍能收到
  完整上一轮推理链。

这个修复不判断 DeepSeek/Anthropic/OpenAI，不解析厂商 response，也不改变 LangGraph state；
它只保证“reasoning metadata 不作为 assistant 正文 content 发给 provider”。因此模型目录和
reasoning 参数仍完全由 LiteLLM metadata 驱动。

### 43.4 回归测试

`tests/test_provider_reasoning_contract.py::test_litellm_tool_followup_strips_reasoning_metadata_from_content`
构造真实失败形状：assistant 同时带 canonical reasoning block、`non_standard(thinking)`、
顶层 `reasoning_content`、tool call，随后接 ToolMessage。测试直接检查
`ChatLiteLLM._create_message_dicts` 的最终 payload：

- assistant `content == ""`；
- `reasoning_content` 原值保留；
- tool call id 保留。

验证：targeted reasoning/runtime tests 为 `38 passed`；完整 `make verify` 的后端为
`347 passed, 1 skipped`，前端 typecheck/production build 通过；`git diff --check` 通过。

用户报告的请求证明旧实现会在 live provider 失败；本节修复完成后尚未重新产生付费 live
DeepSeek 请求，因此不能把离线通过写成 live 修复验证。
## 44. 迭代 36（2026-07-14）：subagent 独立 recursion limit

### 44.1 根因

LangGraph 默认 `recursion_limit=25` 统计的是 graph superstep，不是“模型最多思考 25 次”。
当前子图一次 ReAct 循环会经过 pre-model/summarization/agent/post-model/route/tools 等多个节点；
因此连续调用工具、工具结果后反思、再调用工具会快速消耗额度。纯模型回答没有 tools 往返，
所以同样复杂度下更容易在 25 步内完成。这是 child graph 执行预算不足，不是
`subagent_max_concurrent`（并发数）问题。

### 44.2 边界选择

只放宽 `task_tool` 创建的 child graph，不修改主图：

- `SlotFlowSubagentConfig.recursion_limit` 默认从 25 提高到 100。
- `SubagentTaskRunner` 保存该值，并调用
  `graph.ainvoke(payload, config={"recursion_limit": value})`。
- `SlotFlowRuntimeConfig` 显式携带 `subagent_config`，runtime adapter 将其传给
  `SlotFlowHarnessConfig`/tool registry。
- `SLOTFLOW_SUBAGENT_RECURSION_LIMIT=<positive-int>` 可覆盖默认值；环境解析复用
  `load_positive_int_from_env`，拒绝 0/负数。
- 主 graph 的 request config 不增加 recursion limit，仍沿用 LangGraph 默认值；这避免一次主任务
  因错误路由无限循环，只给隔离的 delegated child 足够的多工具预算。

100 是上限而不是目标步数：正常子任务仍在模型给出无 tool-call 的 final answer 时立即结束，不会
固定运行 100 步。现有 timeout/provider error/tool safety 仍会提前终止失败路径。

### 44.3 回归测试

- `test_subagent_default_recursion_limit_allows_multi_tool_loops` 锁定默认 100。
- `test_task_tool_injects_selected_agency_role` 的 fake child graph 同时捕获
  `ainvoke` config，断言自定义 73 原样进入 `{"recursion_limit": 73}`。
- `test_load_runtime_config_from_env_reads_harness_feature_flags` 断言
  `SLOTFLOW_SUBAGENT_RECURSION_LIMIT=73` 进入 runtime subagent config。
- `test_runtime_graph_factory_delegates_to_harness_builder` 断言 runtime 的自定义
  subagent config 进入 harness config。

验证：targeted subagent/builder/runtime tests 为 `47 passed`；完整 `make verify` 后端为
`348 passed, 1 skipped`，前端 typecheck/production build 通过；`git diff --check` 通过。
## 45. 迭代 37（2026-07-15）：统一使用 LiteLLM Chat Completions，放弃 Responses bridge

### 45.1 架构判断

LiteLLM 的价值仍然存在：它负责 provider 凭据、model routing、OpenAI-like 标准请求到厂商
请求的转换、反向 response/chunk/usage/tool-call 标准化、重试和 provider metadata。
LangGraph 不会阻止 LiteLLM 在第二次工具请求时重新做厂商转换；真正的风险在
`ChatLiteLLM` 的 `LangChain AIMessage ↔ LiteLLM normalized message` 这条额外适配边界。

`ChatOpenAI` 只有在架构为 `LangGraph → ChatOpenAI → LiteLLM Proxy → provider` 时才是直接替代品。
本项目不启动独立 LiteLLM Proxy，而是在 Python 进程内使用 LiteLLM SDK，因此继续使用
`ChatLiteLLM`，但统一让它调用 `litellm.completion/acompletion`。

### 45.2 运行策略

- 原生模型仍使用 `provider/model` provider-qualified id。
- 删除 `openai/responses/<model>` 路由；官方 OpenAI 与 DeepSeek、Qwen、Mistral、custom relay
  共用 Chat Completions normalized shape。
- LiteLLM 1.92.0 对 GPT-5.4+ 的 tools + reasoning_effort 可能自动触发 Responses bridge，
  因此 `ChatLiteLLM` 的 `model_kwargs` 显式传 `_skip_responses_api_bridge=True`，强制保留
  `completion/acompletion` 路径。这个参数是当前锁定 LiteLLM 的内部开关，必须和版本一起验证。
- 这不代表所有厂商原生网络端点都叫 Chat Completions；Anthropic/Gemini/Bedrock 仍由 LiteLLM
  内部转换到各自原生协议，但 SlotFlow 的统一入口和返回面固定为 Chat Completions-like。
- Responses item、`previous_response_id`、OpenAI server-side response chain 不再作为 SlotFlow
  的上下文协议；长期记忆继续由 SlotFlow 自己管理。

### 45.3 离线验证

`tests/test_runtime.py` 锁定 OpenAI 模型保持 `openai/<model>`，并断言
`_skip_responses_api_bridge=True`；DeepSeek/Qwen/custom/native provider 都使用同一
ChatLiteLLM construction path。完整 `make verify` 已于 2026-07-15 重新执行并通过：
后端 `348 passed, 1 skipped`，前端 typecheck 与 Next.js production build 通过。
## 46. 迭代 38（2026-07-15）：reasoning 状态无损往返——归属结论 + thinking_blocks 载体

### 46.1 §43 遗留问题的归属结论（逐行对照锁定依赖源码）

§43 修复 DeepSeek `unknown variant reasoning` 后遗留两个问题：bug 归属（ChatLiteLLM 还是
LiteLLM），以及「ChatLiteLLM 可能不是无损适配器」还缺哪些字段。结论：

- **主因是 langchain-litellm 0.7.0（ChatLiteLLM）的双向转换不对称**（它是 LangChain 集成包，
  不属于 LangGraph）。响应侧它把 `reasoning_content` 发明成 content 里的 `{"type":"thinking"}`
  块（`chat_models/litellm.py:114-134, 200-204`）；langchain-core 1.4.7 无 `"litellm"` 翻译器，
  best-effort 把该块规范化为 `{"type":"reasoning"}`（未识别块包成 `non_standard`）；出站只过滤
  `tool_use/tool_call/thinking/redacted_thinking` 四种（`litellm.py:346-352`），漏掉规范化形态。
  发明了表示却不对称回收。
- **LiteLLM 1.92.0 非根因**：输入契约是 OpenAI 格式消息，reasoning 块本不该出现在 content。
  `convert_content_list_to_str` 只拼 `text` 字段、纯 reasoning 列表得空串于是原样发出
  （`prompt_templates/common_utils.py:84-94, 163-184`）——只是未做防御。它反而已把**所有**厂商
  opaque reasoning state 归一到 OpenAI-like 消息的两个顶层载体：`reasoning_content`（文本）与
  `thinking_blocks`（带 signature 的不透明块）；Gemini thought signature 还编码进 `tool_call_id`
  （`prompt_templates/factory.py:1188-1207`）随 tool_calls 自动往返。
- **LangGraph 无关**，只忠实持久化消息。

### 46.2 第二个缺口：thinking_blocks 载体整体丢失（比 DeepSeek 更隐蔽）

ChatLiteLLM 0.7.0 在两个方向都丢弃 LiteLLM 的 `thinking_blocks`。后果不是报错：LiteLLM 发现
thinking 开启但历史 assistant 消息缺 `thinking_blocks` 时**静默关闭该请求的 extended thinking**
（`llms/anthropic/chat/transformation.py:1759-1776`，仅 verbose 警告）。即 Anthropic/Bedrock
thinking + 工具续轮会悄悄退化成"无思考续轮"。LiteLLM 请求侧还原已就绪：assistant 顶层
`thinking_blocks` 会被正确重建进原生请求，且无签名块会被丢弃（`factory.py:2322-2339, 2530-2688`）。

流式形状（`llms/anthropic/chat/handler.py:654-696`）：每个 thinking delta 产出**无签名分片**
`{"type":"thinking","thinking":"<partial>"}`；signature_delta 到达时产出**自带全量累积文本**的
签名块；`redacted_thinking` 在 content_block_start 一次性完整产出。langchain-core 的 chunk 合并
对无 `index` 列表元素是顺序追加（`utils/_merge.py::merge_lists`），分片全部保留进
`additional_kwargs`。

### 46.3 修法：边界原则从「只删」升级为「载体往返」

`chat/litellm_provider.py` 现在实现统一规则：**assistant content 只携带 text/media；opaque
reasoning state 走顶层载体；只回传 provider 自己产出过的状态；无厂商分支**。

1. 响应侧捕获：包装 langchain-litellm 的模块级 `_convert_dict_to_message` /
   `_convert_delta_to_message_chunk`（它们被 `_stream/_astream/_create_chat_result` 以模块全局
   引用，call-time 解析，重绑即生效），把 message/delta 的 typed `thinking_blocks` 存进
   `additional_kwargs["thinking_blocks"]`。依赖精确锁定 `langchain-litellm==0.7.0`；契约测试驱动
   真实 astream 路径，升级若挪动内部实现会红灯而不是静默丢字段。
2. 请求侧还原：`ChatLiteLLM._create_message_dicts` 覆盖里 `zip(messages, message_dicts)` 对齐，
   assistant 消息做两件事：content 清理（§43 原逻辑，`_without_reasoning_metadata_blocks`）+
   `_consolidated_thinking_blocks` 合并分片后写回 assistant dict 顶层。合并规则：有签名块/
   redacted 块则取它们（签名块已含全量文本，无签名分片是被 subsume 的过程量）、全部无签名才拼接
   成单块（下游 `_drop_unsignable_thinking_blocks` 兜底）。另接受
   `additional_kwargs["provider_specific_fields"]["thinking_blocks"]` 作为 fallback 来源。
3. 对称性保证安全：DeepSeek/OpenAI/多数 relay 从不产出 `thinking_blocks` → 该键永不出现在其
   payload；产出过的（Anthropic/Bedrock/Gemini/代理 Anthropic 的 relay）才会收到回传。

### 46.4 契约测试矩阵（按载体形状，不按厂商名）

`tests/test_provider_reasoning_contract.py` 新增 6 例，断言到最终 provider payload 字段级：

- 签名块流式往返（Anthropic/Bedrock 形状）：无签名分片 + 签名全量块 + tool call 经真实
  `astream` → 合并 chunk 捕获载体 → 续轮 payload 顶层 `thinking_blocks` 只含签名块、content 空、
  `reasoning_content`/tool_call id 原样。
- 非流式响应捕获；分片合并优先完整块且保序（含 redacted）；全无签名时拼接单块。
- Gemini 形状：`__thought__` 后缀 tool_call_id 逐字节往返。
- OpenAI CC 形状：无 reasoning 状态时 payload 完全不被改写（无新键、content 原样）。
- §43 原 DeepSeek 往返/泄漏用例保持，并补断言「无 thinking_blocks 输入时不注入该键」。

### 46.5 上游行动

**已提交（2026-07-15）**：issue
https://github.com/langchain-ai/langchain-litellm/issues/222 + PR
https://github.com/langchain-ai/langchain-litellm/pull/223（`Fixes #222`，上游 CI 全绿：
Python 3.10-3.13 测试/format/lockfile/CodeQL）。PR 在上游代码里完成同等修复（过滤
`reasoning`/`non_standard`、text-only 折叠、`thinking_blocks` 双向往返 + 分片合并），含 8 个新
单测；详见 `docs/upstream-reasoning-roundtrip-drafts.md`（已回填真实链接）。归属确认时上游状态：
PyPI 最新即 0.7.0，main 同样存在缺口；相关前案 #71/#85（非流式 reasoning_content 修复、但引入
content 注入）、#139/#159（thinking 块过滤，不含 reasoning 形态）、#216（open：注入设计本身）、
#218（open PR：流式分片合并）。上游合入发布后：升级依赖、删除本地对应 wrapper/过滤、契约矩阵
作为回归门。

### 46.6 不变量（勿回归）

- opaque reasoning state 只走顶层载体（`reasoning_content`/`thinking_blocks`），content 不携带
  reasoning 元数据；只回传 provider 自己产出过的状态；不加 `if provider ==` 分支。
- `langchain-litellm` 升级必须过 `tests/test_provider_reasoning_contract.py`（模块 wrapper 依赖
  0.7.0 内部结构，靠该矩阵红灯拦截）。
- 新增 provider/载体形状时先在契约矩阵加对应用例，再动边界代码。
- tool_call id 必须逐字节保留（Gemini thought signature 寄生其中）。

### 46.7 验证

- `uv run pytest tests/test_provider_reasoning_contract.py -q` → 16 passed（含 6 新例）。
- `uv run pytest tests/test_runtime.py tests/test_agent_adapter.py tests/test_model_catalog.py -q`
  → 80 passed。
- `uv run ruff check app tests` → passed。
- `PATH="$HOME/.volta/bin:$PATH" make verify` → 后端 `354 passed, 1 skipped`，前端 typecheck 与
  Next.js production build 通过；`git diff --check` 通过。
- **DeepSeek live smoke 已完成（2026-07-15 晚,用户授权付费调用）**：throwaway 探针
  （job tmp,未提交）走生产 `build_agent_adapter`,全新 thread,`deepseek/deepseek-v4-pro`
  + `thinking_enabled=True`,10/10 PASS：
  - 轮 1：thinking 流出（1732 字符 reasoning delta）→ 真实调用 `artifact_write` → 工具续轮
    **无任何 400**（`unknown variant reasoning` 与 `reasoning_content must be passed back`
    均未出现）→ 正文回复 → 产物文件落盘且内容正确。
  - 轮 2（同线程）：完整历史（含 reasoning+tool call 的 assistant 轮）回放被官方严格端点
    接受,模型准确复述上一轮文件名与内容 → 短期记忆修复 live 成立。
  - Anthropic `thinking_blocks` 载体的 live 验证仍缺（环境无 ANTHROPIC_API_KEY）,配置 key 后
    按同法补跑,验证点：LiteLLM debug 下请求带 `thinking_blocks`、无
    「won't use extended thinking」降级警告。
- **live 探针顺带发现一个无关旧 bug（已修，见 §47）**：轮 1 正文 content 通道里流出了
  `<slotflow-todo-enforcer>…` 内部控制消息全文。根因是 todo enforcer/reminder 作为
  `HumanMessage` 注入 messages 通道,违反 §29 边界。

### 46.8 补记（2026-07-15 晚）：短期记忆修复与上游 PR 的语义对齐

同日下午另一会话（GPT-5.6）修复了「模型丢失自己上一轮回复」（短期记忆）问题，根因同属本节
主题：ChatLiteLLM 注入使 content 变列表 → 流式合并留下裸字符串项 → 过滤后 `["answer"]`
不是合法 Chat Completions content → 严格端点拒绝、宽松中转站静默丢正文。修法与上游 PR 同一
方法：在同一边界把 text-only assistant content 折叠回普通字符串
（`_without_reasoning_metadata_blocks`）。

随后做了双向语义对齐并用 6 个载体形状对比验证 **ALL EQUAL**：

- SlotFlow 侧：列表形态改为**保序**（原版会把文本块挪到结构块前面）；空文本项丢弃保留。
  新增回归 `test_structured_assistant_content_preserves_block_order`。
- 上游 PR #223 侧：补第二个 commit（`c95fa46`）——列表形态**丢弃空文本项**（Anthropic 拒绝
  空 text 块，包空串等于换一种 400）。上游 53 passed + ruff + mypy 全绿。

不变量补充：本地 `_without_reasoning_metadata_blocks` 与上游 PR `_collapse_text_only_content`
必须保持语义等价（保序、空文本项丢弃、text-only 折叠字符串）；上游发布含该修复的版本后收缩
本地实现时，以契约矩阵为验收。

PR #223 合并被阻止属上游流程（分支保护要求维护者审查 + fork PR 的 CodeQL required-check
关联延迟），10 项检查全部通过，非代码问题。
## 47. 迭代 39（2026-07-15）：短期记忆丢失根因——assistant 历史 content 裸字符串列表

### 47.1 用户报告与排查路径

用户实测：LiteLLM 迁移后 agent 丢失短期记忆，怀疑 Chat Completions 被改成 Responses 调用。
排查结论：**Responses bridge 不是原因**。仓库内 Responses 残留只有
`runtime/models.py` 的 `_skip_responses_api_bridge=True`——它恰是强制 Chat Completions 的
保险（litellm `main.py:4863,5325` 会 pop 并跳过 bridge），已用 fake `acompletion` 实验确认
flag 抵达调用且 messages 全量发出，必须保留、不是多余代码。

### 47.2 真正根因

流式聚合时 langchain-core 的 `merge_content` 把 text delta 以**裸字符串**追加进 content 列表：
带 thinking 的回合最终 `AIMessage.content == [{"type":"thinking",...}, "正文"]`。下一轮请求
`_without_reasoning_metadata_blocks` 过滤 thinking 后剩 `["正文"]`——列表里是裸字符串，不合
Chat Completions schema：

- DeepSeek：LiteLLM `convert_content_list_to_str` 对非 dict 项直接 `AttributeError`（请求前崩溃）。
- OpenAI/严格端点：400。
- 宽松中转站：静默丢弃正文 → 模型看不到自己上一轮回复 = 用户观察到的"丢失短期记忆"。

### 47.3 修复与新契约

`_without_reasoning_metadata_blocks` 从"只删 reasoning 块"升级为"删 + 收敛"：纯文本内容
（裸字符串 + `{"type":"text"}` 块）一律拼接回**普通字符串**（最通用的 assistant 形态）；
仅存在非文本块时保留列表并把字符串包成 text 块。契约测试
`test_plain_assistant_text_content_normalizes_to_string` 锁定两个形状：text 块列表 → 字符串、
`[thinking块, 裸字符串]` → 字符串。离线复现脚本确认 DeepSeek transform 不再崩溃、
正文完整回传。`uv run pytest -q -k "not live"` -> 354 passed；ruff 通过。

## 48. 迭代 40（2026-07-15）：todo enforcer/reminder 控制文本泄漏进用户正文——根因修复

### 48.1 症状（§46.7 live 探针顺带抓到）

DeepSeek 真机探针轮 1 正文里流出了 `<slotflow-todo-enforcer>…</slotflow-todo-enforcer>` 整段
内部控制指令,直接展示给用户。与 reasoning 修复无关,是独立旧 bug。

### 48.2 根因（对照代码核实,非表面）

`todo_enforcement_update` 与 `todo_reminder_update`（`harness/steps/todo.py`）把内部控制指令
作为 `HumanMessage` 注入 `messages` 会话通道。这违反 §29 已立下的不变量——「SlotFlow 内部上下文
必须走 slotflow state / system_prompt,不能写入会被当作对话内容的通道」。两个后果:

1. **流式泄漏**：v3 messages 投影会把它看到的每个**新** message 对象当增量流给用户（按 id
   去重,所以历史回放不重复,但当步新造的控制 HumanMessage 会 surface）。§13 节点化之后,
   §12.3 时代「注入消息不进投影」的假设已失效——这正是本 bug 与 §32.5「模型复读注入文本」
   同源、但更直接（不经模型、投影层直出）。
2. **持久化污染**：`messages` 带 `add_messages` reducer,checkpointer 每轮回放该控制消息。

投影层过滤（按 name 屏蔽）只能治 (1) 且治不了 (2),是表面补丁。

### 48.3 根因修复：控制文本走 system_prompt 字符串通道

与 §29 给 skills preflight 的同一条路径:

- 新增 state 通道 `todo_enforcement`（`{pending, attempted}`）承载 post_model 的约束意图。
- `todo_enforcement_update` 不再返回 `{"messages":[HumanMessage(...)]}`,改为写
  `{"todo_enforcement": {"pending": <文本>, "attempted": True}}`；`attempted` 是纯标志防循环,
  模型真的调用 `write_todos` 时由 `_reset_enforcement_update` 复位（不再扫历史找命名消息）。
- `todo_reminder_update` 改为**只返回控制文本字符串**（或 None）。
- `pre_model` 把 reminder 文本 + 消费到的 enforcement `pending` 追加进**当步 system_prompt**
  （最靠后,作为最新指令）,并清空 `pending`。控制文本从不进入任何 message 对象,因此既不
  被 messages 投影流出,也不被 checkpointer 持久化。
- `route_after_model` 改用 `route_after_model_has_enforcement`（看 `todo_enforcement.pending`）
  决定是否回环 pre_model,替代原来的「最后一条消息是 enforcer HumanMessage」。
- 删除 `TODO_ENFORCER_MESSAGE_NAME`/`TODO_REMINDER_MESSAGE_NAME`/`latest_message_is_todo_enforcer`
  /`_has_named_human_message_since_last_write_todos` 等基于「命名消息进历史」的死代码。

### 48.4 验证

- 新增 `tests/test_agent_adapter.py::test_todo_enforcer_control_text_never_reaches_stream_or_history`：
  真实 graph + checkpointer 两断言——控制块不出现在 message.delta、不残留进持久 messages 历史；
  同时断言两次模型调用的正文都正常流出（enforcement 循环仍生效）。先对修复前代码验证为红。
- `tests/test_harness_steps.py` 的 todo enforcement/reminder 用例改为断言 `todo_enforcement`
  state 通道与 `consume_todo_enforcement` 消费/复位/防循环语义。
- 全量 `uv run pytest -q -k "not live"` -> **362 passed**；`ruff check app tests` 通过。
- **真机 live（DeepSeek thinking, 全新 thread）**：触发 todo enforcement（`todo.updated`×16,
  证明 enforcer 仍驱动模型写 todo）,正文**无** `slotflow-todo-enforcer`/`-reminder` 泄漏。

### 48.5 不变量（勿回归）

- SlotFlow 内部逐步控制上下文（todo reminder/enforcement、skills preflight、runtime summary
  等）只能走 `system_prompt` 字符串通道或 `slotflow`/专用 state 通道,**绝不**构造 message 对象
  塞进 `messages` 或 `llm_input_messages`——v3 messages 投影会把新 message 对象当可见增量流出,
  `messages` 还会被 checkpointer 持久化回放。
- todo enforcement 的防循环用 `todo_enforcement.attempted` 标志,不靠扫描历史里的命名消息。

## 49. 迭代 41（2026-07-16）：Agent Reach 固定宿主桥接——互联网能力不等于宿主 shell

### 49.1 需求与边界判断

用户拍板 Agent Reach 放在宿主机，不进入 Docker；依赖和上游刷新统一由根目录
`bootstrap.sh` 完成，不再提供额外维护命令。对照 Agent Reach 1.5.0 的实际 Skill/安装器后确认：
Agent Reach 自身是渠道选择器、安装器、体检器和路由说明，真正取数依赖 `mcporter`、`gh`、
`yt-dlp`、Jina Reader 等上游工具。因此“只复制 Skill”不能让 SlotFlow graph 获得能力；反过来把
任意宿主命令交给模型又会破坏现有的“代码只进 Docker”不变量。

本轮选择中间边界：宿主保留 Agent Reach 和登录态，模型只得到 SlotFlow 固定 argv 的只读工具。

### 49.2 bootstrap 实测抓到的工作目录根因

首次在仓库根运行 `agent-reach install --env=auto` 时，上游 `mcporter` 按 cwd 生成了
`config/mcporter.json`，污染工作树。不是 gitignore 问题，而是调用方工作目录错了。修复后
`bootstrap.sh::install_agent_reach` 先创建并进入 `~/.agent-reach`，配置稳定落到
`~/.agent-reach/config/mcporter.json`。`uv tool install` 同时用
`--with-executables-from yt-dlp` 暴露依赖包的 `yt-dlp` executable；否则 uv 隔离环境只链接根包的
`agent-reach` 命令，doctor 会把已经作为 Python dependency 安装的 yt-dlp 误判为“不可用”。

真实 bootstrap smoke（跳过系统包/Docker/Playwright 下载）结果：Agent Reach v1.5.0，GitHub、
YouTube、V2EX、RSS、Exa、Jina 和 B站基础搜索共 7/15 渠道可用；仓库保持干净。需要 Cookie、
浏览器登录或额外账号的 8 个渠道没有自动启用。

### 49.3 代码链路（逐代码核实）

1. `chat.runtime.config.load_agent_reach_config_from_env()` 读取 enabled/home/timeout/output limit，
   进入 `SlotFlowRuntimeConfig.agent_reach_config`。
2. `chat.runtime.adapter.create_langgraph_agent_graph()` 显式传入
   `SlotFlowHarnessConfig.agent_reach_config`；builder 再交给统一 tool registry。
3. `build_harness_tools()` 构建 Agent Reach 工具，并同时放进主 agent 和 subagent 的
   `environment_tools`。若桥接自身关闭或 `SLOTFLOW_NETWORK_ENABLED=false`，返回空列表。
4. `harness/tools/agent_reach.py` 暴露五个固定只读工具：status、Exa search、Jina read、GitHub
   search、YouTube metadata。用户参数只能进入各操作预定义的位置；没有 command/argv/script 字段。
5. `FixedHostCommandRunner` 只解析 allowlist 中的五个 executable，搜索路径不含 cwd，调用
   `subprocess.run([binary, *args], stdin=DEVNULL, stdout/stderr=PIPE, timeout=...)`，没有 shell；cwd
   固定为 Agent Reach home，输出截断，并以环境变量名标记对 token/key/secret/password/cookie 值脱敏。
6. Jina 和 YouTube 的原始目标 URL 继续走 `network.validate_public_url()`，继承 HTTP(S)、DNS 和私网
   拒绝规则；YouTube 再限制 hostname。yt-dlp 的巨大 format URL 列表不会返给模型，只投影紧凑元数据、
   chapters 和字幕语言。
7. system prompt 的 `<slotflow-agent-reach-status>` 明示这是只读桥接而非 shell；多平台调研先 doctor，
   渠道不可用要如实报告，模型不能安装、更新、配置、发帖或修改远程状态。

### 49.4 安全与行为不变量

- Agent Reach/登录态只留宿主，不复制到 Docker，不把 `~/.agent-reach` 挂载给沙箱。
- 原有 `sandbox_exec` 仍是模型运行任意代码/安装依赖的唯一入口；固定桥接不是通用 subprocess tool。
- bridge 不接受 remote write，也不向模型提供 Agent Reach install/update/configure/uninstall。
- 重新运行 `bootstrap.sh` 是仓库唯一刷新入口；自动刷新基础组件不等于自动启用登录态渠道。
- Agent Reach 失败不删除原 `web_search`/`web_fetch`，两条只读网络路径可独立退化。

### 49.5 验证

新增 `tests/test_agent_reach_tools.py` 覆盖工具集合、双开关、固定 argv、结果上限、Jina route、GitHub
shape、YouTube 紧凑投影、async path、allowlist、无 shell、cwd、timeout、截断、secret redaction 和 env
映射；`test_harness_builder.py` 覆盖 prompt 结构锚点与有效开关。真实 StructuredTool smoke 已从
SlotFlow 边界验证 doctor JSON、Exa 搜索、Jina 读取 `example.com`、`gh search repos` 和 yt-dlp
YouTube 紧凑元数据全部成功；过程中还抓到并修正了当前 `gh` 字段名应为 `fullName`，以及原始
`--dump-single-json` 会因 formats 超过输出上限而截断，最终改成 yt-dlp `--print` 的固定字段投影。
本轮不把网络 smoke 放入离线 pytest，避免把单测稳定性绑定到 Exa/GitHub/YouTube 外部状态。

## 50. 迭代 42（2026-07-16）：Playwright MCP 内置化——状态不能按普通 MCP 每次重建

### 50.1 第一处根因：普通 MCP adapter 的 session 生命周期不适合浏览器

`langchain-mcp-adapters` 当前 `MultiServerMCPClient.get_tools()` 文档和源码都明确：默认生成的
LangChain tool **每次调用都会新建 MCP session**。这对无状态查询工具成立，但 Playwright 的
`browser_navigate → browser_snapshot → browser_click` 依赖同一个 browser/page/ref；按旧 loader 直接
接入会让每一步启动新进程，页面和 ref 全丢失。仅把 `@playwright/mcp` 填进环境 JSON 并不等于可用。

本轮给 `SlotFlowMcpServerConfig` 增加 `stateful` 元数据。`MultiServerMcpToolProvider.aload_tools()` 对
普通 server 仍走上游默认路径；对 stateful server 则用 `AsyncExitStack` 进入
`client.session(server_name)`，再将真实 `ClientSession` 传给 `load_mcp_tools()`，直到 provider
`aclose()` 才退出。配置关闭/变化时也先关闭旧 stack，不残留 MCP 子进程。

### 50.2 第二处根因：全局持久 session 会破坏并发对话隔离

SlotFlow 已支持多个 thread 同时运行。若把 stateful provider 继续放在全局 `SlotFlowRuntimeConfig`
复用，两个 run 会共享同一个页面/profile，一个 run 收尾还可能关闭另一个 run 的浏览器。
`RuntimeBackedAgentAdapter` 因而把真实 `MultiServerMcpToolProvider` 视作 template：每次
`stream_events()` 为当前 run 创建 provider，预加载 stateful session，把该 provider 显式传给 graph，
并在 async generator 的 `finally` 中关闭。自定义测试 provider 仍保持原注入语义。这样同一 run 的
多轮工具有状态，并发 run 之间无状态共享，异常/取消也会清理。

### 50.3 第三处根因：上游默认找系统 Chrome，bootstrap 下载的是锁定 Chromium

第一次真实 MCP smoke 能列出 24 个工具，但 `browser_navigate` 报：
`Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`。上游 CLI 默认选择系统
Chrome；阶段 1 的 `playwright install chromium` 下载的是与 pnpm lock 匹配的 Playwright Chromium。
没有理由再装一份系统 Chrome。新增固定、静默的
`frontend/scripts/playwright-mcp.mjs`：从直接依赖 `playwright` 读取
`chromium.executablePath()`，以 argv 方式追加 `--executable-path` 后启动锁定的
`node_modules/.bin/playwright-mcp`，stdin/stdout/stderr 原样透传，不污染 JSON-RPC stdout。

第二次 smoke 正确找到 Chromium，但动态链接器报 `libnspr4.so` 缺失。根因是下载浏览器二进制不等于
安装 Linux shared libraries。`bootstrap.sh` 现在在 pnpm install 后，对 apt 主机运行上游官方
`playwright install-deps chromium`，再下载 Chromium；`SLOTFLOW_SKIP_SYSTEM_PACKAGES=1` 同时跳过
这一步。非 apt 主机不伪造跨发行版包名，打印明确 warning。当前 WSL 通过该官方命令实际安装
`libnspr4`/`libnss3`/字体/Xvfb 等锁定浏览器需要的包。

### 50.4 内置 preset 与安全边界（按代码核实）

`load_mcp_config_from_env()` 默认追加受保护、置顶的 `playwright` server；
`SLOTFLOW_PLAYWRIGHT_MCP_ENABLED=false` 可完全移除，UI 可通过 base-server override 启停，但用户
不能删除或用同名 HTTP server shadow。preset 使用绝对 launcher、固定 PATH/HOME、workspace cwd，参数为：
headless、isolated、block service workers、omit image responses、codegen none、stdout output，以及
可配置 action/navigation timeout。没有 `--allow-unrestricted-file-access`，没有 vision/PDF/devtools caps。

当 `SLOTFLOW_NETWORK_ALLOW_PRIVATE=false` 时传入 localhost、loopback、RFC1918/link-local/metadata
host glob blocklist。上游文档明确说明 blocked-origins 不处理 redirect、不是安全边界，因此这里仅把它
作为纵深防护；网页文本、页面脚本和重定向仍是不可信输入，不能提升为系统指令。MCP 的 cwd 指向
`SlotFlowSandboxConfig.resolved_workspace_root()`；preset 启用时先创建该目录，Playwright 自动生成的
`.playwright-mcp` snapshot 也被限制在 workspace 内。

API record 增加 `stateful`，前端目录卡片显示“内置有状态”。store 合并时 base server 优先，修掉了
用户同名 server 可以覆盖环境/base server 的旧缺口；stateful 元数据在 enabled/pinned/reorder override
中保持，但不会从普通用户 HTTP server JSON 注入。

### 50.5 验证

- 新增 `tests/test_playwright_mcp.py`：固定 preset、workspace cwd、默认 caps、私网开关、默认/显式关闭、
  base server 防覆盖/防删除、API toggle、stateful session 保活与关闭。
- `tests/test_runtime.py` 新增 run-scoped provider 用例：连续两个 run 得到不同 provider，均在收尾关闭。
- `tests/test_distribution_contract.py` 同步检查 `install-deps` 顺序、launcher 可执行位、
  `chromium.executablePath()` 和 `node --check`。
- 真实 stateful MCP smoke：加载 **24** 个上游 browser tools，`browser_navigate(example.com)` 后再调用
  独立的 `browser_snapshot`，第二步仍返回同一 URL/标题和 `Example Domain` accessibility tree，证明
  session/page 没在工具间重建；provider 退出后进程正常关闭。
- 浏览器 shared-library 安装通过 Playwright 官方 apt 路径真实执行；非 apt 分支只做代码/契约验证。

## 51. 迭代 43（2026-07-16）：MarkItDown 单工具内置化 + 官方 Vision OCR

### 51.1 为什么不是把 `markitdown` CLI 交给模型

用户要求核心只保留 `convert_file_to_markdown`，并指出纯图片、扫描 PDF 必须注入大模型客户端。若把
`markitdown <path>` 当宿主 shell 命令暴露，会重复前述宿主执行风险，也绕开 SlotFlow workspace/artifact
边界；若只替换 `workspace_read`，又会把轻量源码读取和重型 Office/PDF/OCR 混成一层。本轮保持两条路：
`workspace_read` 继续做快速、安全预览；格式复杂或明确要 Markdown 时调用唯一新工具。

`harness/tools/markitdown.py::convert_file_to_markdown()` 只接受已经过边界解析的本地 `Path`，内部只调
上游 `MarkItDown.convert_local()`，没有 `convert()` 的 URL/response 多态 I/O。model-facing closure 再用
`SlotFlowWorkspace.resolve_path()` 限定输入，`normalize_artifact_path()` 限定可选输出到当前 thread artifact。
无 output_path 时返回内联 Markdown；有 output_path 时返回元数据和 artifact 路径，避免大文档重复进上下文。

### 51.2 Vision client 的两级选择

MarkItDown 0.1.5 的图片 converter 和 `markitdown-ocr` 0.1.0 实际都调用
`client.chat.completions.create(model=..., messages=[...image_url data URI...])`。按锁定源码实现两级客户端：

1. 当前 run 的 `BaseChatModel` 若有 model id 且 LiteLLM 公共 `supports_vision()` 返回真，
   `LangChainVisionClient` 将这一个模型包装成最小 OpenAI chat facade；messages 通过 LangChain
   `convert_to_messages()`，响应通过既有 `message_content_text()` 归一化。工具发生在模型发出 tool call 后，
   原始 chat model 尚未被工具绑定，不会形成“Vision 调用又发工具”的循环。
2. 用户要固定另一视觉模型时，显式配置 `SLOTFLOW_MARKITDOWN_VISION_MODEL/BASE_URL/API_KEY`，构造
   OpenAI SDK client。API key 在 dataclass 中 `repr=False/compare=False`，不进入 prompt/tool schema/result。

两者都没有时不盲调文本模型：普通 converter 继续工作；图片或空文本 PDF 返回
“无兼容 Vision client”的 warning。client 外再包一层只计数/记异常类型的 tracking facade，因为上游 OCR
service 会吞掉单图异常并返回空文本；因此结果能明确报告 OCR failure，而不是仅把“走过 Vision 路径”当成功。
`use_vision=false` 可按次禁止。prompt 默认要求忠实提取可见文字并保持
Markdown 阅读顺序；无文字时才给简短事实描述。

### 51.3 官方 OCR 路径与成本边界

启用 client 时 `MarkItDown(enable_plugins=True, llm_client=..., llm_model=...)` 会发现官方仓库随包发布的
`markitdown-ocr` entry point。锁定源码确认：插件以更高优先级替换 PDF/DOCX/PPTX/XLSX converter，先保留
原生文本，再 OCR embedded images；PDF 无文本时把整页渲染为 PNG 走同一个 Vision service。因此本项目
不再自己拆 PDF/图片，也不复制 OCR 协议。

上游插件本身没有费用上限，所以 SlotFlow 在调用前增加：input bytes、archive entry 数、总未压缩 bytes、
PDF pages、PDF/OpenXML embedded image 数；输出超限直接报错，不静默截断。ZIP/Office/EPUB 先用
`zipfile` 只读 central directory 做 zip-bomb 预检。PDF 页数用 pypdf，嵌图数尽可能用 PyMuPDF；计数失败
不替代上游解析错误，但明确超限必拒绝。所有同步 Magika/Office/PDF/LLM 工作经
`threaded_structured_tool` 放进 worker thread，不阻塞 async graph loop。

### 51.4 “全格式依赖”的实际含义

`backend/pyproject.toml` 已锁 `markitdown[all]` 和 `markitdown-ocr[llm]`，涵盖 PDF、DOCX、PPTX、
XLS/XLSX、Outlook、audio transcription、YouTube transcript、Azure extras 等 Python 依赖。真实源码审计又
发现音频 MP3/MP4 转 WAV 依赖系统 ffmpeg，图片/音频 metadata 可选 ExifTool；只装 Python extras 会出现
“包已装但格式能力不完整”。`bootstrap.sh::install_markitdown_system_dependencies` 因而在 apt/dnf/yum/
pacman/apk/zypper/brew 尝试安装 ffmpeg + ExifTool，apt 路径已在当前 WSL 真实安装。多媒体仓库不默认
可用的 dnf/yum/zypper 失败时给出明确 warning，不伪造成功；`SLOTFLOW_SKIP_SYSTEM_PACKAGES=1` 同时跳过。

### 51.5 图链路与验证

runtime env → `SlotFlowMarkItDownConfig` → harness config → unified tool registry；主 agent 和 subagent 的
environment tools 都含同一个 `convert_file_to_markdown`。system prompt 的
`<slotflow-markitdown-status>` 说明复杂文档优先转换、large result 用 output_path、Vision warning 必须如实处理。

新增 `tests/test_markitdown_tools.py`，真实创建并转换 HTML、CSV、DOCX、XLSX、PPTX、文本 PDF 和 ZIP；
另用 Pillow PNG + fake OpenAI-compatible client 验证纯图 data URI，用 PyMuPDF 创建无文本扫描 PDF 并证明
**上游 `markitdown-ocr` 真实调用 client**、OCR 文本进入 Markdown；还覆盖 selected-model facade、无 client
warning、workspace escape、thread artifact、input/output/archive/page/image 上限、nested ZIP 拒绝、上游吞掉的
Vision failure warning 和 env secret 配置。
这些 OCR 测试故意用 deterministic fake client，不消耗真实视觉 API；格式解析和官方 plugin 路径本身是真实的。

## 52. 迭代 44（2026-07-16）：Grok 中转流式读取超时与生图能力边界实测

### 52.1 `MidStreamFallbackError` 的根因不是 LangGraph 总 run 超时

失败 run `run_0c19cdd93ec7` 使用 custom `grok-4.5` 执行真实工具验证，总共运行约 3 分 26 秒后失败；
仓库存下的原始错误是 LiteLLM `MidStreamFallbackError`，内层为 OpenAI-compatible transport 的
`Timeout on reading data from socket`。逐代码核对发现 `chat/runtime/models.py` 给每个
`ChatLiteLLM` 硬编码 `request_timeout=30`。这个值进入 langchain-litellm `_client_params.timeout`，再进入
LiteLLM `completion/acompletion`；对流式响应而言，首块之前或任意两个网络读取之间连续 30 秒无数据就会
触发读取超时。LiteLLM 已经发出部分 chunk 时会把底层连接异常包装为 `MidStreamFallbackError`，但 SlotFlow
没有第二个等价模型可安全续写；在工具循环中盲目重放还可能重复远程/本地副作用。

当前修复保持 `max_retries=2` 和现有错误传播不变，不在 SlotFlow 层重放已经开始的流；只把过短的传输窗口
改为 `SLOTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS`，默认 300 秒。该变量在每次构建模型时通过
`load_positive_int_from_env` 读取，所以适用于 native provider 与 custom relay，且空值回退默认值、零值/
负数/非整数在请求发出前明确报配置错误。300 秒是单次模型请求的传输等待上限，不是整个 LangGraph run、
工具执行或浏览器 SSE 连接的总时限。

### 52.2 `.env` 中 Grok 中转的真实网络对照

测试只在进程内读取 `backend/.env`，没有打印或写入 API key。绕过 SlotFlow、直接请求配置的 custom relay：

- `GET /models` 成功并返回 15 个模型，既有 `grok-4.5`，也有 `grok-imagine`、
  `grok-imagine-image`、`grok-imagine-image-quality` 等独立图片模型；
- `grok-4.5` 非流式 Chat Completions 约 3.11 秒返回预期文本；流式探针约 14.71 秒收到首事件，完整请求
  约 19.21 秒，证明中转聊天链路可用，也证明 30 秒静默窗口对拥塞或复杂推理余量很小；
- 直接让 `grok-4.5` 聊天生成图片时，它明确返回自己只有文本能力，没有图片数据或 URL；
- `POST /images/generations` 携带 `size` 时中转返回 400 `Argument not supported: size`；按中转接受的最小
  payload 去掉 `size` 后，对 `grok-4.5` 和三个 `grok-imagine*` 模型均在 SlotFlow 之外直接得到中转/CDN
  的 502，另一次最小请求等待约 125 秒后得到 524；`POST /responses` + `image_generation` tool 也只返回
  reasoning/message，没有 image generation call。

因此本次“选 Grok 4.5 却不能生图”不是聊天投影把已有图片丢掉：当前中转的聊天模型没有返回图片，独立
图片端点又在绕过 SlotFlow 时已经失败。原 Grok 产品界面的生图能力来自产品侧工具/独立图片模型，不能由
`grok-4.5` Chat Completions 名称推导。SlotFlow 当前公开协议仍是聊天文本/工具/文件产物，也没有把
`/images/generations` 暴露成 agent tool；在中转图片端点恢复并稳定给出 URL/base64 之前，新增 UI 渲染或
图片工具只会把上游 502/524 包装成另一层失败，因此本轮不伪造“已支持生图”。

### 52.3 回归保护

`tests/test_runtime.py` 新增三组模型边界契约：默认 timeout 为 300 秒、环境变量可覆盖、无效值必须拒绝。
`backend/.env_example` 与 `AGENTS.md` 同步记录变量和 30 秒回归风险。model catalog 的两个 discovery
测试现在显式清除宿主 `CUSTOM_MODELS`，避免真实 `.env` 把“自动发现”场景偷换成“手工列表”场景。真实回归
继续通过 SlotFlow 的
`grok-4.5` 运行验证，而图片端点状态作为中转站外部依赖单独报告，不混同为 SlotFlow 测试通过。

### 52.4 TaskGroup 外壳不能覆盖真正错误

LangGraph v3 typed projections/AnyIO 在并发子流失败时可能向上传播嵌套 `ExceptionGroup`。此前
`chat/sse.py::make_error_event` 直接取最外层 `type(error).__name__` 与 `str(error)`，所以前端和 runs 表只能
看到 `unhandled errors in a TaskGroup (1 sub-exception)`；本机同一 thread 的多次 DeepSeek 失败都在约
31 秒处留下这个外壳，与旧 30 秒模型 transport timeout 的时间特征一致，但真正 leaf 已被隐藏，无法证明。

现在 SSE 边界递归遍历 exception group，选择第一个带消息的 leaf，再用它构造 `run.error.name/message`；
普通异常保持原样，traceback 不发给浏览器，`asyncio.CancelledError` 仍不被 `iter_business_events` 的
`except Exception` 吞掉。`tests/test_sse.py` 用两层嵌套 group 保护 `TimeoutError("socket read timeout")`
能够到达前端和 run 持久化边界。修复后若上游仍失败，用户会看到实际 LiteLLM/provider 异常，而不是并发框架
包装文本。
### 52.5 真正的 31 秒 TaskGroup 根因：失效的可选 MCP 阻断所有模型

单独调用 `load_runtime_config_from_env → build_mcp_tool_provider → ensure_mcp_tools_loaded`，不创建模型也不经过
SSE，稳定在 31.27 秒复现。实际 active config 有两个服务器：受保护的 stateful `playwright` stdio，以及用户
配置中启用但没有服务进程的 stateless `test-mcp`（`streamable_http`，`localhost:9999/test-sse`）。完整异常树为
`ExceptionGroup → httpx.ConnectTimeout('') → httpcore.ConnectTimeout → TimeoutError → CancelledError(deadline
exceeded)`；30 秒来自 langchain-mcp-adapters/MCP streamable HTTP 默认 connect/operation timeout，不是
LiteLLM。旧 loader 先成功打开 Playwright，再加载 `test-mcp`，后者失败后关闭整个共享 stack 并让 graph 在
创建模型前终止，所以 DeepSeek/Grok 都表现为约 31 秒失败。

`MultiServerMcpToolProvider.aload_tools()` 现在给每个 server 建独立 client，并给每个 stateful server 建独立
`AsyncExitStack`：成功 server 的 tools/session 保留；普通 `Exception` 只记录经过脱敏的
`load_errors[name] = "<LeafType>"`、写 warning 并继续（不把可能含 URL/凭据的异常正文写日志）；外层
cancellation/其他 `BaseException`
仍关闭所有已成功 stack 后传播。这样一个可选 HTTP MCP 下线不会让聊天和健康 Playwright 工具一起失效。
`tests/test_playwright_mcp.py` 覆盖“Playwright session 成功 + 后续 optional server 抛两层 TaskGroup/空
TimeoutError”场景，验证工具仍可调用、失败摘要为 `TimeoutError`、最终 close 正常。SSE 对空 leaf message
也回退到异常类型，不再把空字符串保存到 runs 表。

本机持久配置中的失效 `test-mcp` 已通过 `/api/mcp/servers/test-mcp` 禁用，因此当前 run 不再额外等待 30 秒；
即使以后另一个可选 MCP 暂时不可用，新的 per-server degradation 仍会让模型继续运行。
### 52.6 最终真机回归

禁用失效 `test-mcp` 后再次走真实 provider preload：1.39 秒完成并加载 24 个 Playwright 工具。随后在 Codex
内置浏览器打开 `http://localhost:3000/`，从模型菜单选择 `Grok 4.5`（Pro + Thinking），发送“请只回复
SLOT_GROK_OK，不要调用工具。”；页面正常显示 reasoning 步骤和最终 `SLOT_GROK_OK`，数据库 run
`run_71e8f94dee00` 在约 11.55 秒内变为 `completed` 且 `error=null`。这条验证覆盖前端发送、SSE、每 run
MCP preload、LangGraph、custom LiteLLM transport、消息持久化和前端渲染，不再出现 TaskGroup 或 30 秒读取
超时。

最终 `make verify` 通过：backend 409 passed / 1 skipped，frontend Vitest 2 passed，TypeScript、Knip 和
Next.js production build 全绿；仅保留既有 Starlette/httpx 与 LangGraph v3 beta warning。

## 53. 迭代 45（2026-07-16）：429 请求放大、确定性标题与开发热重载隔离

### 53.1 LangSmith 证明 429 来自中转配额，但 SlotFlow 曾额外消耗配额

官方 LangSmith 配置已真实连通（project `SlotFlow`），可以读取 root graph、child LLM/tool run、错误、metadata 与完整调用时序。最新“只调用一个 Subagent 工具试试”的成功 root graph 运行约 31.7 秒；主 Agent/Subagent 工作流本身完成了 5 次必要 LLM 调用。root 完成后，旧配置又几乎同时发起两次与用户答案无关的调用：

- proactive long-term memory extractor，run `019f6b2f-52a7-7d81-b6b6-99182889009c`，system prompt 以 `You extract DURABLE long-term facts...` 开头；
- `title_agent`，run `019f6b2f-52b3-7f60-8989-b14f84440cb3`。

两者都使用本轮所选 custom relay 模型并返回真实 429。稍后的另一条 trace 在 28 秒内进行 5 次前台 `ChatLiteLLM` 调用，第 5 次直接 429，与中转站很低的 requests-per-minute 配额一致。因此 429 本身由上游限流产生，不能在 SlotFlow 内“修好”；但 post-turn title + memory 两次隐藏请求会耗尽同一个配额桶，使下一次必要的 Agent/Subagent 调用更容易失败。不能用 DeepSeek 或另一家模型来搬运这些请求：那只会增加新的凭据、可用性、费用和行为不确定性，也违反“用户所选模型是唯一模型来源”的边界。

### 53.2 当前严格配额策略：本地确定性行为，不新增模型依赖

`title_generation.maybe_generate_thread_title()` 在 `SLOTFLOW_TITLE_MODEL_ENABLED=false` 时，直接调用 `fallback_title()`：压缩第一条用户消息的空白并截断到 60 字符，不执行 `create_chat_model()`。仓库默认值在环境变量缺失时本来就是关闭；本轮把 `.env_example` 也改为 `false`，使新环境默认不会为了标题多发一次模型请求。专用标题模型仍只是旧功能的显式 opt-in 配置能力，不是当前运行路径；本机 `.env` 不再保留 DeepSeek 标题覆盖，避免误启用。`tests/test_title_generation.py` 将 `create_chat_model` 替换为一调用就失败的 guard，直接证明关闭时只走本地 fallback。

本机严格-RPM profile 同时设定 `SLOTFLOW_PROACTIVE_MEMORY_EXTRACTION=false`。这只关闭每轮结束后的主动抽取 LLM 调用，不改变主 Agent/Subagent 的模型选择，也不引入单独的 memory model；用户显式调用 `memory_save` 的工具能力和已有长期记忆读取保持原有语义。对于配额宽松且明确需要主动抽取的部署，原功能仍可显式开启；`.env_example` 标注其每轮多一次请求的成本。Ultra/Subagent 工作流自身需要的多轮前台推理不能在不改变 agent 语义的情况下合并，因此极低 RPM 中转仍可能对第 5 次必要调用返回 429，这属于保留的上游边界。

### 53.3 `.slotflow/workspace` 不得触发 Uvicorn 重启

旧 `make dev` 使用裸 `uvicorn --reload`，WatchFiles 会递归观察整个 `backend`。工具在 `.slotflow/workspace/.sandbox/palindrome_test.py` 写入正常运行产物时，终端明确打印 `WatchFiles detected changes ... Reloading`，随后 Uvicorn shutdown；这会截断正在进行的 SSE/工具流、让 LangSmith trace 停留 pending，并使用户重试，进一步放大请求量。

`Makefile` 现在使用 `--reload --reload-dir app`，只观察后端源码。`tests/test_distribution_contract.py` 固定该命令并禁止把 `.slotflow` 配成 reload dir。运行产物、上传文件与 sandbox 代码变化不再重启服务，编辑 `backend/app` 仍保留开发热重载。

### 53.4 Pregel v3 警告是噪声，不是 429 根因

SlotFlow 有意消费 LangGraph v3 typed projection stream；`The v3 streaming protocol on Pregel is experimental.` 是依赖库对每个 run 重复发出的 beta 提示，不代表请求失败，也不触发 LiteLLM 重试。`LangGraphEventAgentAdapter` 只在调用 `graph.astream_events(version="v3")` 的边界用 `warnings.catch_warnings()` 忽略该完整消息和 `LangChainBetaWarning` 类别；没有全局关闭 beta/UserWarning。adapter 回归测试同时发出这条 beta warning 和一条无关 UserWarning，验证前者消失而后者仍可观察。

### 53.5 修复后的真机与 LangSmith 回归

重启 `make dev` 后，Uvicorn 明确报告只观察 `['/home/xf/code/SlotFlow/backend/app']`，reloader PID `1386251`、worker PID `1386365`。在 `backend/.slotflow/workspace/.sandbox/` 创建并删除临时运行产物，等待 WatchFiles 后 worker PID 仍为 `1386365`，stderr 没有 `WatchFiles detected changes`、`Reloading` 或 `Shutting down`，证明 sandbox 写入不再中断服务。

随后通过 Codex 内置浏览器在 SlotFlow 新建 `glm-5.2`（Pro + Thinking）对话，发送“只回复 SLOTFLOW_POSTRUN_OK，不要调用工具。”。页面返回精确文本；数据库 run `run_4ab91cdc6d24`（thread `thread_4d5e95094405`）约 6.35 秒后为 `completed`、`error=null`，thread title 是第一条用户消息的本地 fallback。LangSmith root trace `019f6ba6-bb48-7cf2-a94b-7b4019adf443` 为 success，完整 trace 只有 11 个 runs，其中只有 1 个 LLM run：`ChatLiteLLM` / `glm-5.2`，约 5.08 秒成功；不存在 `title_agent`、proactive memory extractor 或紧随其后的额外 root trace。这直接证明严格-RPM 本机配置没有重复请求，也没有借用 DeepSeek/其他供应商。

最终 `make verify` 全绿：backend 411 passed / 1 skipped，frontend Vitest 2 passed，TypeScript、Knip 与 Next.js production build 通过。warning summary 只剩既有 Starlette/httpx deprecation；重复 Pregel v3 beta warning 已消失。
## 54. 迭代 46（2026-07-17）：Context Epoch、渐进式工具空间与本地缓存指标

### 54.1 真实输入构成与外部实现调研

最新 `glm-5.2` LangSmith 调用实测为 107,372 input tokens / 166 output tokens；137 条内部消息由 1 System、10 Human、61 AI、65 Tool 构成。ToolMessage 内容约 153,605 字符，AIMessage 内容约 102,561 字符；54 个完整工具 Schema 约 30,617 字符。因此先治理历史 AI/Tool 消息，再治理重复 Schema。Codex/Pi/DeerFlow 的公开代码与 2026-07-17 检查结果单独保存在 `docs/research/agent-context-tool-disclosure-2026-07-17.md`，其中记录精确来源、原生 deferred 与 fallback 的差异以及不能把公开仓库推断为私有产品实现的边界。

### 54.2 Context window 来源和 epoch 语义

`runtime/models.py::resolve_model_context_budget` 完全本地解析：per-model `SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON` 优先；随后读取 LiteLLM 随包 model metadata；未知 custom model 使用 `SLOTFLOW_DEFAULT_CONTEXT_WINDOW_TOKENS`（默认 128000）并标记 source=default。`SLOTFLOW_CONTEXT_RESERVE_TOKENS`（默认 16384）从 window 中扣除，得到实际输入触发预算；reserve 不得大于等于 window。builder 只把现有 summarization trigger 向下限制到该预算，不把固定 600000 当作所有模型的真实窗口。

摘要不再用 `RemoveMessage` 改写 canonical `messages`。summarization node 只更新 `llm_input_messages` 和 `context_epoch={source_message_count, source_signature, messages}`；`pre_model` 验证 canonical prefix 签名未变化后复用冻结 epoch，并仅追加其后的新消息。回滚/编辑导致前缀不匹配时 epoch 失效并回到 canonical 投影。这样 checkpoint 保留完整 Tool/AI 历史，同一 epoch 不反复改写缓存前缀。

`context_archive_search/context_archive_read` 使用 LangGraph `InjectedState`，只能读取当前 thread 运行时 canonical state，字段不进入模型工具 Schema；结果有 limit/offset/max_chars 上限。模型不获得 SQLite 路径或 SQL 权限。System Prompt 稳定提醒：摘要缺少旧细节时使用 Archive 工具，不得猜测。当前 Archive 的 durable source 是 canonical checkpointer state，而不是让模型解析 `checkpoints.sqlite3`；独立业务 Archive 表仍属于后续可迁移存储实现。

### 54.3 分层延迟工具空间

`tool_spaces.py` 把非核心工具分为 workspace、sandbox、browser、network、documents、extensions、memory。初始模型只绑定核心工具和存在的 `<space>_tools` 加载器；加载器 description 稳定列出该空间的精确工具名与一行能力，调用参数使用 exact names，不依赖严格关键词。加载器通过 LangGraph `Command` 更新 `promoted_tool_names` 并追加 ToolMessage；下一次 agent 调用根据 state 动态 `bind_tools(core + loaders + promoted)`。promotion 在 epoch 内只增不减。ToolNode 虽保留完整执行对象，但 wrapper 对未激活调用返回 `tool_not_activated`，因此模型不能靠幻觉绕过披露。

Browser MCP 按 `browser_*` 进入 browser 空间；带 MCP metadata 的其他未知扩展进入 extensions 空间。完整 MCP session 生命周期仍是 per-run，工具 promotion 不跨 run 复用 session-bound callable。Skills 继续使用既有 `top_level_skills()`：System Prompt 只列顶层 Skill，nested member Skill 不单独出现；Skill 内容读取是资源渐进披露，与 function Schema promotion 是两条独立边界。

Subagent 的 `task_tool.tool_spaces` 最多接受三个 workspace/sandbox/browser/network/documents/extensions/memory 空间；`all`、`*`、未知空间和四个以上空间均 fail-closed。未显式提供时按 profile 给最小默认集合。Child graph 继续关闭 todo、clarification/HITL、memory middleware、summarization 和递归 subagent，因此父模型不能把全部工具偷渡给 child。

### 54.4 本地 usage/cache 指标

每个 `RuntimeBackedAgentAdapter` run 创建 `RunUsageCollector` 并经 RunnableConfig callbacks 传给 LangGraph。collector 只保存 call id、model/provider、状态、latency/TTFT、message/tool/schema 数量及 token usage；不保存 prompt、tool 参数或输出。它兼容 LangChain normalized usage、OpenAI `prompt_tokens_details.cached_tokens` 与 Anthropic `cache_read/cache_creation` 字段。字段缺失时 `cache_status=unknown`，不会误记为 miss。adapter 在 `run.finished` 前发 `run.usage`；ChatRepository 把 JSON 写到独立 `run_metrics` 表，避免依赖 LangSmith 和已有 runs 表迁移。

### 54.5 重试边界

`ChatLiteLLM` 的同步/异步 completion 建连阶段只对 Timeout/APIError/APIConnectionError/RateLimitError 使用 Tenacity exponential retry；默认五次重试（首次之外），min/max/multiplier 均由 `.env` 控制。stream iterator 已开始后的异常不经过该 decorator，因此不会重放部分输出。

`agent` 对 provider context-overflow 文本（中英文 maximum-context/prompt-too-long 标记，含 ExceptionGroup）单独处理：canonical messages 不变，每次重试只对 model-facing input 使用更小、经 dangling-tool repair 的尾部投影，并按 `SLOTFLOW_CONTEXT_OVERFLOW_RETRY_DELAY_SECONDS * attempt` 等待；默认最多五次。认证、权限、普通 BadRequest、取消、已执行工具和 mid-stream failure 不进入整轮重试。该 emergency path 是正常 model-aware summarization 未能提前避免 overflow 时的最后防线。
### 54.6 真机减量与指标验证

热重载完成后通过本机 `/api/chat` 使用真实 custom `glm-5.2`（Flash）运行最小请求，thread `thread_d8879de7acb6`、run `run_73cb9d60a376` 正常 `run.finished` 且无 `run.error`。SSE 在 finished 前发出 `run.usage`；SQLite `run_metrics` 持久化成功。该真实调用从改造前的 54 tools / 约 30,617 Schema 字符下降为 11 tools / 8,885 Schema 字符；usage 为 input 4,848、output 301、total 5,149，TTFT 2,561ms、latency 6,811ms。中转未返回 cached token 明细，因此正确记录 `cache_status=unknown`、`cached_input_tokens=null`,没有伪造 miss。最终 `make verify` 全绿：backend 420 passed / 1 skipped，frontend Vitest、TypeScript、Knip 和 Next.js build 通过。

### 54.7 后续修复（2026-07-18）：`promoted_tool_names` 并发写入根因

**症状**：前端浏览器报 `At key 'promoted_tool_names': Can receive only one value per step. Use an Annotated key to handle multiple values.`(LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE`)。

**根因**：渐进式工具空间披露落地时,`SlotFlowAgentState.promoted_tool_names` 是一个**无 reducer** 的普通 last-write 通道(`NotRequired[list[str] | None]`)。但模型完全可以在**同一个模型步**里一次发出多个 `*_tools` 加载器调用(并行 tool_calls);ToolNode 会并发执行它们,每个 `load_space_tools` 都返回 `Command(update={"promoted_tool_names": ...})`。同一步对同一 key 的第二次写入没有归并规则,LangGraph 直接拒绝。这不是前端 bug,而是 state schema 缺 reducer——是 §54 工具空间特性自带的并发缺陷,只是要多空间同时激活才触发。

**修复**:给该通道加**保序去重的并集 reducer** `merge_promoted_tool_names`,字段改为 `NotRequired[Annotated[list[str] | None, merge_promoted_tool_names]]`。并集与工具披露的语义天然一致——一个 context epoch 内只增不减,且对重复激活幂等;并发写入被折叠成有序并集,已激活项不会重复。加载器本身无需改动(它返回 `[*current, *added]`,reducer 会去重)。

**验证**:用 fan-out(`Send` 到两个并发节点各写 `promoted_tool_names`)复现,修复前抛 `INVALID_CONCURRENT_GRAPH_UPDATE`,修复后合并为有序并集。回归测试 `test_promoted_tool_names_reducer_is_ordered_union` 与 `test_concurrent_tool_space_promotion_does_not_raise_invalid_update` 固定该行为。

**调试手段沉淀**:本次同时把 LangSmith 链路审查方法写进 `AGENTS.md`(见 "Debugging with LangSmith")——LangGraph 图的每个节点/模型调用/工具调用/interrupt 都是可展开的 span,这类"某节点并发写冲突"能被精确定位到触发它的 span,是排查链路问题的首选。

### 54.8 后续修复（2026-07-18 下午）：grok 中转"工具全部 tool_not_activated"三段根因

用户报告主模型(custom 中转 `grok-4.5`)所有工具调用都返回 `tool_not_activated`——**包括核心工具**——且一次压缩后继续对话丢失全部上下文。用今天实际对话 + LangSmith + 真机 grok/glm 排查,定位到三个独立根因,均已修复并真机验证。

**根因 A（真正让 grok 全盘失效的）：中转流式装配丢失 tool_call 名字。** 在真实 harness 流里,`agent` 节点 `ainvoke` 返回的 AIMessage,其 `additional_kwargs["tool_calls"][j]["function"]["name"]` 是正确的(如 `web_search`),但 LangChain 解析出的 `message.tool_calls[i]["name"]` 却是**空字符串**(每个 chunk 的 `index` 都是 0)。空名字导致 ToolNode 无法分派——任何工具(核心的也一样)都失败。**这不是渐进式工具空间的问题**,gate 只是把它显示成 `tool_not_activated`。孤立 `ainvoke`/`astream` 复现不出(简单 prompt 名字正常),只在真实流里必现。修复:`litellm_provider.py::repair_streamed_tool_call_names` 按 `id`(全缺失且数量一致时退化为按位置)从 `additional_kwargs` 回填空名字;`graph.py` 的 `agent`/`agent_sync` 在拿到 response 后调用它。子代理走同一 graph,自动受益。回归 `tests/test_tool_call_name_repair.py`;真机 grok 建 `ok.md` 成功、`na=0`。

**根因 B：渐进式工具空间把日常工具全 gate 在 loader 后。** 原设计 gate 了所有非核心工具(workspace/artifact/web/sandbox/memory/documents),模型必须先调用 `*_tools` loader 才能用,弱/中转模型很少这么做。改为**默认只 gate `browser,extensions`**(schema 最臃肿、最少用),其余日常工具默认进入 `initial_names`、turn-1 直接可调用;`SLOTFLOW_TOOL_SPACES_GATED` 可调。loader→promote→可调用链本身正确(确定性测试 `test_gated_space_becomes_callable_after_loader_promotes_it` 固定)。`tool_spaces.py::assemble_tool_spaces(gated_spaces=...)` + `builder.py` 读 env + prompt 只列被 gate 的空间。

> ⚠️ **后续(2026-08-14,§59)**:整套渐进式披露连同 `SLOTFLOW_TOOL_SPACES_GATED` 已删除——把 provider 前缀缓存算进来之后它是净负收益。本段保留作为历史记录。

**根因 C：context epoch 每轮被重置 → 反复摘要 → keep 窗口滑走旧消息。** epoch 的 `source_signature/source_message_count` 在 `make_summarization_node` 用**原始** `state["messages"]` 计算,而 `pre_model` 用 `repair_dangling_tool_calls(messages)` 复算——历史里只要有 dangling tool call,两者视图不同 → 签名必失配 → epoch 每轮重置 → summarization 每轮重触发 → 固定"keep 最近 20 条"窗口滑动,把更早的用户轮次(如"A")挤掉。修复:两处都用 repaired 视图;投影逻辑抽成纯函数 `graph.py::project_with_context_epoch` 并测试(`test_context_epoch_is_reused_across_appended_turns_not_reset` / `..._resets_when_prefix_signature_changes`)。

**旁证:issue3(注释 DeepSeek 仍可用)非代码 bug**——磁盘 `.env` 第 12 行 `DEEPSEEK_API_KEY=` 当时并未真正注释,进程据此加载;真正注释保存后完整 `make kill && make dev` 即消失。

### 54.9 后续修复（2026-07-19）：grok 思考流反向灌回 + 压缩"只加不减"（入站清洗根因）

用户用 LangSmith 复查 `thread_4f22351440e3` 后报告两点:①支持深度思考的模型(grok-4.5 经 ChatLiteLLM 中转)多轮后 Input Tokens 不可逆膨胀(第四轮飙到 12.2K);②`SlotFlowSummarizationMiddleware` 节点确实跑了、也算出了紧凑摘要,但紧随其后的 `agent` 输入不但没变小、反而更大。并指出这次问题在**接收模型回复**层面(出站方向此前已按 langchain-litellm#222 处理过)。真机 grok-4.5 复现,定位到**同一个入站根因**,已修复并真机 + 直测验证。

**根因:grok 的思考流以逐 token 的 `{"type":"thinking",...}` 块列表塞进 `AIMessage.content`(实测单条回复 = `list[47 blocks]`),外加全文进 `additional_kwargs["reasoning_content"]`。** 出站 `_create_message_dicts` 早已把块列表折叠成纯字符串(所以**发给中转的 payload 是干净的**),但 langchain-litellm 把**原始**块列表留在消息对象上——而被 checkpointer 持久化、被 `pre_model` 每轮重新投影的正是这个原始对象。于是它污染 `llm_input_messages`、触发摘要的 token 计数、LangSmith 记录的输入;更关键——`SummarizationMiddleware` 逐字保留最近 N 条消息,这些消息各自还拖着几十个 thinking 块 → 摘要虽加了、"最近保留区"却从没瘦下来(用户看到的"只加不减")。同时 `reasoning_content` 全文按既有出站契约会被回灌给模型(§reasoning 契约测试固定),长思考多轮累积即 backwash。

**修复(纯入站,不动出站契约):`litellm_provider.py::sanitize_reasoning_message`**——在 `agent`/`agent_sync` 拿到 response 后(紧接 `repair_streamed_tool_call_names`)调用:把 `content` 的思考块列表用同一套 `_without_reasoning_metadata_blocks` 折叠成答案字符串,并从持久化对象上丢弃 `reasoning_content`(思考文本供 UI 的 reasoning 框是**从实时流**单独捕获进 `MessageRecord.metadata` 的,checkpointer 里根本不需要;把 CoT 每轮回喂模型纯属浪费——OpenAI 系 reasoner 会重新推理,DeepSeek reasoner 更是禁止回传)。**签名 `thinking_blocks` 刻意保留**(Anthropic/Bedrock 扩展思考的工具循环续接依赖它)。出站 `_create_message_dicts` 与既有 reasoning 契约测试一字未改。

**验证(真机 grok-4.5):**
- 入站清洗直测:单条真实回复 `content` 从 `list[47 blocks]/3758B` → `str/1455B`,`reasoning_content`(589B)丢弃,答案完整保留。
- 5 轮真机(trigger=1200、keep=4、带 MemorySaver):**每个 agent 调用 `leaked_thinking_blocks=0`**(修复前 turn≥3 会累积上一轮的成百块);epoch 从 turn3 起被复用(`source_count` 5→7→9 递增=只追加、`epoch_msgs` 恒为 5=压实前缀不再膨胀);多次摘要后最终仍准确复述 turn1 的 `ORION-7/张伟/350 万`(retention 全 True)。
- 回归 `tests/test_provider_reasoning_contract.py`(新增 3 个 sanitize 测试)+ 全量 `uv run pytest -q -k "not live"` = 434 passed。

---

## 55. 迭代 47（2026-07-26）：上下文工程三改（cache 稳定 + 工具结果卸载）+ 为什么不退回 create_agent

> 对照当前代码核实。离线 `uv run pytest -q -k "not live"` **443 passed**（新增 7 用例）。三改都是
> 「让有限上下文窗口塞对信息」的工程，核心衡量指标是 **prompt 前缀缓存命中率**（agent
> input:output≈100:1，缓存 vs 未缓存差 ~10×）。

### 55.0 TL;DR
- **A（改代码）**：召回的长期记忆 + 每步 todo 控制块**移出 `system_prompt` 前缀**，改由 `agent` 作为
  **最后一条 user 角色 `<system-reminder>` 消息**追加在所有会话消息之后。前缀稳→缓存能命中。
- **B（无需改代码，记录结论）**：工具 schema 缓存本分支已是稳态——`DEFAULT_GATED_SPACES={browser,
  extensions}`，没 promote 时 `tools` 数组每步逐字节相同。
- **C（改代码）**：单条工具结果超阈值（默认 16000 字符）→ 全文写工作区隐藏文件、上下文只留「引用+
  预览」，模型按需 `workspace_read/workspace_grep` 分块回读（Manus「file system as context」）。

### 55.1 A：易变上下文移出 system 前缀（根因：前缀每轮变→打穿 prompt cache）
- **问题**：`pre_model` 原先把召回记忆（`append_memory_system_message`）+ 每步 todo 控制块拼进
  `system_prompt`。provider 可缓存前缀顺序是 `tools → system → messages`；记忆随 query 每轮变、todo
  每步变 → system 段每步变 → 从 system 往后（含全部 messages）前缀哈希全废，缓存永不命中。
- **改法**（`graph.py::make_pre_model_node`/`make_agent_node`，`state.py` 新增 `model_input_suffix` 通道）：
  `system_prompt` 只留稳定基座（base + skills preflight）；记忆 + todo 组进新的 `model_input_suffix`
  字符串通道；`agent` 用 `_model_input_suffix_message()` 包成 `HumanMessage("<system-reminder>…")` 拼在
  `[System(base), *messages]` **之后**。
- **三个必须守的点**：
  1. **append-only**：放到所有 messages **之后**，前缀（tools+system+历史）逐字节稳定，只扰动尾部——顺带
     蹭到「最近注意力」（Manus recitation）。
  2. **user 角色而非 system**：让送模型的消息序列**始终以 user/tool 结尾**，兼容对顺序更严的中转 provider
     （部分会拒绝「最后一条是 system」）；外层 `<system-reminder>` 让模型仍按带外提示理解。
  3. **不进 `messages` 通道**：`model_input_suffix` 是普通字符串通道（同 `system_prompt`），`agent` 调用时
     即时构造那条消息、绝不写回 `messages` → 既不流式泄漏、也不被 checkpointer 回放（**沿用 §48/2026-07-15
     泄漏边界**）；也**不进 `llm_input_messages`**，故下游 summarization/epoch 不折进摘要（epoch 干净）。
- **验证**：`test_recalled_memory_rides_trailing_suffix_not_system_prefix`——真图跑一轮，断言记忆**不在**首条
  system、**在**尾部 user `<system-reminder>`（顺带证明检索命中）。

### 55.2 B：渐进披露与工具缓存（已是务实版，勿误改）
- `model_for_state` 每步 `bind_tools(initial ∪ promoted)`；`initial_names` 含全部日常工具（只 gate
  browser/extensions），`tool_by_name` 是插入序稳定 dict → **没 promote 时每步 `tools` 数组逐字节相同、
  前缀缓存稳**。只有 browser/extensions 被 loader 激活时打断一次（只增不减、至多一两次/会话）。
- 想彻底不动 `tools` 只剩「Mask, Don't Remove」（约束解码遮蔽），需 provider 支持，留作可选激进版。
  **结论：本分支无需改代码；别为「做点什么」去动它。**

### 55.3 C：超长工具结果卸载（`steps/tool_output_offload.py`）
- **机制**：`make_tools_node` 的 `wrap`/`awrap` 在安全包装后调 `maybe_offload_tool_message`。一条 `ToolMessage`
  文本超 `tool_output_offload_max_chars`（默认 16000；env `SLOTFLOW_TOOL_OUTPUT_OFFLOAD_MAX_CHARS`，开关
  `SLOTFLOW_TOOL_OUTPUT_OFFLOAD`）→ 全文写 `<workspace_root>/.slotflow_offload/<tool>-<callid>.txt`，
  `ToolMessage` 换成 `{path, chars, lines, preview 首尾, how_to_read}`。模型用**默认激活的**
  `workspace_read('<path>')` 取全文 / `workspace_grep('<kw>','<path>')` 定位。
- **防御**（都在 `tests/test_tool_output_offload.py`）：跳过 `workspace_read` 等读文件工具（避免写回工作区的
  循环）；多模态/含非文本块 content 原样放过；写盘失败/只读/超 `max_write_bytes` → 回退「内联截断+预览、
  path=None」，**绝不抛异常**；`awrap` 的卸载走 `asyncio.to_thread` 不阻塞事件循环。
- **为什么不用外部 DB/句柄表**：SlotFlow 早有工作区文件系统 + `workspace_read/grep`（默认激活）+
  `context_archive`，缺的只是「写时把大输出落盘 + 留句柄」这一层。复用文件系统 = 最贴 Manus 模型、零新增工具。

### 55.4 为什么不退回 create_agent（诚实版：可维护性取舍，不是能力取舍）
- **核心判据（最重要的一条）**：分不分图，看 **subagent 之间要不要共享、互相看得见 state**——子任务是黑盒、只回结果、
  彼此不看中间状态 → subagent 当工具、create_agent 够；几个 subagent 要共享同一块 state / 看彼此中间产物 / 并行汇总
  → graph 编排（共享状态 + 并行合并 + per-node checkpoint/中断是图独有的机制）。**只要 subagent 还是工具，SlotFlow
  就是"一个环"，纯 create_agent 就够；图是可读性偏好 + 多 agent 保险，不是必需。**
- **先纠正一个常见（我自己也犯过的）错误框架**：别说「这些流程 create_agent 做不到」。**create_agent
  本身就编译成一张 LangGraph 图**，每个中间件钩子都是图里一个节点；凡手写图能表达的控制流，中间件基本也能
  （`before/after_model` + `wrap_model_call/tool_call` + `jump_to` model/tools/end）。旁证：**SlotFlow 重构前
  这些全是中间件**（澄清门 = `abefore_model` 里 `interrupt()`；todo 自环 = after_model `jump_to="model"`；
  压缩 = wrap 一层投影）。所以三处并非「做不到」。
- **真实差别 = 显式 vs 隐式的控制流表达**：中间件把流程藏在「钩子类型 + 列表顺序 + 各处 jump_to」里，且不同
  钩子合成规则不同（`wrap_*` 洋葱嵌套、before/after 各自方向）——确定但要心算；图把每条路径写成可读、可 diff、
  可按节点名看 trace 的边。定制点少时中间件更短更清楚；到十来个、且多处带控制流跳转/子调用要单独过滤流时，
  「隐式 + 心算」开始让人出错——**迁移的本质是赌「控制流已复杂到：画出来比塞进钩子桶更好维护」，是复杂度阈值
  上的判断，不是能力必需。**
- **唯一算「结构性」的边（仍是更自然、非唯一可行）**：图能有任意的、有名字的独立节点并路由到任意节点；像
  `prepare`/`triage_gate`/`finalize` 这种「每轮一次、与模型调用无直接关系」的工位做成独立节点，比硬塞进
  before_agent/before_model 顺眼。
- **判据（软化版）**：复杂度**长在流程控制**（分支/门/回环/HITL/子流过滤）→ 显式图更好维护；只是**工具多/推理长**
  的标准循环 → create_agent 够。SlotFlow 属前者，故**保留**；但这是维护性判断，不是「非图不可」。
- **该不该退回**：既是可维护性取舍、且已有可跑的图 → **现在退回不划算**（重写+重测只换来少点样板）；从零起步
  用 create_agent 当默认也合理。DeerFlow 反向不矛盾（它复杂在多 agent 协调、单 agent 标准 ReAct）。

## 56. 迭代 48（2026-07-26）：停止(取消)与 429 的错误分类加固

### 56.0 TL;DR
两类事、一个判据。**停止 ≠ 给模型发信号**——模型收不到「停止」，停止是前端断 SSE → Starlette 抛
`asyncio.CancelledError` → 一路向下拆连接（httpx 请求取消 → provider 停生成）；任何一环把它吞掉，停止按钮就
静默失效。**429/限流恰恰相反——它是异常**，危险处在 `task_tool` 内部：子代理撞限流 → 异常在工具里 →
`tool_safety` 把它当普通失败转成 `tool_execution_error` → 模型误以为「任务本身失败」、还永久污染历史。
本迭代把这两条从「靠运气对」变成「写明白 + 主动分类」。

### 56.1 逐条核对（动手前先证明我们本来有什么、缺什么）
- **裸 `except:` / `except BaseException` 吞取消**：全仓仅 2 处命中——`mcp/loader.py:105` 是
  `except BaseException: aclose(); raise`（清理后**重抛**，正确）；另一处在技能模板 `.md`（非代码）。✅ 无隐患。
- **`CancelledError` 能否穿透 tool_safety**：`graph.py` 两个 wrapper 用 `except Exception`——`CancelledError`
  是 `BaseException` 不是 `Exception`，**天然放行**。✅ 成立，但「靠语言特性」，没写明。
- **SDK `max_retries` 没被关**：`models.py` `DEFAULT_MODEL_MAX_RETRIES=5` 走 `load_positive_int_from_env`
  （**恒 ≥1、关不掉**）+ 完整退避 + `litellm_provider` `stop_after_attempt`。✅（对照故事 4 的 `0→2` 教训）。
- **停在 tools 节点的孤儿 tool_call 兜底**：`repair_dangling_tool_calls`（`steps/dangling_tool_call.py`）由
  `pre_model` 每步先修，防下一轮 400。✅
- **干净断开标记 run 取消**：`routes.py:302 except CancelledError: update_run_status('cancelled'); raise`。✅
- **retryable / permanent 分类**：`tool_safety` 一律转 `tool_execution_error`，**无分类**。❌ 缺——最高 ROI。
- **子进程可取消 + docker kill**：`sandbox_exec` 走 `asyncio.to_thread(subprocess.run)`（阻塞、不可取消）、
  `agent_reach` 同理。停止后容器/宿主子进程继续跑到超时。❌ 缺（见 56.3 为什么这次不动）。
- **非干净断开（拔网/飞行模式）**：Starlette 靠 `http.disconnect`，拔网不触发 → 整轮跑完落库。⚠️ 框架固有，基本无解。

### 56.2 做了什么（只做真正有用、且改动小的两条）
1. **`tool_safety` 显式重抛 `CancelledError`**：两个 wrapper 从 `except GraphBubbleUp: raise` 改为
   `except (GraphBubbleUp, asyncio.CancelledError): raise`。不改行为（本就穿过 `except Exception`），把「靠语言
   特性」变成「写明白」，并防日后有人把 `except Exception` 放宽成 `except BaseException` 时意外吞掉取消。
   与 GraphBubbleUp 并列写清「两者都是控制流、不是错误」。
2. **`tool_safety` 分类可重试/永久错误**（收益最大）：新增 `litellm_provider.is_retryable_infra_error`
   （`_RETRYABLE_INFRA_EXCEPTIONS = {Timeout, RateLimitError, APIConnectionError, ServiceUnavailableError,
   InternalServerError}`，含 `BaseExceptionGroup` 递归）。wrapper 的 `except Exception` 里先判定：命中 →
   **重抛**（让整轮像 agent 节点的瞬时错误一样干净失败、state 不落假 ToolMessage，用户重发即可）；未命中
   （参数错/文件不存在/exit≠0/一般异常）→ 维持转 `tool_execution_error` 给模型自我纠正。
   - **为什么精准无误伤**：`litellm.RateLimitError` 等**只**由「工具内部再调模型」的路径抛出（典型 `task_tool`
     子代理）；`web_fetch` 撞站点 429 是把 `status_code` 放进结果 dict、**并不抛** `litellm.*` 异常。所以分类
     刚好只覆盖模型调用类瞬时错误，不动普通工具。
   - **为什么重抛而非工具层退避**：子代理自己的模型调用已带 `max_retries=5` 退避；能逃出来说明退避已耗尽，
     再原地重试无益，干净失败 + 保持 state 干净才是对的（对齐上面「agent 节点里的 429 是安全失败」）。

### 56.3 为什么这次不动 ④（子进程取消）
真问题（停止后 `docker exec` 里的 `pip/npm` 跑到 120s 超时、共享容器还可能干扰别的会话），但修法是把
`subprocess.run` 换成 `asyncio.create_subprocess_exec` + 记容器/pid + `finally: docker kill`——**改的是
demo 关键路径 `sandbox_exec` 的执行模型，测试面大、回归风险高**。以「作品要看得过去、别 churn 关键路径」为准，
本轮**记为已知限制 + 修法草案**，不实现。验证脚本备忘：写个 `sleep 30` 的 sandbox 调用→按停止→看容器里进程
是否还在（顺带验证 ① 没被吞：若后端日志显示整轮跑完，就是取消被吞）。

### 56.4 验证
- 新增 4 条单测（`tests/test_harness_tools.py`）：retryable(`RateLimitError`/`Timeout`)→重抛、`CancelledError`→
  重抛、永久错误(`ValueError`/`FileNotFoundError`)→仍转 `tool_execution_error`；同步/异步两路都覆盖。
- 离线全量 **443 passed**、`ruff` 干净；既有 GraphBubbleUp(HITL) 集成测试不回归。

## 57. 迭代 48 续（2026-07-26）：grok 思考流的**非流式(合并)孪生**（承接 §54.9）

### 57.0 起因
跑 `evals --live --model grok-4.5` 第一条 `no-tool-chat` 就 FAIL——`answer_contains` 报**终答为空**。
§54.9 修的是**流式**方向（`sanitize_reasoning_message` 清洗落库对象、#223 合并逐 token 思考块）；这条是它的
**非流式(`ainvoke`)孪生**：一直没被单独验证过。

### 57.1 真机探针链（每步都花钱，故按最小增量推进）
1. 裸 `model.invoke([Human])` + sanitize → **3/3 有正文**（低思考量时 sanitize 能从合并后的 list 里把答案
   字符串捞回，所以"以为早修好了"）。
2. `graph.ainvoke`（真图，带 system + 工具）→ **1/3**；`[System, Human]` 无工具无 suffix → **0/3**；
   `[Human]` 无 system → 3/3。**→ 差异变量 = system 提示**（不是我这轮加的 suffix，也不是工具；suffix 那版
   反而略好，**排除是本轮上下文工程改动的回归**）。
3. `[System, Human]` **流式** `m.stream` 分通道计量 → **content 39/39/36(3/3 非空)** vs reasoning 250~486。
   **→ 决定性:带 system 时 grok 思考量翻几倍,思考块(内容块)与答案(裸字符串)在 content 里大量交错,
   langchain 的 chunk 合并把答案压没(`ainvoke` 0/3);而流式逐 token 投影从不合并,正文照常出。**
   所以**产品(SSE 流式)一直是好的**,坏的只有 `ainvoke`/合并/落库对象这一路。

### 57.2 根因与修复
**根因**：`_convert_delta_to_message_chunk_preserving_thinking_blocks` 此前只把 `thinking_blocks` **补进**
`additional_kwargs`，却把思考块**留在 `chunk.content` 里**。低思考量时合并后是 `[…thinking, "答案"]`，sanitize
还能捞回；高思考量时合并直接把答案压成空/混合 list，sanitize 已无正文可捞。

**修复(litellm_provider.py，同一函数加 3 行)**：转换阶段就用既有 `_without_reasoning_metadata_blocks` 把思考块
从 `content` 剔除——`content` 只留纯答案 → 合并变成干净字符串拼接、`ainvoke`/落库不再丢正文；思考已完整在
`additional_kwargs.reasoning_content` / `thinking_blocks`，**前端思考框走 v3 `message.reasoning` 通道（源自
`reasoning_content`，非 content 块）**，故不受影响。是 §54.9 那套「入站清洗」向**流式 chunk 转换**这一层的延伸。

### 57.3 验证(真机 grok-4.5，用真实 SSE 适配器 `LangGraphEventAgentAdapter` 逐通道核对)
- **流式(产品)**：content 54/45（答案照出）、reasoning 126/126（**思考框不回归**）。
- **非流式(`ainvoke`/评测)**：终答 len 36/48（**从 0 → 非空,合并 bug 修好**）。
- 2 条确定性单测（`tests/test_provider_reasoning_contract.py`）钉死：thinking 块从 content 剔除+答案保留、
  纯思考 chunk 塌成空串;`reasoning` 契约 22/22 不破;离线全量 **449 passed**。
- **真机 live 评测(10 条,grok-4.5,`--judge --langsmith`)**:`no-tool-chat` 从修复前空答案 → **PASS**(本修复的
  端到端验证);`memory-after-compaction` **PASS**(强制压缩后仍答对暗号「42 号蓝盒子」= Issue-2 有效);完整
  scorecard(6/10、含逐条归因与识别出的评测改进项)见 `backend/evals/README.md`。
- **边界（诚实标注）**：若某模型在特定 system 下**根本把答案留在 reasoning、content 交空**，这是模型输出层的事,
  清洗无法凭空造正文——属选模型/调 thinking 的范畴，与本修复无关。



## 58. 迭代 49（2026-08-09）：工作区改为「一个对话一个目录」

### 58.0 起因
旧布局是三个**横切**目录:`artifacts/<thread>/`、`uploads/<file_id|run_id>/`、`.sandbox/<thread>/`。
容器里 `docker exec` 的 cwd 是 `/workspace/work/<thread>`,模型在里面 `ls` 只看得到自己的 scratch——
要读上传得知道 `/workspace/uploads/<run_id>/`(这个 run_id 只出现在 system prompt 的上传注入块里),
要写产物得读 `SLOTFLOW_THREAD_ARTIFACTS` 环境变量。**三条路径三种发现方式**,全靠提示词把路径讲清楚。

### 58.1 新布局
```
<workspace>/
├── <thread>/                 一个对话一个目录,docker exec 的 cwd 就在这
│   ├── work/                 沙箱 scratch
│   ├── artifacts/            用户可见产物
│   └── uploads/<run_id>/     本次 run 的上传副本
├── .uploads/<file_id>/       上传原件 + metadata.json(容器内只读)
├── .slotflow_offload/  .playwright-mcp/
└── artifacts/  uploads/      旧布局遗留,只读兼容(见 58.5)
```
模型在对话目录里 `ls` 一次就同时看到 `work / artifacts / uploads`,不再依赖提示词描述路径。

**单一事实源 `sandbox/layout.py`**:容器路径、宿主相对路径、路由可见性校验三方必须对齐。
以前分散在 `docker.py` / `tools/workspace.py` / `workspace/routes.py` 各写一份,
`artifacts/` 用**原始** thread_id 而 `.sandbox/` 用**规范化**后的 key,已经是口径不一致。

### 58.2 挂载:为什么只能挂根,以及只读怎么保住
容器是全局共享的(空闲只 stop 不 rm,为的是 pip 依赖跨对话保留),挂载在 `docker run` 时固定,
而对话目录是**动态出现**的 → 只能把工作区整根读写挂进 `/workspace`。

代价是旧布局 `/workspace/uploads` 的 `readonly=true` 保不住。解法是**把原件挪出对话目录**:
`.uploads/` 存原件并叠一层**嵌套只读挂载**,对话目录里的只是本次 run 的副本——模型改坏了也不动
用户的原始文件。实测确认(alpine 容器)嵌套 ro 生效:整根可写、`.uploads` 写入被拒。

**skills 改挂 `/skills`**:实测发现挂在 `/workspace/skills` 会在**宿主**工作区根留下一个空的挂载点
目录,破坏"根下只有对话目录"。

**容器名混入 `LAYOUT_VERSION`**:挂载结构固定在 `docker run`,换了布局却复用旧容器,跑的还是旧挂载,
现象是"代码改了但容器里看到的还是老目录"。版本进哈希 → 换布局自然换新容器。

### 58.3 顺带修掉的真 bug:产物发现跨对话串台
`artifact_baseline()` 以前扫的是**所有对话共用**的 `artifacts/`,`finalize` 拿它做差集算「本轮新增产物」。
两个对话并发跑时,B 新写的文件会被算进 A 的 `new_entries`,前端就弹出一个跟当前提问无关的产物。
按对话分目录后自然只扫本对话,既修串台,又把每轮两次全库扫降成单对话扫。回归测试
`test_artifact_discovery_ignores_other_conversations` 钉住。

### 58.4 上传为什么分两处落盘
`POST /api/uploads` 时**根本不知道 thread**——前端 `uploadFile(file)` 只传文件,用户完全可能在新建
对话之前就把文件拖进来。所以原件按 `file_id` 存 `.uploads/`;等 run 真正开始(那时才知道属于哪个对话)
再 `stage_upload_for_run(file_id, run_id, thread_id)` 复制进 `<thread>/uploads/<run_id>/`。

### 58.5 兼容与迁移(刻意不搬的两类)
`viewable_kind()` 同时认新旧两种路径,旧文件不迁移也能读。`scripts/migrate_workspace_layout.py`
(默认 dry-run,`--apply` 才动)搬 `artifacts/<t>`→`<t>/artifacts`、`.sandbox/<t>`→`<t>/work`、
`uploads/file_*`→`.uploads/`(并改写 metadata.json 的 `workspace_path`)。

**刻意不搬**:
- `uploads/<run_id>/`:run→对话的对应**只存在聊天库的消息元数据里**,搬动就必须同步改写数据库。
  后端保留旧路径读取分支,留在原地照样预览。
- `artifacts/` 下的散落文件:本来就不属于任何对话,前端「未归类产物」分组兜着。

### 58.6 安全边界的变化(必须知道)
可见性校验从「路径必须以 `artifacts/` 开头」变成「**第二段**必须是 `artifacts`|`uploads`」——**规则变松了**,
是这轮最容易出洞的地方。因此:点开头的目录(`.uploads`/`.slotflow_offload`/`.playwright-mcp`)一律拒绝,
`work/` scratch 不可见,`..` 直接拒,且 `resolve_path` 的越界防护仍是第二道闸。
`test_workspace_layout.py` 把这些边界逐条钉住。

## 59. 迭代 50(2026-08-14):工具集恒定 —— 删掉渐进式披露,Skills 改两段式,MCP 收敛成两个工具

### 59.0 起因:一句话——**动态改工具 schema 会打掉提示词缓存**

上一版(§26 引入、后来退成"部分 gate")的思路是:把重工具空间藏在 `*_tools` 加载器后面,模型要用时
先调加载器把工具"提升"进 `promoted_tool_names`,下一步才能真正调用。省 schema token。

问题出在**省错了地方**。provider 的可缓存前缀是 `tools → system → messages`,三段里 `tools` 排在最前:

- 模型一旦调 `extensions_tools(["skill_match"])`,下一步 `bind_tools` 的工具数组就变了 →
  **整段前缀缓存从第一个 token 起全部作废**,不只是变化的那部分;
- 而为了让模型知道能激活什么,加载器又把整个空间的工具目录(名字 + 前 120 字描述)内联进了自己的
  `description` —— 这段 description 本身就在 `tools` 里。MCP 接多了之后,"省下的 schema"以
  description 的形式原样回到前缀,净收益进一步变负;
- 代价还不止一次:一次会话里模型可能激活好几轮,每轮赔一次全量缓存。

结论写成一句话:**模型侧的工具数量必须收敛,MCP / Skills / 角色的数量才可以发散。** 收敛的手段不是
"动态加载",而是"换一种承载":重工具交给子代理,发散内容交给"代理函数 + 本地检索"。

### 59.1 三处改动

**① 工具集恒定(删掉渐进式披露)。** `_GraphInputs` 现在在构造时 `bind_tools` 一次,`agent` 节点每步复用同一个
bound model;`make_tools_node` 不再有 `active()` 失败关闭。整套 `assemble_tool_spaces` / `_build_space_loader` /
`promoted_tool_names` / `tool_not_activated` / `SLOTFLOW_TOOL_SPACES_GATED` 删干净,`tool_spaces.py` 只留分类
函数(子代理切工具面还要用,原先 `subagents/tools.py` 里那份重复实现也合并过来了)。主 agent 工具面因此是
**31 个、全程一个字节不变**。

**② Skills 两段式:目录进前缀,正文进工具结果。** 之前 system prompt 里只有 `name: description`,正文唯一的
读取路径是 `sandbox_exec` 去 `cat /skills/<name>/SKILL.md` —— 也就是说 **Docker 不可用时 Skills 正文完全读不到**,
而 SlotFlow 本来就有一整套 Docker 降级路径。把 Skill 绑死在容器上是设计错误。现在:

- `skill_read(name)`(`skills/reader.py`,宿主侧纯文件读)把 SKILL.md 正文作为**工具结果**返回;
  `path=` 读附属文件(带目录穿越防护),`offset=` 续读被 `MAX_SKILL_READ_CHARS` 截断的长正文;
- 正文**刻意不做**超长卸载。`steps/tool_output_offload.py` 只处理 `ToolMessage`,而 `skill_read` 返回
  `Command`,天然绕过——把操作步骤挪去文件再让模型回读是本末倒置,模型需要的就是这段文本在上下文里;
- 每次成功读取写进 `used_skills` 通道(reducer 见 ③)。

**③ MCP 收敛成 `mcp_docs` + `mcp_call`。** `mcp/proxy.py`:手册由已连接 server 的工具定义**自动生成**
(名字/描述/参数名/类型/必填),`mcp_docs` 在本地做关键词检索(不走大模型、不发网络),`mcp_call` 在宿主侧直调
真实工具。接 1 个还是 100 个 server,模型看到的都是这两个。

### 59.2 为什么 MCP 不放进沙箱(评估过,否掉了)

"在容器里跑 MCP 客户端、让模型写代码调用"是另一种常见解法,在 SlotFlow 落不了地:

- 默认 MCP 是 playwright(stdio + pnpm + Chromium),`python:3.12` 容器里根本起不来;
- MCP 凭证要注入容器;
- MCP 从此依赖 Docker——**这正是 ② 刚修掉的错误**,不能一边修一边再犯一次。

宿主侧我们已经持有 `MultiServerMcpToolProvider` 的活 session(§45 的 stateful 会话),直接 `ainvoke` 即可。

一个必须补的细节:`langchain_mcp_adapters` 转换出的工具**不记来源 server**(只写 MCP 自己的
annotations/`_meta`,见其 `convert_mcp_tool_to_langchain_tool`)。而我们是逐 server 加载的,所以在
`loader._tag_server` 里补一个 `slotflow_mcp_server` 标签——否则两个 server 出现同名工具时无法区分。

### 59.3 垂类子代理:只开 browser,判据是什么

用户最初的想法是"把重工具都改成垂类子代理,比如上网搜索"。**上网搜索被否掉了**,判据写死成三条乘积:
`schema 数 × 交互轮数 × 中间产物脏度`。

| | schema 数 | 典型轮数 | 中间产物 | 结论 |
|---|---|---|---|---|
| `web_search` / `web_fetch` | 2 | 1 | 结果要被父 agent 直接引用带链接 | **直绑** |
| playwright `browser_*` | ~21 | 10+ | 每轮回大段 DOM/快照 | **垂类子代理** |
| MCP(用户自加) | 不定,可能上百 | 1-3 | 结构化数据 | **通用代理函数** |

给 web 搜索开子代理 = 多跑一遍 system prompt、结果被二次转述、来源链接大概率丢失,纯亏。子代理的价值是
隔离"脏且长"的上下文,不是隔离"小而常用"的 schema。

**降级路径**:子代理未启用时(flash 模式)`browser_*` 回落进 MCP 代理,而不是整块消失——否则 flash 用户
会突然打不开浏览器,这是静默的能力回退。

### 59.4 Subagent:3 个工具 → 1 个,2 次往返 → 0 次

角色库 235 份 md 其实**从来没有撑爆上下文**(父 agent 只看到 8 个领域摘要),真正的问题是**选择成本**:
模型要依次决定 `agent_name`(6 选 1)→ `domain`(8 选 1)→ 可能还要 `subagent_role_search` → `role_name`,
三层选择两次工具往返,很容易在第二跳就放弃。所以:

- `subagent_list` 删掉 —— 它的返回值在一次运行里**恒定**,那就该是可缓存的 system 文本
  (`build_subagent_catalog_prompt` → `<slotflow-subagents>`),而不是拿一次往返去换一段本来白送的文本;
- `subagent_role_search` 删掉 —— 检索下沉成 `task_tool(role_query=...)`,宿主在本地角色库里匹配。

顺带修掉一个**假阳性**:原来的 `_score_role` 用**子串**匹配,`"not"` 会命中 `annotation`、`"can"` 会命中
`candidate`,在 235 份角色里几乎任何查询都能捞到东西。而 `role_query` 的命中结果会被整段(最长 12000 字符)
注入子代理 system prompt,假阳性代价极高。改成:**整词**匹配 + 停用词 + 身份字段(id/name/division)权重 3、
描述权重 1,且 `role_query` 这条路径要求至少一次身份命中(`_ROLE_QUERY_MIN_SCORE=3`)。**查不到就不注入**——
让子代理用功能画像跑,好过被一段错的领域指令带偏。

### 59.5 顺手修掉的真 bug:skills preflight 一直在污染 system 前缀

`state.py` 的注释早就写清楚了"易变内容必须走尾部 `model_input_suffix`,保住 `tools→system→messages` 前缀
逐字节稳定",长期记忆和 todo 也确实搬走了——**唯独 skills preflight 还留在 `system_prompt` 通道里**
(`pre_model` 里 `system_sections = [base, format_preflight(...)]`)。而 preflight 每个新用户轮都会重算,
其 JSON 里就带着用户原话:于是**每开一个新话题就自己打掉一次前缀缓存**。这轮一并搬到尾部。

`test_system_prefix_stays_byte_identical_across_turns` 钉住两件事:两轮之间 system 文本完全相同,
且两轮的 preflight 确实各不相同地出现在尾部(反过来证明它留在 system 里必然会改写前缀)。

### 59.6 压缩台账:压缩之后不能"忘了自己在用 Skill"

Skill 正文是几 KB 的工具结果,压缩时会被整段折叠。折叠之后模型只知道"聊过很多",不知道自己已经在按某个
Skill 的流程做事——这正是一次运行半途悄悄放弃 Skill 的方式。所以做了**双保险**:

1. 摘要 prompt 里注入台账(`SKILLS_LEDGER_PROMPT_BLOCK`),让摘要模型把它写进摘要正文;
2. 压缩视图末尾再追加一条**确定性**的 `<slotflow-skills-ledger>`(`SKILLS_LEDGER_MESSAGE`),不依赖模型听话,
   明确写清:要么 `skill_read(name)` 重读,要么 `context_archive_search/read` 回溯原始工具结果,**不要凭记忆
   复述 Skill 的流程**。

台账消息只进 `llm_input_messages` / `context_epoch`,不进 `messages` 通道——既不会流式泄漏给用户,也不会被
checkpointer 回放(与 §29 / 2026-07-15 泄漏修复同一条边界)。

`used_skills` 通道复用了原先 `promoted_tool_names` 的有序并集 reducer(改名 `merge_ordered_unique`):模型完全
可以在一步里并行 `skill_read` 多个 Skill,每个返回一个 `Command(update=...)`,没有 reducer 就会撞
`INVALID_CONCURRENT_GRAPH_UPDATE`。

### 59.7 验证

- `uv run pytest -q -k "not live"`:475 通过。
- 新增/改写的行为钉子:
  - `test_context_runtime.py::test_every_bound_tool_is_callable_without_any_activation_step`(没有激活步)、
    `::test_used_skills_reducer_is_ordered_union`、`::test_concurrent_skill_reads_do_not_raise_invalid_update`;
  - `test_harness_skills.py`:正文不进 system 前缀、frontmatter 剥离、附属文件清单、目录穿越拒绝、
    截断续读、台账只记成功读取;
  - `test_harness_mcp.py`:原生 schema 不绑而 `mcp_docs`/`mcp_call` 绑、手册→调用闭环、瞎编工具名被拒、
    被过滤的宿主执行工具不能从手册后门暴露、browser 在无子代理时回落进代理、loader 打 server 标签;
  - `test_harness_subagents.py`:工具面只剩 `task_tool`、目录是静态文本、`role_query` 直接解析、
    查不到不注入、`browser_*` 只出现在子代理环境工具里;
  - `test_harness_graph_integration.py`:压缩台账进 epoch、system 前缀跨轮逐字节相同。
- **尚未做真机验证**:以上都是单测/集成测。缓存命中率的真实收益(前缀稳定 → provider cache hit)需要在真机
  多轮会话里用 `run.usage` 的 cache 字段核对,这一步还没跑。

## 60. 迭代 51(2026-08-14 续):砍掉两个"替模型做决定"的前置步骤 + 澄清丢思考的真 bug

### 60.0 删 skills preflight

§59 把它从 system 前缀搬到了尾部,这一轮直接删掉整个 `steps/skills_preflight.py`。理由是它已经没有
存在价值了:

- §59 之后 system 前缀里本来就有 Skills 目录(`name: description`)+ 一句"看着相关就 `skill_read`",
  preflight 那段"STRONG hint: PREFER a Skill" 是在重复同一件事;
- 它的实际内容不是目录,是 `find_relevant_skills()` 的返回 JSON,**第一个字段就是用户这一轮的原话**
  (实测 1283 字符)。哪怕搬到尾部,也是每步重发、永不复用的净开销;
- 它跑在 `prepare` 节点,**卡在第一个 token 之前**要扫一遍磁盘上所有 SKILL.md(有缓存,但冷启动仍是
  首字延迟)。

删掉之后 Skills 链路是纯粹的两段式:目录常驻前缀 → 模型自己判断 → `skill_read` 取正文。

### 60.1 删强制澄清门(`triage_gate`)

同一类问题:它在**每个新用户轮**额外跑一次模型,去判断"这个请求够不够清楚"。代价是每轮的首字延迟,
收益是偶尔拦住一个含糊请求——而模型自己判断该不该问(`<slotflow-operating-procedure>` 里已经写了
"blocking 的模糊点先调 `ask_clarification`")已经够用,强制门更多是在打断本来就明确的请求。

删除范围:`steps/clarify_gate.py`、`triage_gate` 节点与两条边、`clarify_gate_enabled` 开关、
`SLOTFLOW_CLARIFY_GATE` 环境变量、`tests/test_clarify_gate.py`。**HITL 本身没删**:自愿路径
(`ask_clarification` 工具 → `interrupt()` → `Command(resume=...)`)完整保留,它才是真正在用的那条。

拓扑因此少一个节点:`START → prepare → pre_model → 压缩 → agent → post_model → …`。

### 60.2 ⭐ 真 bug:澄清消息把本轮思考内容整个丢了

**现象**(用户真机报告):模型思考了一大段之后决定问澄清问题,思考框里的内容"找不回了",看起来像
resume 之后模型从头重新思考。

**先澄清一个误解**:模型**没有**重新思考。`interrupt()` 是在 `ask_clarification` 工具里调的,图暂停在
`tools` 节点;`agent` 节点早已执行完、它产出的 AIMessage(含 `reasoning_content` 和 tool_calls)已经写进
`messages` 并进了 checkpoint。resume 只重放 `tools` 节点,不会重跑 `agent`。**图状态里一直是全的。**

**真正的根因在另一个存储**:聊天库(前端读的那个 SQLite)和 graph checkpoint 是两套。
`chat/routes.py` 在 `clarification.requested` 上落库时只写了:

```python
metadata={"source": "clarification", "clarification": dict(event.data)}
```

**没有 `reasoning_content`**。而紧接着 `clarification_saved = True` 会让 `run.finished` 的正常保存被跳过
(`if content and not clarification_saved`)——**所以这一次写入是这条 assistant 消息唯一的落库点**。

于是表现极具迷惑性:直播时前端内存里的 `ChatUiMessage` 还带着 `reasoningContent`,思考框正常显示;
一旦刷新页面或切走再切回,消息按 DB 记录重建(`messageRecordToUiMessage` → `parseReasoningContent(metadata)`),
思考框就整块消失。看起来像"模型重新想了一遍",实际是**这段文本从来没被存下来**。

**修复**:澄清落库改用与正常路径同一个 `assistant_message_metadata(...)`,把已累积的
`select_assistant_reasoning_content(snapshot / streamed)` 一起写进去,再补上 `source`/`clarification` 两个字段。
钉子:`test_stream_run_persists_clarification_request` 现在会先吐一段 reasoning delta,再断言它进了 metadata。

### 60.3 前端:流式期间滚不上去(粘底逻辑被自己的时间窗吞掉)

**现象**:流式输出时想往上滚看历史,页面被立刻拽回底部。

**根因**在 `message-list.tsx` 的 `handleScroll`:

```ts
if (performance.now() <= programmaticScrollUntilRef.current) return;   // ← 元凶
```

这个"程序滚动时间窗"本意是别把自己触发的滚动误判成用户意图。但流式期间**每来一批 token 就调一次
`scrollViewportToBottom`,而它每次都把窗口往后推 700ms** —— 于是整个流式过程窗口永远有效,用户往上滚
产生的那次 scroll 事件被直接 return 掉,`autoFollowLatestAssistantRef` 永远保持 true。

**修复**:删掉时间窗(以及现在无人读的 `programmaticScrollUntilRef`)。判据本来就不需要知道"谁触发的":

- **在底部** → 一定该跟随(不管这次滚动来自谁);
- **不在底部 + 有真实手势**(wheel/touchmove/pointerdown/keydown)→ 用户明确要看历史,立刻停住。

程序滚动不产生手势,所以不会误伤——这比时间窗既简单又准确。

### 60.4 前端:思考框也要粘底

思考框是消息气泡内部的 `max-h-[30rem] overflow-y-auto` 小盒子,之前**完全没有自动滚动**:思考流式增长时
它停在顶部,用户得手动往下拖才看得到最新一步。抽了 `hooks/use-stick-to-bottom.ts` 复用同一套语义
(在底就跟、往上滚就停、滚回底再恢复),挂到思考框的滚动容器上。它和主列表**不共享滚动容器**,所以必须
各挂一份,但判据是同一套。

### 60.4b 前端第二处:澄清框把思考框吃掉了(和 60.2 是**两个独立 bug**)

修完 60.2 的持久化之后,界面上思考框**还是**不出现。原因在 `message-list-parts.tsx`:

```ts
const assistantContent = isUser || clarification ? { thought: "", body: content } : split(...)
const shouldShowThinkingCard = !isUser && !clarification && (...)
```

**只要这条消息带 clarification,就强制清空 thought、不渲染思考卡片。** 即使数据齐全也永远显示不出来。

两个 bug 症状完全一样("思考框没了"),所以只修一个看不出任何变化——这也是最初判断成"模型重新思考了"
的原因之一。现在澄清消息按模型实际发生的顺序渲染:**思考框 → 工具时间线 → 澄清卡片**,一轮澄清对话
在页面上是「思考框 → 澄清框 → 思考框」的连续记录。

顺带记一条**排查判据**:模型侧完全没有报错,本身就是线索。喂给模型的历史走 checkpointer
(新一轮只送一条 user message,历史由 checkpointer 提供),那条路上 reasoning 一直是全的;真丢在模型侧
DeepSeek 会直接回 `reasoning_content ... must be passed back`。所以"无报错 + 界面缺失" ⇒ 问题只可能在
**聊天库/渲染**这一侧,不在模型链路上。

### 60.5 验证

- 后端 `uv run pytest -q -k "not live"`:461 通过;`ruff` 全绿。
- 前端 `pnpm typecheck` + `pnpm build` 通过。
- **未做真机验证**:滚动行为和澄清刷新后的思考框需要真机点一遍才算数,这一步还没跑。

## 61. 迭代 52(2026-08-14 续):"上下文占用显示偏少" —— 仪表没骗人,历史真的丢了

### 61.0 报告与第一反应

用户:"聊了很久、模型干了很多活,上下文占用才显示 7k,是不是不准?"

7k 这个数量级本身就是线索:它差不多正好等于 **system prompt + 31 个工具 schema + 一两条消息**。
也就是说模型看到的可能真的就是一个近乎空白的会话。所以先别改显示,去读真实数据。

### 61.1 证据:`run_metrics` 里的 message_count 时间线

`run_metrics` 表一直在写每次模型调用的 `input_tokens` / `message_count`。同一个 thread 按时间排:

| 时间 | message_count | context_tokens |
|---|---|---|
| 04:07 | 5 → 22 | 4481 → 11323 |
| 04:22 | 24 | 13132 |
| 04:24 | 26 → 30 | 17164 → 24780 |
| 04:27 | 32 | 25180 |
| 04:29 | 34 → 38 | 25192 → 35856 |
| **04:55** | **3** | **4349 → 6972** |

历史在稳步增长,然后**一次性跌回 3 条**。这不是计量口径问题,是会话历史真的没了。

### 61.2 根因:模型侧历史**只**来自 checkpointer,而默认 checkpointer 是内存的

两件事叠在一起:

1. `build_agent_input` 每轮**只送一条新的 user message**,历史完全由 checkpointer 提供
   (`agent_adapter/streaming.py` 用 `bundle.config` 的 `thread_id` 恢复)。
2. `SLOTFLOW_CHECKPOINTER_BACKEND` 默认是 `memory`(`InMemorySaver`,进程内),而 `make dev` 跑的是
   `uvicorn --reload --reload-dir app`。

于是:**每改一次后端代码触发热重载,所有会话的模型侧历史就被清空一次。** 本机 `checkpoints.sqlite3`
的 mtime 停在 7 月 16 日就是旁证——它根本没被写过。

更隐蔽的是**前端完全看不出来**:消息列表读的是聊天库 SQLite(另一套存储,§60.2 那张表),
所以界面上依然是一长串对话,只有 token 仪表会露馅。用户看到的"数字偏少"其实是**唯一**暴露这个
严重问题的信号。

### 61.3 修复

- **默认 checkpointer 改成落盘 sqlite**(`config.py` 默认值 + `.env` + `.env_example`)。
  异步工厂 `create_async_checkpointer` → `create_sqlite_checkpointer` 早就实现好了,只是默认没用它。
- 回归钉子 `test_conversation_history_survives_a_backend_restart_with_sqlite_checkpointer`:
  用**两个独立的 saver 实例**指向同一个文件模拟重启,断言第二个进程仍能看到第一轮消息。

### 61.4 顺带修掉的计量归因错误(用户自己也怀疑到了)

`context_tokens` 原本取"最近一次成功调用的 prompt tokens"。但一次 run 里的模型调用不止主 agent:
**压缩节点**会调一次模型做摘要,**`task_tool` 子代理**在工具内部整跑一张子图(它复用父 run 的
callbacks,所以每次调用也落进同一个 collector)。这两类调用的 prompt 与会话窗口占用毫无关系——
一轮里只要末尾跑了子代理或触发了压缩,仪表就会瞬间跌到一个不相干的小数字。

改成**只认主 agent 节点、且不在任何工具内部**的那次调用,两个判据都来自 callback 本身:

- `metadata["langgraph_node"] == "agent"` 排除压缩节点(它叫 `SlotFlowSummarizationMiddleware`);
- `on_tool_start`/`on_tool_end` 维护的嵌套深度排除子代理——**子图的节点也叫 `agent`,光看节点名分不出来**,
  但它必然是在某个工具调用内部开始的。

没有任何主 agent 调用时(非 graph 场景/单测桩)退回旧行为,而不是让仪表直接消失。

### 61.5 仪表本身也修了一处:刷新/切会话就空

`run_metrics` 一直在写,但**没有任何读口**——所以 composer 的 token 仪表只在"本次页面会话真的跑过一轮"
时才有数。新增只读端点 `GET /api/chat/threads/{id}/context-usage`(取最近一次量到过窗口的 run),
前端在打开会话时补拉一次,失败不影响会话打开。

### 61.6 验证

- 后端 464 通过;`ruff` 全绿;前端 `pnpm typecheck` + `pnpm build` 通过。
- **未做真机验证**:切到 sqlite 后端之后的多轮真机会话需要点一遍确认历史确实连续。

## 62. 迭代 53(2026-08-14 续):澄清丢思考的**第三、第四个** bug —— 答完就删气泡 + 快照扫过界

### 62.0 报告

> 当模型思考一堆调用澄清 之前思考的内容框就看不到了 应该要保留

§60.2(后端补落库 reasoning)和 §60.4b(前端强制清空 thought)都已经修完并合并,症状**依然存在**。
这已经是同一句现象("思考框没了")背后的第三个独立 bug 了——同症状多根因是这条链路的固有特征:
思考内容要经过 *落库 → 重建 → 渲染 → 存活* 四道关,任何一道断掉,用户看到的都是"思考框没了"。

### 62.1 第三个 bug:回答澄清 = 删掉整条气泡

`chat-app.tsx`:

```ts
async function handleSelectClarification(messageId, clarification, option) {
  removeMessage(messageId);                      // ← 元凶
  await submitMessage(option.label, { ... });
}
```

那条被删掉的消息,**正是承载"想了一大堆 → 决定问一句"的那条**:思考框 + 工具时间线 + 问题本身,
全在它身上。所以时序是:

1. 模型思考一大堆 → 思考框正常显示;
2. 模型调 `ask_clarification` → interrupt → 思考框 + 澄清框(§60.4b 修好的部分,**这一步是对的**);
3. **用户一点选项 → 整条气泡被 `removeMessage` 抹掉** → 思考框和澄清框一起消失;
4. 新一轮开始 → 只剩一个新的思考框。

这就是为什么前两个 bug 修完"看起来没变化":修好的那一帧(第 2 步)存在的时间,只有用户点选项之前那几秒。

**修复**:不删。留下之后页面上就是「思考框 → 澄清框 →(用户答案)→ 新思考框」的连续记录。
选项按钮不需要额外处理——`canAnswerClarification = clarification && isLatestAssistant && !isStreaming`,
新 assistant 消息一建出来,旧澄清框就自动禁用成历史。

顺带修掉一处**存储不一致**:这条消息后端本来就落库(§60.2),前端却把它从内存里删了。于是
"直播视图"和"刷新后按 DB 重建的视图"长期不一致——刷新一下澄清框又回来了。现在两边对齐。

`removeMessage` 自此无人使用,连同 hook 里的定义一起删掉;`onSelectClarification` 的 `messageId`
形参也随之从三处签名里去掉。

### 62.2 第四个 bug:快照反向扫描会扫进上一条气泡

这个是**修 62.1 时顺出来的**:留下旧气泡之后,同一段思考会在新旧两个气泡里各出现一次。

`state.snapshot` 带的是 `state["messages"]` 全量历史,不是本轮新增。而两侧的取值函数
(后端 `latest_assistant_message_field`、前端 `latestAssistant{Content,ReasoningContent}`)
都是**无边界地反向扫描**,取"最后一条 assistant 的 content / reasoning_content"。

于是在"本轮还没产出正文/思考"的那一小段时间里(values projection 在 `agent` 之前的每个节点后都会发),
扫描一路退到上一条气泡,把上一轮的内容当成本轮的。更糟的是两侧的合并策略都是**取较长者**
(`mergeReasoningContent` / `select_assistant_reasoning_content`),所以上一轮更长时,污染不会被后续
正常输出冲掉,而是**一直留着**,甚至跟着 `run.finished` 落库。

边界有两个,缺一不可:

- **用户消息** —— 普通一轮的起点;
- **`ask_clarification` 的工具结果** —— 澄清 resume 的起点。resume 走 `Command(resume=...)`,
  答案是写成**工具结果**的,state 里**不会新增 user message**,所以光看 role 拦不住。不拦的话,
  澄清前那一大段思考正好会被算成 resume 这一轮的 —— 也就是上面说的"同一段思考出现两次"。

钉子(三个,都先在旧实现上验证过会失败):
`test_snapshot_scan_stops_at_the_user_message`、
`test_snapshot_scan_stops_at_the_clarification_tool_result`、
`test_snapshot_scan_still_sees_this_turn_across_tool_steps`(反向保证:本轮内部的普通工具往返照常扫得到)。

### 62.3 验证

- 后端 467 通过、1 skipped;`ruff` 全绿。
- 前端 `pnpm test` / `typecheck` / `check:dead-code`(knip) / `build` 全绿。
- **未做真机验证**:「思考框 → 澄清框 → 新思考框」这条连续记录需要真机点一遍澄清才算数。

## 63. 迭代 54(2026-08-14 续):真机两个大问题 —— 「答完还接着干」和「传大文件对话直接死」

用户报了两条,都拿真机 thread 的 checkpoint + `run_metrics` 复原了完整时序,不是猜的。

### 63.1 「明明早就完活了,还要接着干」—— todo 强制门只会推翻已完成的回合

thread `7450b95f`(一句「这是什么」+ 一个 PPT),checkpoint 里 24 条消息:

```
[1]  AI   convert_file_to_markdown
[3]  AI   530字 无 tool_calls   ← 完整答案,结尾「需要我帮你…吗?」本该到此为止
[4]  AI   write_todos + web_search ×3     ← 被拽回来
[20] AI   1385字 无 tool_calls  ← 又一份完整答案
[21] AI   write_todos                      ← 又被拽回来
[23] AI   1602字                           ← 第三份答案
```

同一个问题答了三遍,9 次模型调用,思考文本 24617 字符。

根因是 `steps/todo.py` 的 `todo_enforcement_update`(post_model)+ `route_after_model` 的回边。
它的**结构性矛盾**:

```python
if last_ai.tool_calls:
    return None          # ← 只在「模型不再调工具」时才可能触发 = 已经写完最终答案
...
pending = "...Do not answer in prose before creating the todo list."
```

触发条件是「已经答完」,指令内容却是「别用散文回答,先建 todo 列表」——那个时刻早就过去了。
它唯一能做的就是把一个完成的回合重新拽开。`[3]` 命中「还没有 todos」分支,`[20]` 命中
「todos 没做完」分支,各拽一次。两个分支的缺陷是同一个,所以**整套删除**:
`todo_enforcement` 通道、`consume_todo_enforcement`、`route_after_model_has_enforcement`、
`_should_create_initial_todos` 这些启发式全部删掉。

保留的:`write_todos` 工具本身、系统提示里的主动规划引导、`todo_reminder_update`
(走 `model_input_suffix` 的非强制提醒)。规划回到"模型自己判断",图不再替它决定什么时候算答完。

### 63.2 「上传大文件,对话直接死」—— 一个没有上限的读口 + 静默死亡

thread `c13009042616`,checkpoint 只有 7 条:

```
[1] AI    workspace_read('…/index.html')
[2] Tool  373,215 字   ← 446KB 的 HTML 全量内联(≈166k token)
[3] AI    len=0        ← 空
[4] AI    len=0
[5] Human '继续啊'
[6] AI    len=0
```

`run_metrics`:`input_tokens=166730 → output_tokens=0`,`status: "success"`,**全程无报错**。

两层缺陷叠加:

**(a) `workspace_read` 是整个工具集里唯一没有任何上限的读口。** 它恰恰是系统提示点名让模型
读上传文件的工具;而工具结果卸载(`tool_output_offload`)又把它列在 `_OFFLOAD_SKIP_TOOLS` 里
——把工作区文件卸载成工作区文件确实是循环,跳过本身没错,但跳过之后就没有任何人管上限了。
对照 `skill_read` 早有 `MAX_SKILL_READ_CHARS = 24_000` + `offset` 分页。
修复:`MAX_WORKSPACE_READ_CHARS = 24_000` + `offset` 参数 + `read.next_offset` 续读提示,
和 `skill_read` 同一套语义。

**(b) 空响应静默终结整轮,还毒化 checkpoint。** 空 AIMessage 没有 tool_calls → 路由到
`finalize` → 这一轮"正常结束";`run.finished` 时 `content` 为空 →
`if content and not clarification_saved` 不成立 → **一条都不落库**,前端看起来就是发完消息
什么都没发生。更糟的是那两条空消息**进了 checkpoint**,于是 thread 被永久毒化:用户后来发
「继续啊」,模型读到的还是那 166k,继续吐空。

修复:`assert_model_response_not_empty` 在 agent 节点里抛 `EmptyModelResponseError`。
**抛异常而不是返回**是关键——LangGraph 会丢弃这个节点的写入,空消息不进 state、不进
checkpoint,thread 保持可用;同时 `run.error` 带着可读原因走到前端。

### 63.3 `reasoning_content` 默认改为保留

原来是白名单:只有 `provider == "deepseek"` 保留,其余全剥,理由是"OpenAI 系推理模型会自己
重新推理,回喂 CoT 纯浪费 token"。省 token 这点仍然成立,但白名单键的是
`run_context.model_provider`,而**经 OpenAI 兼容中转访问的 DeepSeek 上报的是 `"custom"`**
——唯一硬性要求回传这个字段的 provider,恰恰是被剥掉的那个。

现在默认保留:checkpoint 里有,`llm_input_messages`(从 checkpoint 投影)自然也有。
想恢复省 token 的旧行为,设 `SLOTFLOW_STRIP_REASONING_PROVIDERS="grok,openai,glm"`。

注意这条闸**只管 `additional_kwargs` 里的载体**;content 里的 reasoning/thinking 块仍然一律
剥掉——那是线路非法(`unknown variant 'reasoning'` → 400)且是体积大头,与本开关无关。

### 63.4 仪表加了前缀缓存命中率

`context_cached_tokens` 取**和 `context_tokens` 同一次主 agent 调用**的数字,不是整轮聚合
——两者必须同源,否则"整轮缓存总量 / 单次上下文占用"除出来是个没有意义的比例。

前端要能区分两种 `null`:**这家 provider 不上报缓存字段**(实测走中转的 `provider: "custom"`
就是这样,`cache_status` 全是 unknown)显示 `—`,**报了但没命中**才显示 `0%`。
混成一个 `0%` 会让人以为缓存策略失效,其实是没有数据。

### 63.5 验证

- 后端 467 通过、1 skipped;`ruff` 全绿。
- 前端 `pnpm test` / `typecheck` / `check:dead-code` / `build` 全绿。
- **未做真机验证**:这四项都需要真机再跑一遍——特别是 63.2(a) 之后传同一个 446KB 文件应该
  能正常回答,以及 63.3 之后 DeepSeek 多轮思考是否确实不再报缺字段。
