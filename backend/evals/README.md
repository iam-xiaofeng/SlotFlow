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
