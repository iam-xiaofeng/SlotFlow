# 模块 13：Harness MCP tools 边界

模块 13 给 SlotFlow harness 加入第一版 MCP tools 边界。

这里先不连接真实 MCP server，也不启动外部进程。当前目标只是把边界钉住：

```txt
MCP env config
-> SlotFlowMcpConfig
-> McpToolProvider
-> build_harness_tools()
-> LangGraph create_agent(tools=...)
```

真实 `MultiServerMCPClient` 后续只需要实现 provider，不应该直接塞进 FastAPI 路由或
chat runtime。

## 这一层解决什么问题

它解决的是：“以后外部 MCP tools 应该从哪里进入 agent graph？”

模块 11 已经有内置工具 registry：

```txt
build_harness_tools()
-> slotflow_context
-> LangGraph tools
```

模块 13 把 MCP 也收敛到同一个入口：

```txt
build_harness_tools()
-> builtin tools
-> MCP tools
-> dedupe by tool.name
```

这样后续 builtin、MCP、subagent、skills allowed-tools 策略都可以在 harness tools registry
统一处理。

## 它接收什么输入

runtime 从环境变量读取第一版 MCP 配置：

```txt
SLOTFLOW_MCP_ENABLED=true
SLOTFLOW_MCP_SERVERS=filesystem,search
```

转换成：

```py
SlotFlowMcpConfig(
    enabled=True,
    servers=(
        SlotFlowMcpServerConfig(name="filesystem"),
        SlotFlowMcpServerConfig(name="search"),
    ),
)
```

`SLOTFLOW_MCP_ENABLED` 只接受这些布尔值：

```txt
true:  1 / true / yes / on
false: 0 / false / no / off
```

## 它输出什么数据

`load_mcp_tools()` 输出：

```txt
list[BaseTool]
```

当前默认 provider 是 `EmptyMcpToolProvider`，不会连接任何外部 MCP server，只返回空列表。

测试里的 fake provider 会返回一个 LangChain `BaseTool`，用来证明边界能工作。

## 它在完整链路里的位置

模块 13 位于 harness builder 内部：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> harness builder
-> harness tools registry
-> MCP tools loader      <-- 当前模块
-> LangGraph agent graph
-> AgentEvent / SSE / 前端
```

注意：`chat.runtime` 只读取配置，不加载 MCP tools。真正加载工具仍在
`app/harness/tools/registry.py`。

## 主要代码

```txt
backend/app/harness/mcp/__init__.py
backend/app/harness/mcp/config.py
backend/app/harness/mcp/loader.py
backend/app/harness/config.py
backend/app/harness/tools/registry.py
backend/app/harness/builder.py
backend/app/chat/runtime.py
backend/tests/test_harness_mcp.py
```

`SlotFlowHarnessConfig` 现在增加：

```txt
mcp_config
mcp_tool_provider
```

`SlotFlowRuntimeConfig` 也保存同样的 MCP 配置，但 runtime 不直接操作 provider。

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_mcp.py
```

它保护三件事：

```txt
1. MCP disabled 时不会调用 provider
2. enabled server 会过滤掉 disabled server 后再交给 provider
3. build_harness_tools 会把 MCP tools 加到 slotflow_context 后面
```

`backend/tests/test_harness_builder.py` 还保护一件事：

```txt
harness builder 会把 mcp_config / mcp_tool_provider 传进 tools registry
```

`backend/tests/test_runtime.py` 保护：

```txt
环境变量只被解析成 SlotFlowMcpConfig，不在 runtime 阶段连接外部 MCP
```

## 这一模块不做什么

当前明确不做：

```txt
不启动 MCP server
不连接 stdio / HTTP MCP server
不引入 MultiServerMCPClient
不解析复杂 JSON server 配置
不做工具权限过滤
不执行 skill 里的 allowed-tools 策略
```

这些留到后续模块。模块 13 只证明 MCP tools 的入口位置和配置传递路径是稳定的。
