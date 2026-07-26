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
