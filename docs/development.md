# 开发笔记

所有开发都在 WSL 里进行。

```txt
workspace: /home/dell/code/SlotFlow
reference repo: /mnt/d/test/deer-flow
```

不要把 Python 虚拟环境或 `node_modules` 装到 `/mnt/c`、`/mnt/d` 下面。那些路径是
Windows 挂载进 WSL 的文件系统，Linux 工具在上面会更慢，也更容易遇到奇怪的权限和
文件监听问题。

## 已验证命令

后端：

```bash
cd ~/code/SlotFlow/backend
uv run pytest -q
```

前端：

```bash
cd ~/code/SlotFlow/frontend
pnpm install
pnpm typecheck
pnpm build
```

整个仓库：

```bash
cd ~/code/SlotFlow
make verify
```

## Streaming API 决策

当前优先级：先跑通 SlotFlow 自己的后端学习链路，再进入前端。

```txt
LangGraph v3 typed projections
-> AgentEvent
-> BusinessSseEvent
-> SSE frame
-> FastAPI chat routes
-> backend TestClient chain
-> DeepSeek live smoke test
-> frontend SSE parser
-> frontend chat state
```

最新 LangChain/LangGraph 文档推荐新应用使用
`stream_events(..., version="v3")`。这个 API 会给出 typed projections，例如：

```txt
messages
values
tool_calls
output
extensions
```

SlotFlow 已经把模块四改成这个方向：真实 graph 通过
`await graph.astream_events(..., version="v3")` 拿到 run stream。FastAPI 使用的是异步
路径，本地实测的 `AsyncGraphRunStream` 当前没有 `ainterleave(...)`，所以代码直接消费
主事件日志里的 `method` 和 `params.data`，再映射成自己的 `AgentEvent`。

这样做比旧式 `astream(stream_mode=[...])` 更贴近官方新接口，也更适合前端消费。

实际规则：

```txt
1. 默认使用 v3 event streaming。
2. 业务层只认识 AgentEvent，不直接依赖 LangGraph 投影对象。
3. SSE 层只认识 AgentEvent，不直接依赖 LangGraph 或 DeepSeek。
4. 只有在真实 harness agent 明确不支持 v3 时，才回退到 astream(stream_mode=[...])。
5. 任何回退都必须写清楚具体版本、具体 API、具体失败原因。
```

## Live Smoke Test 原则

真实 DeepSeek 调用不进入 `make verify`。原因是它依赖网络、API key、余额、模型服务状态，
不适合作为日常健康闸门。

日常测试使用 `StaticProjectionAgentAdapter`。它不假装是旧 API，而是模拟 v3 projection
之后的业务事件顺序。

需要验证真实模型时，单独运行 live smoke test，并临时提供 `DEEPSEEK_API_KEY`。
