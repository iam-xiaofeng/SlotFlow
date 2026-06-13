# 模块 28：真实 Subagent 和模拟实现瘦身

本模块把 `task_tool` 从“返回结构化委派说明”升级为“启动真实 LangChain subagent”。

## 扫描结论

本轮扫描了生产代码中的 `fake`、`mock`、`simulated`、`static`、`placeholder` 等关键词。

结论：

- 生产路径里真正仍在返回模拟结果的是 `backend/app/harness/subagents/tools.py` 的旧 `SubagentTaskRunner`。
- 其它 fake 基本位于 `backend/tests`，用于稳定测试，不属于生产运行路径。
- docs 中仍有早期 static/fake 学习阶段说明，模块 21 已补充“模块 28 已替换执行语义”的说明。

## 新执行链路

```txt
主 agent
-> task_tool(agent_name, task, context)
-> SubagentTaskRunner.arun()
-> create_agent(model=当前模型, tools=环境工具, system_prompt=profile prompt)
-> subagent graph.ainvoke(...)
-> 返回 completed/error JSON 给主 agent
```

`build_subagent_tools()` 现在要求同时具备：

- `subagent_enabled=True`
- 真实 `model`
- 当前 `RunContext`
- 至少一个 enabled subagent profile

如果没有 model/run context，不再注册 `task_tool`，避免回落到空心模拟结果。

## 子 agent 能用哪些环境工具

子 agent 继承当前 harness registry 中的非递归工具：

- `slotflow_context`
- `workspace_list`
- `workspace_read`
- `workspace_tree`
- `workspace_search`
- `artifact_list`
- 已配置的 MCP tools

它不会拿到 `task_tool` 自己，因此不会出现子 agent 再递归委派子 agent 的隐式行为。

写入类 workspace 工具仍然只在 `SLOTFLOW_WORKSPACE_WRITES_ENABLED=true` 时出现。

## 没有引入 DeerFlow 旧调度器

本模块没有导入 DeerFlow 的 subagent scheduler、后台任务线程或全局任务表。

原因是 SlotFlow 当前阶段需要先拥有真实可运行的子 agent，而不是把旧项目的大型调度系统整体搬进来。后续如果需要并发、隔离、取消和结果持久化，可以在 `SubagentTaskRunner` 内部继续演进，不改变主 agent 调用 `task_tool` 的协议。

