# 模块 04：Agent 事件适配层

模块四开始把项目方向从“假流”切到真实 LangGraph/LangChain 的当代接口：

```txt
LangGraph v3 typed projections
-> SlotFlow AgentEvent
```

这里还不写 FastAPI，也不写 SSE。原因很简单：如果一上来就把 agent、HTTP、SSE、
仓库全放在一起，出错时很难判断是模型、网络、事件格式还是路由出了问题。

模块四先固定 agent 边界：不管底层是 DeepSeek、LangChain agent，还是测试里的静态
模拟器，上层都只接收 `AgentEvent`。

## 这一层解决什么问题

官方最新文档推荐新应用使用：

```py
stream = await agent.astream_events(input, version="v3")
```

这个 API 返回的不是旧式 `(mode, chunk)` 元组，而是一组 typed projections，例如：

```txt
stream.messages
stream.values
stream.tool_calls
stream.output
stream.extensions
```

这些投影对应用开发更友好，但它们仍然是 LangGraph 的内部输出。SlotFlow 不让前端
直接认识这些对象，而是先翻译成自己的业务事件。

## 输入长什么样

模块四接收两份输入。

第一份是前端请求整理后的 `ChatStreamRequest`：

```json
{
  "message": "分析上传内容",
  "model_name": "deepseek-v4-flash",
  "mode": "ultra",
  "agent_name": "default",
  "files": ["upload_1", "upload_2"],
  "metadata": {}
}
```

第二份是模块三产出的 `RunConfigBundle`：

```json
{
  "config": {
    "configurable": {
      "thread_id": "thread_test"
    }
  },
  "context": {
    "thread_id": "thread_test",
    "run_id": "run_test",
    "model_name": "deepseek-v4-flash",
    "mode": "ultra",
    "agent_name": "default",
    "files": ["upload_1", "upload_2"],
    "thinking_enabled": true,
    "is_plan_mode": true,
    "subagent_enabled": true
  }
}
```

真正喂给 LangChain agent 的输入会被整理成：

```json
{
  "messages": [
    {
      "role": "user",
      "content": "分析上传内容"
    }
  ]
}
```

后续模块六接入仓库后，可以把 thread 历史消息也拼进这个 `messages` 数组。

## 输出长什么样

模块四输出的是 `AgentEvent`：

```json
{
  "event": "message.delta",
  "data": {
    "message_id": "run_test:assistant",
    "role": "assistant",
    "delta": "第一段回答",
    "index": null
  }
}
```

当前保留五类业务事件：

```txt
run.prepared    后端已经创建 run，并整理好本次 agent 配置
message.delta   assistant 文本片段，前端用它做流式显示
tool.delta      工具调用片段，后面 UI 可以单独展示
state.snapshot  graph 状态快照，前端用它同步最终状态
run.finished    agent 正常结束
```

一次完整的学习模拟流大概是：

```txt
run.prepared
message.delta
message.delta
state.snapshot
run.finished
```

## 主要代码

文件：

```txt
backend/app/chat/agent_adapter.py
```

核心对象：

```txt
AgentEvent
AgentAdapter
StaticProjectionAgentAdapter
LangGraphEventAgentAdapter
```

`StaticProjectionAgentAdapter` 用于测试和学习。它不调用模型，只模拟 v3 projection 之后
的业务事件顺序。

`LangGraphEventAgentAdapter` 用于真实 graph。它会先调用：

```py
run_stream = await graph.astream_events(..., version="v3", context=...)
async for event in iter_projection_agent_events(run_stream, bundle=bundle):
    yield event
```

主路径优先消费官方 v3 typed projections：

```txt
stream.messages   -> message.delta
stream.values     -> state.snapshot
stream.tool_calls -> tool.delta
```

其中 `stream.messages` 在异步 lane 里不是直接给文本，而是先给一个“消息子流”，子流里再
逐条出现 `content-block-delta` 这样的 message 事件：

```json
{
  "event": "content-block-delta",
  "delta": {
    "type": "text-delta",
    "text": "第一段回答"
  }
}
```

SlotFlow 会先把这些 LangGraph projection item 展开，再统一翻译成自己的
`AgentEvent`。对外链路仍然保持：

```txt
LangGraph projection
-> AgentEvent
-> BusinessSseEvent
-> SSE frame
```

这里有一个实测细节：同步 `stream_events(..., version="v3")` 返回的 `GraphRunStream`
有 `interleave(...)`；但 FastAPI 路由里会使用异步
`astream_events(..., version="v3")`，它返回的 `AsyncGraphRunStream` 当前没有官方
`ainterleave(...)`。这意味着如果我们分别消费多个 projection channel，就拿不到
LangGraph 主日志那种严格的跨 channel 顺序。

所以当前实现是：

```txt
优先走 projection path
-> 如果 messages / values / tool_calls 能正常产出 AgentEvent，就直接返回
-> 如果 projection lane 缺失或当前异步 API 能力不够，再退回 raw protocol log
```

raw fallback 仍然是 v3 协议，只是从主事件日志里读取每条 event 的 `method` 和
`params.data`：

```json
{
  "method": "messages",
  "params": {
    "data": {
      "delta": "第一段回答"
    }
  }
}
```

保留这条 fallback 不是因为 SlotFlow 还想走旧实现，而是因为当前异步 v3 API 在
“projection 间保序”这件事上还有能力缺口。测试会专门保护“默认先走 projections，
只有缺口时才回 raw”。

### `context` 和 `configurable` 怎么分

这一层故意不把 `RunContext` 里的业务字段塞进 `config["configurable"]`。

当前规则是：

```txt
config["configurable"] 只放真正给 runtime/checkpointer 用的字段
目前只有 thread_id
```

`bundle.context` 会通过：

```py
await graph.astream_events(..., context=bundle.context)
```

显式传给 graph。

如果未来真实 graph 节点要读 `mode/files/feature flags`，正确做法是：

```py
graph = create_agent(..., context_schema=RunContext)
await graph.astream_events(..., context=...)
```

而不是把这些业务字段混进 `configurable`。当前 `create_deepseek_agent_adapter`
已经把 `context_schema=RunContext` 补上，和这条方向保持一致。

不过要注意：目前模块四测试里的 fake graph 还没有真正读取这些字段，所以在当前学习
阶段，`RunContext` 主要仍是 adapter 外层的业务上下文，而不是 graph state 本身。

### `state.snapshot` 的语义

`state.snapshot` 的 `data["state"]` 表示“graph state 的归一化快照”，不是
`RunContext` 回显。

结构上故意分成两层：

```json
{
  "thread_id": "thread_test",
  "run_id": "run_test",
  "messages": [...],
  "state": {
    "messages": [...]
  }
}
```

其中：

```txt
thread_id / run_id  是 SlotFlow 事件归属信息
state               是 graph state 快照
messages            是从 state 里提取出来的常用字段，方便前端/路由直接读取
```

`StaticProjectionAgentAdapter` 里的 `state.snapshot` 仍然是教学模拟数据，不是真实
LangGraph values。但它会尽量贴近真实 `values` 的结构，并在代码注释里明确这是模拟。

还有一个真实测试抓到的细节：`values` 里的 state 可能带着 LangChain 的
`HumanMessage`、`AIMessage` 这类 Python 对象。它们不能直接 `json.dumps`，所以
`state.snapshot` 进入 SSE 之前必须被压成普通 JSON 数据：

```txt
HumanMessage(content="ping")
-> {"role": "human", "content": "ping"}
```

这个归一化在 `normalize_values_snapshot` 和 `to_jsonable` 里完成。模块五的 SSE 测试会
专门验证真实 v3 adapter 产出的 `state.snapshot` 可以被编码成 SSE frame。

## 测试怎么读

测试文件：

```txt
backend/tests/test_agent_adapter.py
```

重点看这些边界：

```txt
1. build_agent_input 是否生成 LangChain agent 要的 {"messages": [...]} 形状
2. message projection item 是否变成 message.delta
3. values projection 是否变成 state.snapshot
4. 默认主路径是否优先消费 projection，而不是直接读 raw 主日志
5. raw protocol fallback 是否只在 projection lane 不可用时保留
6. `context` 是否走 `context=` 而不是混进 configurable
7. 静态 adapter 是否能产出 prepared -> delta -> snapshot -> finished
8. LangChain fake model 是否能跑通真实 v3 stream 并产出 delta/snapshot/finished
9. 真实 v3 adapter 产出的 state.snapshot 是否能被 JSON 编码成 SSE
```

这组测试不调用 DeepSeek。真实模型会单独做 smoke test，避免 `make verify` 受网络、
余额、模型限流影响。
