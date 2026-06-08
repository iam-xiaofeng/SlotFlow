# 模块 7：SlotFlow 本地 runtime 装配层

## 这一模块解决什么问题

前面模块 1-6 已经把后端学习链路跑通了，但还有一个运行时问题没有单独收拢：

```txt
真实 agent 在哪里创建？
checkpointer 在哪里挂进去？
本地 static / 真实 DeepSeek / 后续更完整 runtime 怎么切换？
```

如果这些事情继续散落在 `main.py`、live smoke test、临时脚本里，后面一旦开始吸收
DeerFlow 有价值的 harness 思路，边界就会重新变乱。

所以模块 7 做的不是“引入 DeerFlow 包”，而是：

```txt
参考 DeerFlow 的运行时装配思路
-> 在 SlotFlow 里本地重写一个更小的 runtime 边界
-> 保持现有 AgentEvent / SSE / FastAPI 路由契约不变
```

## 它在完整链路里的位置

它插在 `create_app` 和真实 LangGraph graph 之间：

```txt
前端输入
-> FastAPI route
-> run 配置
-> SlotFlow runtime 装配层  <-- 当前模块
-> LangGraph agent graph
-> AgentEvent
-> BusinessSseEvent
-> SSE frame
-> 前端
```

这层的职责不是发 SSE，也不是改业务事件名。它只负责决定：

```txt
这次 run 用哪个 adapter 模式
这次 graph 要不要带 checkpointer
真实 graph 应该如何创建
```

## 文件结构

```txt
backend/app/chat/runtime.py
backend/tests/test_runtime.py
```

`main.py` 现在会通过这层构建默认 adapter：

```txt
create_app()
-> build_agent_adapter(...)
-> RuntimeBackedAgentAdapter / StaticProjectionAgentAdapter
```

## 输入是什么

runtime 层当前只接收一个很小的配置对象：

```py
SlotFlowRuntimeConfig(
    adapter_mode="static" | "deepseek",
    model_name="deepseek-v4-flash",
    checkpointer_backend="none" | "memory",
    system_prompt="...",
    prefer_projection_stream=True,
)
```

以及一次具体 run 的：

```txt
ChatStreamRequest
RunConfigBundle
```

这里有个关键点：

```txt
runtime 配置决定“默认运行时策略”
bundle.context 决定“这次 run 的具体模型/模式/thread_id”
```

所以真实 graph 的创建不能在 app 启动时一次性写死，否则 `request.model_name`
后面就变成摆设了。

## 输出是什么

runtime 层对外仍然只输出现有边界认识的东西：

```txt
AgentAdapter
```

也就是说，路由层完全不用知道：

```txt
graph 是什么时候创建的
有没有 checkpointer
底层是 static 还是 deepseek
```

它只继续调用：

```py
adapter.stream_events(request=..., bundle=...)
```

## 当前实现做了什么

### 1. 增加 `SlotFlowRuntimeConfig`

它是 SlotFlow 自己的最小运行时配置，不引入 DeerFlow 旧网关里那种大配置树。

### 2. 增加 `create_checkpointer`

当前只支持：

```txt
none
memory -> langgraph.checkpoint.memory.InMemorySaver
```

模块 19 已经在这个边界上继续扩展了 SQLite / Postgres 持久化 checkpointer。模块 7
保留的是最初的 runtime 装配思路。

### 3. 增加 `RuntimeBackedAgentAdapter`

这是本模块最重要的对象。

它解决两个问题：

```txt
1. 保持路由层继续只依赖 AgentAdapter
2. 把真实 graph 的创建推迟到每次 run 调用时
```

这样一来：

```txt
app 启动时不写死具体模型
每次 run 仍可根据 bundle.context.model_name 选择真实模型
共享 checkpointer 仍然可以保留同一个 thread_id 的多轮状态
```

### 4. `create_app` 改成走 runtime builder

现在默认路径不再是：

```txt
create_app -> StaticProjectionAgentAdapter()
```

而是：

```txt
create_app -> build_agent_adapter(runtime_config)
```

默认 `runtime_config` 仍然落到 `static`，所以本地开发和测试行为不变，不会突然要求
API key。

## 测试怎么读

测试文件是：

```txt
backend/tests/test_runtime.py
```

它保护四件事：

```txt
1. 默认 runtime 配置仍然是 static
2. checkpointer 当前只支持 none / memory
3. static 模式下仍然流出同样的 AgentEvent 序列
4. deepseek 模式下：
   - 每次 run 会按请求里的 model_name 动态创建模型
   - 同一个 thread_id 的第二轮能读到第一轮 state
```

第 4 点很关键，因为它证明了这层不是“只有装配外壳”，而是已经具备最小的
checkpointer 价值。

## 这一模块不做什么

当前 runtime 层明确不做：

```txt
不直接引入 DeerFlow 包
不迁移 DeerFlow 全量 tools / middlewares
不在模块 7 阶段做 SQLite / Postgres checkpointer
不改变 AgentEvent / BusinessSseEvent / SSE 事件名
不改 FastAPI 路由契约
```

## 下一步最自然接什么

模块 7 落下以后，后面最自然的方向是继续扩 SlotFlow 自己的本地 runtime，而不是回头
接 DeerFlow 包：

```txt
1. 在 runtime 层增加更像 harness 的 graph builder
2. 把 mode -> feature flags 真正映射到本地工具/中间件开关
3. 再考虑 SQLite/Postgres checkpointer
```

顺序上，应该先扩本地 runtime 边界，再决定哪些 DeerFlow 能力值得继续吸收。
