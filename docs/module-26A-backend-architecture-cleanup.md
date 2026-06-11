# 模块 26A：后端架构整理和过渡接口清理

## 这个模块解决什么问题？

模块 1 到模块 25 是边学边搭的过程，后端里保留了一些早期为了测试、教学、兼容探索而存在的
设计。进入端到端运行后，这些东西会让项目看起来不够干净：

```txt
生产代码里有 StaticProjectionAgentAdapter
runtime 里有 static / deepseek 双模式
LangGraph adapter 里有 raw protocol fallback
测试直接复用生产静态 adapter
文档仍描述旧的过渡路径
```

模块 26A 的目标是把后端收敛到一条真实运行主线：

```txt
FastAPI route
-> RuntimeBackedAgentAdapter
-> LangGraph harness graph
-> LangGraph v3 typed projections
-> AgentEvent
-> Business SSE
```

## 删除了什么？

### 1. 删除生产静态 adapter

删除：

```txt
StaticProjectionAgentAdapter
split_text()
```

这些只用于早期模拟流式输出。现在测试需要稳定输出时，在测试文件里定义小 fake，不再把测试
替身放进 `app/` 生产代码。

### 2. 删除 runtime 模式切换

删除：

```txt
RuntimeMode = "static" | "deepseek"
SlotFlowRuntimeConfig.adapter_mode
SLOTFLOW_AGENT_MODE 读取逻辑
```

现在 `RuntimeBackedAgentAdapter` 永远构建真实 LangGraph harness graph。日常测试如果不想
调用真实 DeepSeek，通过 `model_factory` 注入 `FakeListChatModel`。

### 3. 删除 raw protocol fallback

删除：

```txt
prefer_projection_stream
protocol_event_to_agent_event()
raw event method / params.data fallback
```

当前主线只消费 LangGraph v3 typed projections：

```txt
messages
values
tool_calls
```

如果未来某个真实 agent 真的不支持 projections，需要先记录具体版本和失败原因，再作为新的
显式设计加入，不能提前把兼容分支留在生产代码里。

## 保留了什么？

### AgentAdapter 协议

`AgentAdapter` 协议仍然保留，因为 FastAPI 路由只应该依赖一个很小的业务边界：

```txt
ChatStreamRequest + RunConfigBundle
-> AsyncIterator[AgentEvent]
```

这不是过渡接口，而是路由层和 agent/runtime 层之间的稳定边界。

### LangGraphEventAgentAdapter

真实 adapter 仍然保留，但它现在只负责一件事：

```txt
LangGraph v3 typed projections
-> SlotFlow AgentEvent
```

它不再知道 static mode，也不再处理 raw protocol event。

### 测试 fake

测试 fake 没有消失，只是移动到了测试文件内部：

```txt
tests/test_chat_routes.py::CompletedAgentAdapter
tests/test_chat_routes.py::BrokenAgentAdapter
RuntimeBackedAgentAdapter(model_factory=FakeListChatModel)
```

这样测试仍然稳定，但生产包不再暴露测试用 adapter。

## 主要改动文件

```txt
backend/app/chat/agent_adapter.py
backend/app/chat/runtime.py
backend/app/main.py
backend/app/harness/builder.py
backend/app/chat/__init__.py
backend/tests/test_agent_adapter.py
backend/tests/test_chat_routes.py
backend/tests/test_runtime.py
backend/tests/test_sse.py
backend/tests/test_live_deepseek.py
docs/development.md
docs/rewrite-boundary.md
```

## 当前后端启动路径

应用启动：

```txt
create_app()
-> build_agent_adapter()
-> load_runtime_config_from_env()
-> RuntimeBackedAgentAdapter
```

用户发送消息：

```txt
POST /api/chat/threads/{thread_id}/runs/stream
-> build_run_config()
-> RuntimeBackedAgentAdapter.stream_events()
-> create_langgraph_agent_graph()
-> LangGraphEventAgentAdapter.stream_events()
-> iter_projection_agent_events()
-> iter_business_events()
-> StreamingResponse
```

## 验收结果

后端重点测试：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest tests/test_agent_adapter.py tests/test_sse.py tests/test_chat_routes.py tests/test_runtime.py tests/test_health.py
```

结果：

```txt
39 passed
```

Ruff：

```bash
cd /home/dell/code/SlotFlow/backend
uv run ruff check app tests/test_agent_adapter.py tests/test_sse.py tests/test_chat_routes.py tests/test_runtime.py tests/test_live_deepseek.py
```

结果：

```txt
All checks passed
```

本地服务：

```txt
uvicorn pid=5106
GET http://127.0.0.1:8000/health -> 200 OK
```

真实 SSE 简短验收：

```json
{
  "networkChunks": 6,
  "events": 15,
  "deltas": 11,
  "firstChunkMs": 1908,
  "firstDeltaMs": 6128,
  "finishedMs": 6160,
  "totalMs": 6160
}
```

## 注意事项

本地 `.env` 里如果还留着：

```txt
SLOTFLOW_AGENT_MODE=deepseek
```

现在它已经不再被读取。后端默认就是真实 LangGraph/DeepSeek-compatible runtime。真正仍然有用的
核心环境变量是：

```txt
DEEPSEEK_API_KEY
SLOTFLOW_DEEPSEEK_MODEL
SLOTFLOW_CHECKPOINTER_BACKEND
SLOTFLOW_CORS_ORIGINS
```
