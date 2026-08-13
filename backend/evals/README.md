# SlotFlow 自建 Agent 评测集

一套复用真实调用链的 agent 评测:`build_slotflow_harness_graph(model=…) → ainvoke → result["messages"]`,
评测器直接读原生 message(工具调用 / 终答 / 是否回灌思考)。**只换 `model` 一个参数**即可在
"桩模型(确定性、免费)"与"真模型(真机)"之间切换。

## 三档运行

```bash
cd backend

# offline:对人工编造的桩 transcript 打分 —— 证明"评测器 + 打分 + 报表"本身正确(确定、免费)
.venv/bin/python -m evals.run_eval

# smoke:真图 + FakeModel 跑一条 —— 证明"图→抽取→打分"端到端接线通(确定、免费)
.venv/bin/python -m evals.run_eval --smoke

# live:真模型跑全部 10 条(读 backend/.env 里的 key;会真的调用模型)
.venv/bin/python -m evals.run_eval --live --model grok-4.5
.venv/bin/python -m evals.run_eval --live --model grok-4.5 --judge        # 额外开 LLM-as-judge
.venv/bin/python -m evals.run_eval --live --model grok-4.5 --langsmith    # 额外把 trace 推到 LangSmith
.venv/bin/python -m evals.run_eval --live --only read-file                # 只跑一条
```

`--provider` 默认 `custom`(中转模型必须显式指定,否则 `grok-4.5` 会被 litellm 误判成 xai 直连)。

## 10 条样本覆盖什么

| id | 标签 | 考点 |
|---|---|---|
| read-file | tool, issue-1 | 读文件是否可用(桩故意演示 `tool_not_activated`) |
| write-file | tool, issue-1 | 写文件走 loader→promote→work 两步链 |
| web-search | tool, issue-1, network | 联网检索 |
| artifact-code | tool | 生成 artifact |
| no-tool-chat | precision | **不该**调用工具时别乱调(精度) |
| clarify | gate | 信息不足应触发澄清 |
| todo-plan | planning | 复杂任务先写 todos |
| memory-basic | memory | 两轮内记住用户信息 |
| memory-after-compaction | memory, issue-2 | **跨压缩阈值后仍记得早期暗号**(临时把阈值调到 1200) |
| no-bloat-contract | contract, reasoning | 落库消息不得回灌 `reasoning_content` / thinking 块(桩故意演示膨胀) |

## 真机结果（grok-4.5，2026-07-27，`--live --judge --langsmith`）

**6/10 样本通过 · 13/18 评测器通过**(trace 已上报 LangSmith `project=SlotFlow`)。原始分不做粉饰——**逐条归因**才是这套评测的价值:

| # | 样本 | 结果 | 说明 |
|---|---|---|---|
| 3 | web-search | ✅ 2/2 | 联网检索,走 `network_tools→web_search` loader 链 |
| 4 | artifact-code | ✅ 2/2 | 生成代码 artifact |
| 5 | no-tool-chat | ✅ 3/3 | **修复验证**:此条修复前因思考流合并 bug 终答为空(见 `HARNESS_NOTES.md §57`),现终答正常 |
| 6 | clarify | ✅ 1/1 | 指令模糊时正确触发 `ask_clarification` |
| 8 | memory-basic | ✅ 1/1 | 两轮内记住用户信息 |
| 9 | memory-after-compaction | ✅ 1/1 | **修复验证**:强制压缩(阈值调到 1200)后仍答对暗号「42 号蓝盒子」= Issue-2「压缩不丢史」有效 |
| 1 | read-file | ⚠️ 2/3 | **环境缺口**:评测工作区未预置 README.md → `workspace_read` 报 file-not-found;agent 行为正确(确实调了 `workspace_read`) |
| 2 | write-file | ⚠️ 0/2 | grok 用 `artifact_write` 建文件(合理替代),评测器严格只认 `workspace_write`;叠加同一未预置工作区的读错误 |
| 7 | todo-plan | ⚠️ 0/1 | grok 选择先 `ask_clarification`/`skill_match`(合理),评测器严格只认 `write_todos` |
| 10 | no-bloat-contract | ⚠️ 1/2 | **被测契约本身通过**:`no_reasoning_bloat` PASS(落库无思考回灌);仅 `answer_contains` 因 grok 在「深入想清楚」提示下终答为空而扣分——属模型输出层边界(§57 已标注),非本项契约失效 |

**归因小结**:4 个"失败" = 1 环境缺口(read-file 未预置文件)+ 2 严格单工具期望 vs 合理替代工具(write-file / todo-plan)+ 1 模型输出边界(no-bloat 的被测契约实际通过)。**核心能力——联网 / artifact / 精度(不乱调工具)/ 澄清 / 多轮记忆 / 跨压缩记忆 / 防思考回灌——均验证通过。**

**由此识别的评测改进项**(诚实记录,未在本轮实现):① `read-file` 应在跑前把目标文件预置进工作区;② `write-file`/`todo-plan` 的 `expects_tools` 过严,应接受等价工具(如 `artifact_write` ⊇ 写文件意图)或收紧样本措辞;③ `no-bloat-contract` 应把「被测契约」与「答案非空」拆成两条独立判定,避免模型边界淹没契约结论。

## 评测器(`evaluators.py`)

**能用代码判的绝不用 LLM 判** —— 6 个里只有最后一个是 LLM-as-judge:

- `expects_tools` —— 期望工具 ⊆ 实际调用(轨迹评测)
- `forbids_tools` —— 不该调用任何工具(精度)
- `no_tool_errors` —— 无工具执行失败(**`tool_not_activated` 在这里被抓 → 直指 Issue-1**)
- `answer_contains` —— 终答含关键子串(all / any)
- `no_reasoning_bloat` —— 落库无思考回灌(**这条就是防上下文膨胀的契约,普通正确性评测抓不到**)
- `llm_judge` —— LLM-as-judge,仅 `--judge` 且 live 时运行,否则跳过

## 分层策略里它处在哪

- **Tier 1(确定性契约/单元/集成)**:`tests/` 里的 pytest(如 `test_provider_reasoning_contract.py`),占大头。
- **Tier 2(离线评测,本目录)**:数据集 + 评测器 + 打分表,改 prompt/工具空间时跨版本比分抓回归。
- **Tier 3(真机 / 在线)**:`--live` 真机冒烟;`tests/test_live_deepseek.py` 那种 env-gate 真机,默认不进 CI。

## 接 LangSmith 可视化(下一步)

`--langsmith` 会置 `LANGSMITH_TRACING=true` + `LANGSMITH_PROJECT=SlotFlow`,让每次 `ainvoke`
自动上报 trace(需 `.env` 里已有 `LANGSMITH_API_KEY`),即可在 LangSmith 里下钻每条样本的
node/tool span。若要把**分数**也进 LangSmith 的 Experiments 对比视图,把本目录的数据集与评测器
接到官方 `aevaluate(target, data=…, evaluators=[…])` 即可(target = 上面的 graph 包装)。

> ⚠️ offline 的 transcript 是人工编造的,只证明流水线正确;真实分数以 `--live` 为准。
> 1 号(直调未激活工具)与 10 号(回灌思考)是**故意造的红项**,演示评测器能抓到 Issue-1 与膨胀。
