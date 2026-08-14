# SlotFlow 自建 Agent 评测集

一套复用真实调用链的 agent 评测:`build_slotflow_harness_graph(model=…) → ainvoke → result["messages"]`,
评测器直接读原生 message(工具调用 / 终答 / 思考载体)。**只换 `model` 一个参数**即可在
"桩模型(确定性、免费)"与"真模型(真机)"之间切换。

## 四档运行

```bash
cd backend

# offline:对人工编造的桩 transcript 打分 —— 证明"评测器 + 打分 + 报表"本身正确(确定、免费)
uv run python -m evals.run_eval

# smoke:真图 + FakeModel 跑一条 —— 证明"图→抽取→打分"端到端接线通(确定、免费)
uv run python -m evals.run_eval --smoke

# live:真模型跑全部 20 条(读 backend/.env 里的 key;会真的调用模型)
uv run python -m evals.run_eval --live --model grok-4.5
uv run python -m evals.run_eval --live --model grok-4.5 --judge      # 额外开 LLM-as-judge
uv run python -m evals.run_eval --live --model grok-4.5 --langsmith  # 额外把 trace 推到 LangSmith
uv run python -m evals.run_eval --live --only read-file              # 只跑一条

# LangSmith 实验:注册数据集 + 跑成可对比的 Experiment(分数进 UI,不只是 trace)
uv run python -m evals.langsmith_eval --model grok-4.5 --judge
uv run python -m evals.langsmith_eval --dataset-only                 # 只同步数据集
```

`--provider` 默认 `custom`(中转模型必须显式指定,否则 `grok-4.5` 会被 litellm 误判成 xai 直连)。

### `--langsmith` 和 `langsmith_eval` 的区别

面试常被追问的一点,两者不是一回事:

- `--langsmith` 只**打开链路追踪**(`LANGSMITH_TRACING=true`)。你能看到每次模型/工具调用的
  trace,但没有数据集、没有实验、没有分数——换个模型再跑,两次结果只能靠人眼在 trace 列表里对。
- `evals/langsmith_eval.py` 做的是**评测**:把 20 条样本注册成 LangSmith Dataset,把本目录的
  评测器包装成 LangSmith evaluator,用 `langsmith.evaluate()` 跑成一次 Experiment。
  于是每个模型 / 每次改动都是一行可对比的实验记录,逐条分数、失败明细、trace 在 UI 里是连起来的。

包装时有个坑值得记:LangSmith 的 evaluator 是"对每个 example 都跑一遍"的,而我们的评测器是
**逐样本声明**的(读文件那条才评 `no_tool_errors`,纯聊天那条才评 `forbids_tools`)。
所以 `build_evaluators` 包了一个分发器:对当前 example 只跑它自己声明的那几个。

## 20 条样本覆盖什么

| # | id | 标签 | 考点 |
|---|---|---|---|
| 1 | read-file | tool | 读工作区文件(**跑前预置文件**,量的是行为不是环境) |
| 2 | read-file-paginated | tool, context | 大文件必须分页/检索,工具结果不得超上限 |
| 3 | grep-file | tool | 工作区检索 |
| 4 | artifact-code | tool | 产出 artifact(接受 `artifact_write` / `sandbox_exec`) |
| 5 | sandbox-verify | tool, sandbox | 写代码并在沙箱里真跑用例验证 |
| 6 | convert-doc | tool | 文档转 markdown |
| 7 | web-search | tool, network | 联网检索 |
| 8 | web-fetch | tool, network | 抓取指定 URL |
| 9 | no-tool-chat | precision | **不该**调工具时别乱调 |
| 10 | stop-when-done | precision, issue-verbose | **答完就收工**,不自我加戏(工具调用数上限) |
| 11 | no-search-for-common-sense | precision | 常识题不该联网 |
| 12 | refuse-unknowable | precision, honesty | 读不到的东西要说读不到,不编 |
| 13 | todo-plan | planning | 明确要求记待办时应调 `write_todos` |
| 14 | clarify | gate, hitl | 信息不足应澄清 |
| 15 | clarify-keeps-thinking | gate, hitl, contract | 澄清那条消息必须带着它的思考 |
| 16 | memory-basic | memory | 两轮内记住用户信息 |
| 17 | memory-after-compaction | memory, issue-2 | **跨压缩阈值后仍记得早期暗号**(临时把阈值调到 1200) |
| 18 | context-archive-recall | memory, context | 压缩后能用归档检索捞回细节 |
| 19 | skill-two-step | skills | Skills 两段式:先发现(目录)再读正文(工具结果) |
| 20 | reasoning-roundtrip | contract, reasoning | 思考载体保留 + 思考块不入 content |

## 真机结果(2026-08-14 · deepseek-v4-pro 走中转 · `--live --judge --langsmith --concurrency 4`)

**样本 20/21 通过 · 评测器 54/55 通过。** 唯一失败的一条逐条归因如下——**归因比分数重要**:

| # | 样本 | 结果 | 归因 |
|---|---|---|---|
| 13 | todo-plan | ⚠️ 1/2 | 模型把 13 个待办拆成 **13 个并行 `write_todos` 调用**,被 `todo_parallel_call_guard` 全部拒绝(它对每个并行调用各回一条 `status=error`,所以 `no_tool_errors` 收到 13 条)。**守卫做的是对的**——并行写 todo 会互相覆盖;模型随后自己纠正,`expects_tools` 那项通过。属于"模型误用一次 + 守卫拦住 + 自愈",不是产品缺陷。这里刻意不放宽评测器:它如实反映了一次真实的工具误用。 |

跑通之前踩到的三件事,都记在这里(它们比分数更能说明评测该怎么做):

**① 模型名过期 → 21 条全红。** `--model` 默认硬编码 `grok-4.5`,而中转早换成了 `deepseek-v4-pro`,
第一次跑 21 条全部 `NotFoundError`,报表看着像 agent 全线崩溃。现在默认跟随 `.env` 的 `CUSTOM_MODELS`。
和评测集里那些过期工具名是同一类腐烂:**硬编码的外部事实会漂,而它自己不会喊**。

**② 评测跑的不是产品那张图 → Skills 全废。** `_run_graph` 原来自己拼了个只有 `system_prompt` 的
`SlotFlowHarnessConfig`,于是 skills_root / MCP / 记忆全是空的:`skill_list` 返回空列表、
`skill_read` 报 `skills_root_not_configured`。看报表像"agent 不会用 Skill"。现在走**和后端完全
同一条**路径:同一个 `runtime_config`、同一个 `create_langgraph_agent_graph`、同一套 MCP 异步预加载。
**评测的价值取决于它跑的东西和线上有多像**,这种"跑了个简化版"的偏差最难发现。
(改完还崩过一次:MCP 是 stdio 子进程,每条样本各加载一次 = 21 轮 spawn/teardown,
跑到第二条就 `CancelledError`。改成整轮复用一份、结束统一关闭。)

**③ 中转限流不回 429,回的是空补全 → 又一次 21 条全红。** 裸调最小 prompt(无工具、无历史、
不经过 harness)实测 **3 次里 2 次 `content=''`**。所以 `assert_model_response_not_empty` 那道闸是
必要的(否则空消息进 checkpoint,整个 thread 被永久毒化),但**重试 1 次远远不够**:空响应率 2/3 时
单次重试仍有约 44% 失败。改成 3 次退避重试后,同一套代码、同一个中转,从 0/21 变成 20/21。

并发上限硬夹在 5(`MAX_LIVE_CONCURRENCY`),默认 4。**带 `env_overrides` 的样本始终串行**——
它们改的是进程级 `os.environ`(压缩阈值),并发会串味:一条把阈值调到 1200,同时在跑的其它样本
也跟着被压缩。

## 2026-08-14:上一版有 8/10 条是坏的

复查旧评测集时发现的问题,记在这里比修掉更有价值——**评测集会随架构漂移,而它自己不会报错,
只会安静地量错**:

| 样本 | 问题 | 性质 |
|---|---|---|
| `write-file` | 期望 `workspace_write` —— **registry 里从来没有这个工具** | 恒红,永不可能通过 |
| `read-file` | 评测工作区没预置 README.md | 量的是环境不是 agent |
| `read-file` / `no-tool-chat` / `no-bloat-contract` | `no_reasoning_bloat` 断言"落库不得有 `reasoning_content`" | **契约当天被翻转**,现在默认保留 |
| `todo-plan` | 期望 `write_todos`,依赖已删除的 post_model 强制门 | 依赖已删机制 |
| `web-search` / `artifact-code` 的离线桩 | 演示 `network_tools` / `workspace_tools` loader | 渐进式披露已整体删除 |
| `clarify` | 强制澄清门已删 | 改为量"自愿澄清"能力 |

只有 `memory-basic` 和 `memory-after-compaction` 两条是干净的。

**为此加了 `tests/test_evals_dataset.py`**,把三条不变量钉死,让这类腐烂不能再悄悄发生:

- 评测器期望的每个工具名都必须在运行期 registry 里(`write-file` 那个 bug 的钉子);
- 离线桩里出现的工具名同样必须真实存在(loader 那个 bug 的钉子);
- 每条样本都得有 turns / 评测器 / 离线桩,用了 `llm_judge` 就必须给 `reference`。

上一版 README 里我自己记过三条"评测改进项"(预置文件、期望过严、契约与答案非空混在一起),
这一版全部落实了:`workspace_files` 预置、`expects_any_tool` 接受等价工具、
`no_thinking_blocks` 与 `reasoning_preserved` 拆成两条独立契约。

## 评测器(`evaluators.py`)

**能用代码判的绝不用 LLM 判** —— 11 个里只有最后一个是 LLM-as-judge:

| 评测器 | 判什么 |
|---|---|
| `expects_tools` | 期望工具 ⊆ 实际调用(轨迹评测) |
| `expects_any_tool` | 候选工具至少调一个 —— 一个任务往往有多条合理路径,锁死某一条量到的是"跟我的猜测有多像" |
| `forbids_tools` | 不该调的没调(省略 `names` 表示一个都不该调) |
| `max_tool_calls` | 工具调用数不超上限 ——「答完就收工」的可量化代理 |
| `no_tool_errors` | 无工具执行失败 |
| `tool_result_capped` | 单条工具结果不超字符上限 —— 钉 `workspace_read` 那个洞 |
| `no_empty_assistant` | 没有"既无正文也无工具调用"的空消息 —— 钉静默死亡 |
| `answer_contains` | 终答含关键子串(all / any) |
| `no_thinking_blocks` | content 里没有 thinking/reasoning 块(线路非法 + 体积大头) |
| `reasoning_preserved` | `reasoning_content` 载体保留(2026-08-14 起的新契约) |
| `llm_judge` | 语义正确性,仅 `--judge` 时运行 |

裁判的分数解析用的是"先找 JSON、再退回正则",不是子串匹配——旧实现里 `{"score": 10}`
会被误判成 1,裁判改用 markdown 代码块包 JSON 也会漏。解析不出来记为**跳过**而不是失败。

## 分层策略里它处在哪

- **Tier 1(确定性契约/单元/集成)**:`tests/` 里的 pytest,占大头。
- **Tier 2(离线评测,本目录)**:数据集 + 评测器 + 打分表,改 prompt/工具集时跨版本比分抓回归。
- **Tier 3(在线实验)**:`langsmith_eval.py` 跑成 LangSmith Experiment,跨模型/跨改动可对比。
- **Tier 4(真机)**:`--live` 真机冒烟;`tests/test_live_deepseek.py` 那种 env-gate 真机,默认不进 CI。

> ⚠️ offline 的 transcript 是人工编造的,只证明流水线正确;真实分数以 `--live` / LangSmith 实验为准。
> `reasoning-roundtrip` 是**故意造的红项**(content 里塞了 thinking 块),演示评测器确实抓得到——
> 一套全绿的评测集是证明不了自己有效的。
