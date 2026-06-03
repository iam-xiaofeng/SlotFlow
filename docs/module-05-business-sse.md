# 模块 05：业务 SSE 事件

模块四已经把 LangGraph v3 typed projections 翻译成了 SlotFlow 自己的 `AgentEvent`。
模块五继续把它变成浏览器能读的 SSE 文本帧。

链路是：

```txt
AgentEvent
-> BusinessSseEvent
-> SSE frame
```

这里仍然不启动 FastAPI。模块五只是把“事件怎么命名、数据怎么编码”固定下来。

## 这一层解决什么问题

浏览器收到的 SSE 不是 Python 对象，而是一段一段文本：

```txt
event: message.delta
data: {"delta":"你好"}

event: run.finished
data: {"run_id":"run_123"}

```

前端会根据 `event:` 后面的名字决定更新哪个状态：

```txt
message.delta   拼接 assistant 正文
state.snapshot  同步最终状态
tool.delta      展示工具调用
run.error       标记本次运行失败
run.finished    标记本次运行完成
```

如果不提前固定这些事件名，前端就会被迫理解 LangGraph 的内部投影，学习和维护都会变乱。

## 输入长什么样

模块五接收模块四产出的 `AgentEvent`：

```json
{
  "event": "message.delta",
  "data": {
    "message_id": "run_test:assistant",
    "role": "assistant",
    "delta": "第一段回答",
    "index": 0
  }
}
```

如果 agent 结束，会收到：

```json
{
  "event": "run.finished",
  "data": {
    "thread_id": "thread_test",
    "run_id": "run_test"
  }
}
```

## 输出长什么样

模块五先把 `AgentEvent` 转成 `BusinessSseEvent`：

```json
{
  "event": "message.delta",
  "event_id": null,
  "data": {
    "message_id": "run_test:assistant",
    "delta": "第一段回答"
  }
}
```

最后编码成 SSE 文本：

```txt
event: message.delta
data: {"message_id":"run_test:assistant","delta":"第一段回答"}

```

注意最后有一个空行。SSE 用这个空行表示“一帧结束”。

## 异常怎么处理

如果模块四的 agent adapter 在流式过程中抛异常，模块五不会让异常直接冲破响应边界。
它会产出一条 `run.error`：

```json
{
  "event": "run.error",
  "data": {
    "name": "RuntimeError",
    "message": "agent adapter crashed"
  }
}
```

这样前端不需要猜连接为什么断了，它能明确知道本次 run 失败。

## 主要代码

文件：

```txt
backend/app/chat/sse.py
```

核心函数：

```txt
agent_event_to_sse_event
make_error_event
encode_sse_event
iter_business_events
iter_sse_frames
```

模块六的 FastAPI 路由会使用最末端的接口：

```py
async for frame in iter_sse_frames(agent_events):
    yield frame
```

## 测试怎么读

测试文件：

```txt
backend/tests/test_sse.py
```

重点看四类测试：

```txt
1. AgentEvent 是否能保留事件名和 data
2. encode_sse_event 是否生成标准 SSE 文本
3. 上游异常是否变成 run.error
4. 模块四的静态 adapter 是否能直接接到 SSE 编码器
```

这一步验证的是“浏览器将来会收到什么”。它还不关心 HTTP 路由，也不关心真实模型。
