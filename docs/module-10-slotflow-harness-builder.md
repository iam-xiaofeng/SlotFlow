# 模块 10：SlotFlow harness builder 骨架

模块 10 开始把后端重心从“能跑通 stream”转到“能组装真实 agent 能力”。

前面模块 7 的 `runtime.py` 里直接写了：

```py
create_agent(
    model=model,
    tools=[],
    system_prompt=...,
    context_schema=RunContext,
    checkpointer=checkpointer,
)
```

这在学习早期是合理的，但后面要加入 tools、skills、MCP、middleware 时，如果继续把组装逻辑
塞在 `chat/runtime.py`，边界会重新变乱。

模块 10 的目标是先落下一个清楚边界：

```txt
chat/runtime.py         选择运行模式、模型、checkpointer
app/harness/builder.py  组装 LangGraph agent graph
```

## 这一层解决什么问题

它解决的是：“真实 agent graph 到底在哪里创建？”

新的依赖方向是：

```txt
FastAPI route
-> chat/runtime.py
-> harness/builder.py
-> langchain.agents.create_agent
```

反方向不允许：

```txt
harness -> FastAPI route
harness -> ChatRepository
harness -> SSE encoder
harness -> frontend
```

这样后续增加 tools / skills / MCP / middleware 时，只扩展 `app/harness/`，不污染路由层和
SSE 层。

## 它接收什么输入

`build_slotflow_harness_graph()` 接收：

```txt
model             已创建好的 chat model
run_context       模块 3 产出的 RunContext
harness_config    harness 自己需要的最小配置
checkpointer      可选 LangGraph checkpointer
tools             可选工具列表，模块 11 后由 registry 产生
middleware        可选 middleware 列表，模块 14 后由 chain 产生
```

其中 `run_context` 不会被原样当成 feature 配置。模块 10 先把它收窄成：

```txt
thinking_enabled
plan_enabled
subagent_enabled
```

也就是：

```txt
RunContext
-> features_from_run_context()
-> SlotFlowHarnessFeatures
```

## 它输出什么数据

输出是 LangGraph agent graph，也就是后面会被 `LangGraphEventAgentAdapter` 消费的对象：

```txt
CompiledStateGraph
```

路由层仍然不知道这个 graph 怎么创建，只继续消费：

```txt
AgentAdapter.stream_events(...)
```

## 它在完整链路里的位置

模块 10 位于 runtime 和 LangGraph graph 之间：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> SlotFlow harness builder  <-- 当前模块
-> LangGraph agent graph
-> AgentEvent
-> BusinessSseEvent
-> 前端
```

## 主要代码

```txt
backend/app/harness/__init__.py
backend/app/harness/builder.py
backend/app/harness/config.py
backend/app/harness/features.py
backend/app/harness/state.py
backend/app/chat/runtime.py
backend/tests/test_harness_builder.py
```

`runtime.py` 现在仍然保留 `create_langgraph_agent_graph()`，但它内部已经委托给：

```py
build_slotflow_harness_graph(...)
```

这样模块 7 的外部边界稳定，模块 10 的内部组装边界也清楚。

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_builder.py
```

它保护三件事：

```txt
1. RunContext 会被收窄成 SlotFlowHarnessFeatures
2. harness builder 会把 model/tools/middleware/checkpointer/system_prompt 传给 graph 创建边界
3. chat/runtime.py 不再自己组装 create_agent，而是委托给 harness builder
```

这一模块还不加入真实工具、skills、MCP 或 middleware。它只负责把“agent graph 组装入口”
从 runtime 中拆出来。
