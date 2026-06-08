# 模块 11：Harness 安全内置工具

模块 10 只是把 agent graph 组装入口迁到 `app/harness/`。

模块 11 开始证明 harness 不只是空壳：它能把工具绑定进 LangGraph agent graph，并让模型完成
一次真实 tool call。

第一批工具故意非常保守：

```txt
不读文件
不写文件
不访问网络
不执行 shell
不依赖 sandbox
```

原因是：当前目标是先证明 tool calling 边界，而不是一上来引入危险执行面。

## 这一层解决什么问题

它解决的是：“SlotFlow harness 怎么决定 agent 能看到哪些工具？”

新边界是：

```txt
RunContext
-> SlotFlowHarnessFeatures
-> build_harness_tools()
-> LangGraph create_agent(tools=...)
```

后续 MCP tools、subagent tools、文件工具策略，都应该进入 `build_harness_tools()` 或它
调用的子 registry，而不是直接散落在 builder/runtime 里。模块 16/20/21 已经分别把
workspace 文件工具、真实 MCP provider、`task_tool` 接进了这条 registry。

## 它接收什么输入

当前 `build_harness_tools()` 接收：

```txt
features          从 RunContext 收窄后的 harness feature flags
extra_tools       测试或后续扩展传入的工具
sandbox_config    workspace 文件工具边界
mcp_config        MCP server 配置
subagent_config   task_tool 子 agent profile 配置
```

模块 21 后，`subagent_enabled` 已经开始控制 `task_tool` 是否注册：

```txt
subagent_enabled -> 是否加入 task tool
MCP enabled      -> 是否加入 MCP tools
sandbox config   -> 文件工具能访问哪些 workspace 资源
```

## 它输出什么数据

输出是 LangChain 能绑定到 agent 的 `BaseTool` 列表。

当前默认内置工具：

```txt
slotflow_context
```

它接收：

```json
{
  "thread_id": "thread_xxx",
  "run_id": "run_xxx",
  "mode": "pro"
}
```

返回：

```json
{
  "thread_id": "thread_xxx",
  "run_id": "run_xxx",
  "mode": "pro",
  "source": "slotflow_context_tool"
}
```

这个工具只是只读上下文摘要。它的价值不是业务功能，而是作为第一颗“安全钉子”，证明
SlotFlow harness 的 tool calling 链路能跑通。

## 它在完整链路里的位置

模块 11 位于 harness builder 内部：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> harness builder
-> harness tools registry  <-- 当前模块
-> LangGraph agent graph
-> AgentEvent / SSE / 前端
```

## 主要代码

```txt
backend/app/harness/tools/__init__.py
backend/app/harness/tools/builtins.py
backend/app/harness/tools/registry.py
backend/app/harness/builder.py
backend/tests/test_harness_tools.py
backend/tests/test_harness_builder.py
```

`builder.py` 现在会调用：

```py
build_harness_tools(features=features, extra_tools=tools)
```

然后再把工具传给 `create_agent()`。

## 重要边界：不是所有模型都支持 tools

真实 DeepSeek/OpenAI chat model 支持 `bind_tools()`，但 LangChain 的一些 fake model 只用于
普通文本测试，没有实现 `bind_tools()`。

所以模块 11 加了一个保护：

```txt
如果模型没有实现 tool binding，harness 不强行传 tools
```

这样模块 7 的 fake model 运行时测试仍然能通过；真正需要证明 tool calling 时，测试使用
一个支持 `bind_tools()` 的 fake model。

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_tools.py
```

它保护三件事：

```txt
1. slotflow_context 是只读 JSON 输出
2. build_harness_tools 会加入安全内置工具，并按 name 去重
3. fake tool-calling model 能通过真实 LangGraph graph 调用 slotflow_context
```

`backend/tests/test_harness_builder.py` 也更新了一个边界测试：

```txt
普通 fake model 没有 bind_tools，builder 会跳过 tools，避免 graph 执行时失败
```

## 这一模块不做什么

当前明确不做：

```txt
不加入 bash
不在模块 11 阶段加入文件写入
不加入网络工具
不在模块 11 阶段加入 MCP
不在模块 11 阶段加入 sandbox
不在模块 11 阶段做文件工具
```

这些能力后续会逐步进入，但第一步必须先让 tool calling 本身可解释、可测试。
