# 模块 06：FastAPI 聊天路由

模块六第一次把后端前五个模块真正串起来。

前面每一步都很小：

```txt
模块 01：定义 thread / message / run 长什么样
模块 02：用内存仓库保存它们
模块 03：把请求整理成 config + context
模块 04：把 agent v3 投影整理成 AgentEvent
模块 05：把 AgentEvent 编码成 SSE
```

模块六把它们接成 HTTP 接口：

```txt
前端 HTTP 请求
-> FastAPI 路由
-> 仓库保存用户消息和 run
-> build_run_config
-> AgentAdapter.stream_events
-> iter_business_events
-> encode_sse_event
-> StreamingResponse
-> 仓库保存 assistant 最终消息和 run 状态
```

## 这一层解决什么问题

模块六回答的是：“前端真的发请求过来以后，后端怎么把一次聊天跑完？”

它不负责生成回答。回答来自模块四的 agent adapter。

它不负责手写 SSE 格式。SSE 编码来自模块五。

它主要负责编排顺序：

```txt
1. thread 是否存在
2. 保存用户消息
3. 创建 run
4. 把 run 标记为 running
5. 调用 agent adapter
6. 一边流式返回 SSE，一边收集 assistant 文本
7. 正常结束时保存 assistant 消息，把 run 标记为 completed
8. 出错时发 run.error，把 run 标记为 failed
```

## 当前接口

创建 thread：

```http
POST /api/chat/threads
Content-Type: application/json

{
  "title": "学习会话"
}
```

响应：

```json
{
  "id": "thread_xxx",
  "title": "学习会话",
  "created_at": "...",
  "updated_at": "..."
}
```

列出 thread：

```http
GET /api/chat/threads
```

读取单个 thread：

```http
GET /api/chat/threads/{thread_id}
```

读取某个 thread 下的消息：

```http
GET /api/chat/threads/{thread_id}/messages
```

启动一次流式 run：

```http
POST /api/chat/threads/{thread_id}/runs/stream
Content-Type: application/json

{
  "message": "解释完整链路",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "agent_name": "default",
  "files": ["upload_1"],
  "metadata": {}
}
```

响应是 SSE：

```txt
event: run.prepared
data: {"thread_id":"thread_xxx","run_id":"run_xxx","model_name":"deepseek-v4-flash","mode":"pro","agent_name":"default"}

event: message.delta
data: {"message_id":"run_xxx:assistant","role":"assistant","delta":"第一段","index":0}

event: state.snapshot
data: {"thread_id":"thread_xxx","run_id":"run_xxx","messages":[...],"state":{...}}

event: run.finished
data: {"thread_id":"thread_xxx","run_id":"run_xxx"}

```

如果 agent 出错，会返回：

```txt
event: run.error
data: {"name":"RuntimeError","message":"boom from test adapter"}

```

## 数据怎么变化

假设前端发送：

```json
{
  "message": "解释完整链路",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "files": ["upload_1"]
}
```

进入路由后，仓库先保存 user message：

```json
{
  "role": "user",
  "content": "解释完整链路",
  "metadata": {
    "files": ["upload_1"],
    "request_metadata": {}
  }
}
```

然后创建 run：

```json
{
  "id": "run_xxx",
  "thread_id": "thread_xxx",
  "status": "queued",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "agent_name": "default"
}
```

调用 agent 前，run 被更新成：

```json
{
  "status": "running"
}
```

流式过程中，前端不断收到 `message.delta`。

当收到 `run.finished` 时，路由会把 assistant 最终正文保存成 message：

```json
{
  "role": "assistant",
  "content": "SlotFlow 收到：解释完整链路...",
  "run_id": "run_xxx",
  "metadata": {
    "source": "agent"
  }
}
```

最后 run 变成：

```json
{
  "status": "completed"
}
```

## 主要代码

文件：

```txt
backend/app/chat/routes.py
backend/app/main.py
```

`routes.py` 负责聊天接口。

`main.py` 负责创建 FastAPI app，并把两个对象放进 `app.state`：

```txt
chat_repo
agent_adapter
```

这样测试可以注入静态 adapter，真实 smoke test 可以注入 DeepSeek adapter。

## 测试怎么读

测试文件：

```txt
backend/tests/test_chat_routes.py
```

重点看三类测试：

```txt
1. thread 基础接口：创建、列出、读取、列出消息
2. stream 成功链路：SSE 事件返回，用户消息和 assistant 消息落库，run 变 completed
3. stream 失败链路：返回 run.error，run 变 failed，不保存 assistant 消息
```

这组测试已经是完整后端链路测试，但仍然不调用真实模型。

真实 DeepSeek 调用放在单独 smoke test 里，是因为它依赖网络、API key、余额和模型服务状态，
不适合作为 `make verify` 的常规闸门。
