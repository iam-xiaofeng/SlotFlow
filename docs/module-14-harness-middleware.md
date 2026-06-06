# 模块 14：Harness middleware registry

模块 14 给 SlotFlow harness 加入第一版 middleware 边界。

这里的 middleware 指 LangChain agent middleware，不是 FastAPI middleware。它运行在 agent graph
内部，可以在模型调用前后、工具调用前后，或者 agent 执行前后观察和调整状态。

第一版不搬 DeerFlow 的复杂 middleware，只做一个安全内置 middleware：

```txt
SlotFlowRuntimeSummaryMiddleware
```

它只在 `before_agent` 阶段把当前 `RunContext` 的摘要写进 graph state 的
`slotflow.runtime`，不改消息、不拦截模型、不拦截工具。

## 这一层解决什么问题

它解决的是：“SlotFlow 自己的 agent middleware 应该从哪里进入 LangGraph graph？”

模块 10 已经有 builder：

```txt
build_slotflow_harness_graph()
-> create_agent(...)
```

模块 14 把 middleware 组装收敛到：

```txt
build_harness_middleware()
-> SlotFlowRuntimeSummaryMiddleware
-> create_agent(middleware=...)
```

后续如果加 uploads、sandbox、tool error handling、dangling tool call、dynamic context 等能力，
都应该先进入 `app/harness/middleware/`，不要散落在 `chat.runtime` 或 FastAPI 路由里。

## 它接收什么输入

registry 接收：

```txt
features           从 RunContext 收窄出的 SlotFlowHarnessFeatures
middleware_config  SlotFlow 自己的 middleware 开关
extra_middleware   测试或后续扩展传入的外部 middleware
```

runtime 现在读取一个环境变量：

```txt
SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE=false
```

默认值是 `true`。它只控制当前第一颗内置 middleware 是否启用。

## 它输出什么数据

`build_harness_middleware()` 输出：

```txt
list[AgentMiddleware]
```

默认输出：

```txt
[SlotFlowRuntimeSummaryMiddleware]
```

如果关闭开关：

```txt
[]
```

registry 会按 `middleware.name` 去重，保留更早出现的实例。这样测试或后续自定义 middleware
可以覆盖同名内置 middleware。

## 它在完整链路里的位置

模块 14 位于 harness builder 内部：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> harness builder
-> middleware registry      <-- 当前模块
-> LangGraph create_agent
-> AgentEvent / SSE / 前端
```

注意边界：

```txt
FastAPI middleware 处理 HTTP 请求
LangChain AgentMiddleware 处理 agent graph 内部执行
```

这两个不是一类东西。

## 主要代码

```txt
backend/app/harness/middleware/__init__.py
backend/app/harness/middleware/config.py
backend/app/harness/middleware/builtins.py
backend/app/harness/middleware/registry.py
backend/app/harness/config.py
backend/app/harness/builder.py
backend/app/chat/runtime.py
backend/tests/test_harness_middleware.py
```

`SlotFlowHarnessConfig` 现在增加：

```txt
middleware_config
```

`SlotFlowRuntimeConfig` 也保存同样的配置，但 runtime 不直接创建 middleware。

## 当前内置 middleware 做了什么

`SlotFlowRuntimeSummaryMiddleware.before_agent(...)` 读取：

```txt
runtime.context
features
```

写入：

```py
{
    "slotflow": {
        "runtime": {
            "thread_id": "...",
            "run_id": "...",
            "model_name": "...",
            "mode": "ultra",
            "agent_name": "default",
            "thinking_enabled": True,
            "plan_enabled": True,
            "subagent_enabled": True,
            "files_count": 1,
        }
    }
}
```

这份摘要只服务于学习和后续调试。它不是 HTTP 响应协议，也不是 SSE 元数据。

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_middleware.py
```

它保护五件事：

```txt
1. runtime summary middleware 会保留原有 slotflow state
2. middleware 会写入 RunContext 摘要和 feature flags
3. registry 默认加入 runtime summary middleware
4. config 可以关闭内置 middleware
5. 真实 LangGraph fake graph 会执行 before_agent 并返回 slotflow.runtime
```

`backend/tests/test_harness_builder.py` 还保护：

```txt
harness builder 会通过 middleware registry 组装 middleware
```

`backend/tests/test_runtime.py` 保护：

```txt
环境变量只解析成 SlotFlowMiddlewareConfig，不在 runtime 阶段实例化 middleware
```

## 这一模块不做什么

当前明确不做：

```txt
不接 DeerFlow 旧 middleware
不加入 uploads / sandbox / memory / title middleware
不拦截模型调用
不拦截工具调用
不改写消息列表
不处理工具错误
不处理 dangling tool call
```

这些能力后续可以逐个模块加。模块 14 只先证明 middleware 入口位置、开关和执行链路。
