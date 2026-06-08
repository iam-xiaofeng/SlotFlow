# 模块 20：真实 MCP provider

模块 20 把模块 13 的 MCP 边界推进到真实 provider：

```txt
MultiServerMcpToolProvider
```

它使用 LangChain 官方 adapter：

```txt
langchain-mcp-adapters
MultiServerMCPClient
```

模块 20 不自己实现 MCP 协议，只把 SlotFlow 的 MCP 配置转换成官方客户端能识别的连接配置。

## 这一层解决什么问题

模块 13 已经把入口钉住：

```txt
SlotFlowMcpConfig
-> McpToolProvider
-> build_harness_tools()
```

但当时默认 provider 是空实现，不会连接真实 MCP server。

模块 20 解决的是：

```txt
读取真实 MCP server 连接配置
-> 调用 MultiServerMCPClient.get_tools()
-> 得到 LangChain BaseTool
-> 进入 harness tools registry
-> 绑定给 LangGraph agent
```

## 它在完整链路里的位置

```txt
前端输入
-> FastAPI chat routes
-> runtime
-> ensure_mcp_tools_loaded()
-> MultiServerMcpToolProvider.aload_tools()
-> MultiServerMCPClient.get_tools()
-> harness builder
-> build_harness_tools()
-> MultiServerMcpToolProvider.load_tools()
-> LangGraph create_agent(tools=...)
```

注意这里分成两步：

```txt
aload_tools()  异步连接/加载 MCP tools
load_tools()   同步返回已缓存 tools
```

原因是 SlotFlow 当前 graph stream 是 async，但 harness builder 和 tools registry 仍是同步边界。
所以 runtime 先异步准备，builder 再同步读取。

## 输入是什么

模块 13 的名字列表仍然保留：

```bash
SLOTFLOW_MCP_ENABLED=true
SLOTFLOW_MCP_SERVERS=filesystem,search
```

这种配置只表达“有哪些 server 名字”，不会自动连接真实 server。

模块 20 新增真实连接配置：

```bash
SLOTFLOW_MCP_CONFIG_JSON='{
  "filesystem": {
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "my_mcp_server"]
  },
  "search": {
    "transport": "streamable_http",
    "url": "http://localhost:8000/mcp"
  }
}'
```

如果设置了 `SLOTFLOW_MCP_CONFIG_JSON`，默认认为 MCP enabled。也可以显式关闭：

```bash
SLOTFLOW_MCP_ENABLED=false
```

单个 server 可以禁用：

```json
{
  "disabled_server": {
    "enabled": false,
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "unused_server"]
  }
}
```

## 输出是什么

输出仍然是：

```txt
list[BaseTool]
```

这些 tool 会和 SlotFlow 内置工具、workspace 工具一起进入：

```txt
build_harness_tools()
```

模块 20 不改 SSE 事件名，也不改 ChatRepository。

## 主要代码

```txt
backend/app/harness/mcp/loader.py
  MultiServerMcpToolProvider
  ensure_mcp_tools_loaded()
  build_multi_server_mcp_connections()

backend/app/harness/mcp/__init__.py
  导出真实 provider

backend/app/chat/runtime.py
  解析 SLOTFLOW_MCP_CONFIG_JSON
  在 graph 创建前预加载 MCP tools

backend/tests/test_harness_mcp.py
  provider 单元测试，使用 fake client，不启动真实 server

backend/tests/test_runtime.py
  runtime JSON 配置和预加载时机测试
```

## 测试怎么读

测试保护：

```txt
1. disabled server 仍会被过滤
2. 没有连接 config 的 server 不能交给真实 provider
3. MultiServerMcpToolProvider 会把 SlotFlow config 转成 MultiServerMCPClient connections
4. 真实 provider 必须先 async preload，再允许同步 tools registry 读取
5. runtime 读取 SLOTFLOW_MCP_CONFIG_JSON 后会自动创建真实 provider
6. deepseek stream 在创建 graph 前会调用 async MCP preload
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_harness_mcp.py tests/test_runtime.py
```

## 这一模块不做什么

模块 20 明确不做：

```txt
不启动一个真实 MCP server 做集成测试
不做 OAuth / API key 注入策略
不做 MCP tool 权限白名单
不做 tool name 重命名策略
不把 MCP provider 放进 FastAPI route
不把 MCP tools 存进业务数据库
```

后续如果要给真实 MCP server 做 smoke test，应该单独提供本地 server 配置和明确的环境变量，
不要让普通测试依赖外部进程或网络。
