# SlotFlow Harness 工程笔记（Harness Engineering Notes）

> 配合 `AGENTS.md` 一起阅读。`AGENTS.md` 是仓库的「地图」（架构 / 约定 / 不变量），
> 本文是「工程日志」：我们遇到的**行为问题**、**试过的方案**、**结果**、**现在怎么做**、
> **实测结论**。给新对话（人或 AI）快速建立对 harness 现状的准确认知。
>
> 最近一次更新：2026-06（feature/clarify-gate）。所有结论均经**真实 DeepSeek API 实测**，
> 非纸面推断。

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
