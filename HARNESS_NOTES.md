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
                                                                                              └─ finalize → END
```
节点职责（对应 steps 模块）：
- `prepare`（每回合一次）：`runtime_summary` / `uploads` / `skills_preflight` / 记忆检索(`long_term_memory.retrieve_memories`) / 产物基线(`artifact_discovery.artifact_baseline`)。
- `triage_gate`（仅首步，pro/ultra）：`clarify_gate.run_triage` → 不可做则 `clarify_via_interrupt`（`interrupt`+答案 `HumanMessage`）。
- `pre_model`（每步）：`todo_reminder_update` / `repair_dangling_tool_calls` / 记忆 system 注入(`append_memory_system_message`)。
- `SlotFlowSummarizationMiddleware`（独立节点，名字固定）：复用官方 `SummarizationMiddleware.abefore_model` 的 `RemoveMessage`+`lc_source` 逻辑。
- `agent`：`model.bind_tools(tools)` 调用，读 `state.llm_input_messages` + `state.system_prompt`。
- `post_model`：`subagent_limit.cap_subagent_calls` 截断超额 `task_tool`。
- `route`：官方 `tools_condition` → `tools` / `finalize`。
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
重复的 async memory 注入用例；中间件单测迁移到 steps 测试后用例更聚焦）。live 验证（阶段 F）待跑。
