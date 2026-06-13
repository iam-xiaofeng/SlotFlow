# 模块 21：Subagent task tools

模块 21 给 SlotFlow harness 加入第一版 subagent 工具：

```txt
task_tool
```

> 模块 28 已经把这里的第一版“结构化委派结果”升级为真实 subagent agent 调用。
> 本文保留模块 21 的工具边界说明，但执行语义以模块 28 为准。

它只在本次 run 的 feature 开关启用时注册：

```txt
subagent_enabled=True
```

当前映射来自模块 3：

```txt
flash -> subagent_enabled=False
pro   -> subagent_enabled=False
ultra -> subagent_enabled=True
```

## 这一层解决什么问题

模块 10-20 已经让 harness 能装配模型、tools、skills、MCP、middleware、sandbox、checkpointer。

模块 21 解决的是：

```txt
主 agent 如何表达“把一个子任务交给某个子 agent profile”
```

模块 21 先把工具协议和注册位置落稳：

```txt
agent 调用 task_tool
-> task_tool 校验 agent_name/task/context
-> 返回结构化 JSON
-> 主 agent 继续基于结果回答
```

模块 28 已经替换 `SubagentTaskRunner` 内部实现：`task_tool` 会启动一个真实
LangChain agent profile，而不是拼接模拟结果。

## 它在完整链路里的位置

```txt
前端输入
-> ChatStreamRequest.mode
-> build_run_config()
-> RunContext.subagent_enabled
-> features_from_run_context()
-> build_harness_tools(model=..., run_context=...)
-> build_subagent_tools(model=..., run_context=...)
-> task_tool
-> LangGraph create_agent(tools=...)
```

这和 MCP/workspace 一样，都收敛在 harness tools registry：

```txt
slotflow_context
workspace_list / workspace_read / workspace_tree / workspace_search
artifact_list / workspace_write / artifact_write
task_tool
MCP tools
```

## 输入是什么

`task_tool` 接收三个字段：

```txt
agent_name  子 agent profile 名字
task        要委派的具体任务
context     可选上下文
```

当前默认 profile：

```txt
researcher
coder
reviewer
```

示例：

```json
{
  "agent_name": "coder",
  "task": "检查模块 21 的工具注册顺序",
  "context": "SlotFlow harness tests"
}
```

## 输出是什么

输出是 JSON 字符串：

```json
{
  "status": "completed",
  "agent_name": "coder",
  "task": "检查模块 21 的工具注册顺序",
  "context": "SlotFlow harness tests",
  "result": "真实子 agent 返回的回答文本",
  "source": "slotflow_subagent_task_tool"
}
```

未知 profile 或空任务不会直接抛异常，而是返回：

```json
{
  "status": "error",
  "result": "unknown subagent: missing"
}
```

这样 tool call 协议仍然闭合，主 agent 可以看到结构化错误并继续处理。

## 主要代码

```txt
backend/app/harness/subagents/config.py
  SlotFlowSubagentProfile
  SlotFlowSubagentConfig
  DEFAULT_SUBAGENT_PROFILES

backend/app/harness/subagents/tools.py
  SubagentTaskRunner
  build_subagent_tools()

backend/app/harness/tools/registry.py
  在 subagent_enabled=True 时加入 task_tool

backend/app/harness/config.py
  SlotFlowHarnessConfig.subagent_config

backend/tests/test_harness_subagents.py
  subagent tools 行为和真实 graph 工具调用测试
```

## 测试怎么读

测试保护：

```txt
1. flash/pro 不注册 task_tool
2. ultra 只有在有 model/run_context 时才注册 task_tool
3. task_tool 通过真实子 agent 返回结构化 completed JSON
4. 未知子 agent 返回结构化 error JSON
5. 没有 enabled profile 时不注册 task_tool
6. 真实 LangGraph graph 能执行 task_tool
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_harness_subagents.py tests/test_harness_tools.py tests/test_harness_builder.py
```

## 这一模块不做什么

模块 21/28 当前仍然不做：

```txt
不创建后台任务队列
不实现并发 subagent 调度
不读写 subagent 专属 workspace
不把 subagent 结果写入独立数据库表
不引入 DeerFlow 旧 subagents 包
```

当前重点是让 `task_tool` 进入真实模型执行路径，同时不引入旧项目的大型 scheduler。
真正的隔离执行、并发调度和多 agent 状态管理应该在这个工具边界之后再扩展。
