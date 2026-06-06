# Codex Learning Memory

这份文档用于在新对话里快速恢复 SlotFlow 的学习协作上下文。它放在仓库根目录，只服务于本项目。

新线程开始时，先读：

```txt
docs/rewrite-boundary.md
codex-learning-memory.md
```

## 协作规则

- 默认用中文解释。
- 这个项目的主要目标是学习全栈开发，不是让 Codex 一次性代写完整项目。
- 这个项目随着对话进行更新 修改 增加
- 每个数据结构都要解释内容是什么 复杂一点的要模拟内容/结构的输出 直观解释
- 遇到事件名、类型名、字段名、协议形状时，要明确说明它是 SlotFlow 项目自定义的，
  还是 LangGraph/LangChain/FastAPI/Pydantic 等框架规定的。
- 涉及 LangGraph/LangChain 的最新 API、推荐用法、参数含义或版本变化时，优先通过
  MCP 或联网查看官方文档/一手资料；本地安装环境只用于验证当前项目依赖的实际行为。
  回答时要明确区分“官方最新推荐”和“当前本地依赖实测”。
- 除非用户明确要求“实现 / 修改 / 写代码”，否则 Codex 主要负责讲解架构、边界、数据流、测试目的和语法要点。
- 解释时优先结合当前仓库文件，不泛泛讲概念。
- 进入新模块时先回答四个问题：

```txt
这个模块解决什么问题？
它接收什么输入？
它输出什么数据？
它在「前端 -> 后端 -> agent -> SSE -> 前端」链路里的位置是什么？
```

- 学习节奏是：先讲架构，再讲模块边界，再讲数据流，再讲测试保护什么，最后看代码细节。
- 每个模块要小、可解释、可测试。不要一次性生成大量文件。

## 项目主线

SlotFlow 是学习版重写项目，用更小、更直接的结构重写 DeerFlow 的核心聊天链路。

核心链路：

```txt
前端输入
-> 后端 API
-> run 配置
-> harness / agent
-> SSE 事件
-> 前端流式状态
-> UI 渲染
```

当前学习重点不是“功能堆满”，而是看清每一层为什么存在、数据在哪里变形、测试保护什么边界。

## 模块 1：领域模型和可读 ID

相关文件：

```txt
backend/app/chat/models.py
backend/app/chat/ids.py
backend/tests/test_chat_models.py
docs/module-01-domain-models.md
```

核心对象：

```txt
ThreadRecord       一条会话
MessageRecord      会话里保存的一条消息
RunRecord          一次 assistant 执行记录
ChatStreamRequest  前端点击发送时的请求体
RunContext         当前 run 的业务运行上下文
RunConfigBundle    调 agent 时需要的 config + context
```

ID 含义：

```txt
thread_id  哪条会话
message_id 哪条消息
run_id     哪一次 assistant 执行
```

`RunContext` 不保存完整 `messages`。它描述的是这次 run 的运行开关和定位信息，例如 `thread_id`、`run_id`、`model_name`、`mode`、`agent_name`、`files`、`thinking_enabled`、`is_plan_mode`、`subagent_enabled`。

`ChatStreamRequest` 和 `MessageRecord` 的关系：

```txt
ChatStreamRequest = 前端发来的本次发送请求
MessageRecord     = 后端保存下来的消息记录

ChatStreamRequest.message -> MessageRecord.content
ChatStreamRequest.metadata -> MessageRecord.metadata
```

已解释语法：

- `re.fullmatch(r"thread_[0-9a-f]{12}", thread_id)`：整个字符串必须匹配 `thread_` 加 12 位小写十六进制字符。
- `[0-9a-f]{12}`：每一位是 `0-9` 或 `a-f`，一共重复 12 次。
- `pytest.raises(...)`：测试非法输入是否按预期抛异常。
- `Field(min_length=1)` 只能挡住空字符串，`field_validator + strip()` 用来挡住全空白字符串。

## 模块 2：内存版 thread/message/run 仓库

相关文件：

```txt
backend/app/chat/repository.py
backend/tests/test_chat_repository.py
docs/module-02-in-memory-repository.md
```

核心理解：

```txt
InMemoryChatRepository = 一个小型内存数据库
ThreadRecord           = 仓库里的一条会话记录
MessageRecord          = 某个 thread 下的一条消息
RunRecord              = 某个 thread 下的一次执行
```

`InMemoryChatRepository` 不是每个 user / thread 创建一个。它是一个仓库服务，内部统一保存多条 thread、每个 thread 下的 messages、每个 thread 下的 runs。

当前还没有 `user_id`，这是学习阶段的单用户简化。以后加多用户时，核心是加 `user_id` 归属和权限检查，优先在 repository/API 边界处理。

核心规则：

```txt
thread 是会话容器
message 必须属于 thread
run 必须属于 thread
message 按写入顺序读取
run 可以更新 queued/running/completed/failed/cancelled
message/run 变化会更新 thread.updated_at
仓库返回副本，避免外部修改内部状态
```

`model_copy(deep=True)` 是 Pydantic `BaseModel` 提供的方法，用来返回深拷贝。它不是项目里自己定义的函数。

## 模块 3：run 配置构建器

相关文件：

```txt
backend/app/chat/run_config.py
backend/tests/test_run_config.py
docs/module-03-run-config.md
```

模块 3 的职责是：把一次聊天请求整理成 agent 能消费的 `config + context`。

核心区分：

```txt
config  -> 给 LangGraph / checkpointer / agent runtime 用
context -> 给 SlotFlow 自己的业务逻辑用
```

字段放置规则：

```txt
thread_id -> config["configurable"]["thread_id"]

run_id
model_name
mode
agent_name
files
thinking_enabled
is_plan_mode
subagent_enabled
-> context
```

`thread_id` 放进 `config["configurable"]` 很重要。后面接真实 LangGraph checkpointer 时，多轮记忆会靠这个位置找到同一条会话。

`flash/pro/ultra` 当前映射：

```txt
flash:
  thinking_enabled = False
  is_plan_mode = False
  subagent_enabled = False

pro:
  thinking_enabled = True
  is_plan_mode = True
  subagent_enabled = False

ultra:
  thinking_enabled = True
  is_plan_mode = True
  subagent_enabled = True
```

已解释语法：

- 函数参数里的单独 `*` 表示后面的参数必须使用关键字传参。
- `@pytest.mark.parametrize(...)` 用一份测试逻辑跑多组输入输出。
- `files=list(request.files)` 是复制列表，避免请求对象后续变化影响已经构建好的 `RunContext`。

## 模块 4：Agent 事件适配层

当前新的模块 4 是 `Agent 事件适配层`，相关文件：

```txt
backend/app/chat/agent_adapter.py
backend/tests/test_agent_adapter.py
docs/module-04-agent-event-adapter.md
```

`AgentEventName` 里的事件名是 SlotFlow 项目自定义的业务事件名，不是 LangGraph/LangChain
官方直接规定的名字。

当前 SlotFlow 业务事件名：

```txt
run.prepared
message.delta
tool.delta
state.snapshot
run.finished
```

它们的来源关系：

```txt
LangGraph/LangChain 原始事件或 projection
例如 messages / values / tool_calls
-> backend/app/chat/agent_adapter.py
-> SlotFlow AgentEventName
```

也就是说：

```txt
messages   -> message.delta
values     -> state.snapshot
tool_calls -> tool.delta
```

`run.prepared` 和 `run.finished` 更明显是 SlotFlow 自己补出来的生命周期事件，用来告诉
上层“一次 run 已准备好 / 已正常结束”。

`AgentEvent.data` 是当前事件携带的业务数据。不同 `event` 对应不同 data 结构，例如：

```txt
run.prepared:
  thread_id
  run_id
  model_name
  mode
  agent_name

message.delta:
  message_id
  role
  delta
  index

state.snapshot:
  thread_id
  run_id
  messages
  state

tool.delta:
  工具调用相关字段，例如 name / args

run.finished:
  thread_id
  run_id
```

`data: dict[str, Any] = Field(default_factory=dict)` 的含义：

```txt
data 是一个字典
key 是字符串
value 可以是任意 JSON-ish 数据
默认值是每个 AgentEvent 独立的新空 dict
```

这里用 `default_factory=dict`，不是 `data: dict = {}`，是为了避免多个事件共享同一个
可变字典。

`state.snapshot` 里的 `state` 不等于 `RunContext`。区别：

```txt
RunContext
  SlotFlow 在调用 agent 前准备好的运行时业务上下文
  例如 thread_id/run_id/model_name/mode/files/feature flags

state
  agent / graph 在运行过程中产出的状态快照
  例如 messages、next、工具状态、模型运行后的值
```

在 `state.snapshot` 事件里，`thread_id` 和 `run_id` 放在 `data` 顶层，是事件归属信息；
`state` 放 agent 状态本身。不要默认认为 `state` 就是完整 context。

LangGraph 里的 `state` 也不应该理解成“由 checkpointer 生成”。更准确的关系是：

```txt
graph state
  由图的 state schema 定义
  由节点执行返回的更新和 reducer 合并产生

checkpointer
  在 graph 执行的步骤中保存 state 快照
  下次用相同 thread_id 调用时可以取回/恢复之前的 state
  负责持久化和恢复，不负责创造业务 state
```

所以：

```txt
node/reducer 生成和更新 state
checkpointer 保存和恢复 state snapshot
thread_id 帮 checkpointer 找到属于哪条会话的 checkpoint 历史
```

LangGraph 里 `StateGraph(State)` 的 `State` 不是 `context`。它是 graph state schema。
调用 graph/agent 时，通常有三条不同输入通道：

```txt
input/state update
  例如 {"messages": [{"role": "user", "content": "..."}]}
  会进入 graph state

config
  例如 {"configurable": {"thread_id": "thread_1"}}
  给 LangGraph runtime/checkpointer 用

context
  例如 Context(user_id="user_1")
  给本次运行的节点读取，用于业务上下文；不会自动变成 state，除非节点显式写入 state
```

来源归属：

```txt
State schema / Node / Reducer / Checkpointer
  是 LangGraph 的核心概念

State 里的具体字段，例如 messages / foo / bar
  是应用自己定义的，或者使用 LangGraph 预置的 MessagesState

SlotFlow 的 RunContext / AgentEventName
  是本项目自定义的业务边界
```

不要把 LangGraph 的 `State` schema 和 SlotFlow 的 `state.snapshot.data["state"]` 完全画等号。

```txt
class State(TypedDict):
    messages: list
```

这类 `State` 是 graph 的状态结构定义，通常在创建 graph 时提供。

```txt
AgentEvent(event="state.snapshot", data={"state": ...})
```

这里的 `data["state"]` 是 SlotFlow 事件里对外暴露的“状态快照字段”。在
`StaticProjectionAgentAdapter` 里它是手动模拟出来的学习数据；在
`LangGraphEventAgentAdapter` 里它来自真实 LangGraph `values` 事件，经
`normalize_values_snapshot/to_jsonable` 归一化后放进 `state.snapshot`。

当前 SlotFlow 的真实 adapter 确实基于 LangChain：

```txt
langchain.agents.create_agent(...)
-> graph.astream_events(..., version="v3")
-> LangGraphEventAgentAdapter
-> AgentEvent
```

但默认开发和测试可以使用 `StaticProjectionAgentAdapter`，它不调用真实模型，只模拟同样的
SlotFlow 业务事件序列。

State schema 不是每次调用 graph/agent 时和 `config/context` 一起传入的。更准确是：

```txt
构建 graph/agent 时：
  定义 state schema
  例如 StateGraph(State) 或 create_agent(..., state_schema=CustomState)

每次调用 graph/agent 时：
  传入本次 input/state update
  例如 {"messages": [{"role": "user", "content": "..."}]}

同时可传：
  config  -> runtime/checkpointer 配置，例如 thread_id
  context -> 本次运行的静态业务上下文
```

在当前 SlotFlow 的 `LangGraphEventAgentAdapter` 中，真实调用是：

```txt
build_agent_input(request) -> {"messages": [{"role": "user", "content": request.message}]}
graph.astream_events(input, config=bundle.config, version="v3")
```

目前没有自定义 `state_schema`，使用 `create_agent` 的默认 agent state；以后如果要给真实
agent state 增加字段，再在创建 agent/graph 时显式传 `state_schema`。

关于 LangGraph/LangChain runtime context：

```txt
context 不应该随便塞进 config["configurable"]
config["configurable"] 主要给 runtime/checkpointer 定位和配置用，例如 thread_id
```

当前本地包里 `CompiledStateGraph.astream_events` 的签名没有显式 `context=`，但有
`**kwargs`；实测可以传：

```py
graph.astream_events(
    input,
    config=bundle.config,
    context=some_context,
    version="v3",
)
```

前提是创建 agent/graph 时声明了对应 schema，例如：

```py
create_agent(..., context_schema=Context)
```

否则节点也不知道该按什么结构读取 context。当前 SlotFlow 代码暂时没有把
`bundle.context` 传进真实 graph；它主要被 adapter 外层用来补 `run.prepared`、
`message_id`、`state.snapshot`、`run.finished`。如果后续真实 agent 节点需要读取
`mode/files/feature flags`，应当新增明确的 `context_schema` 和测试，而不是把所有业务
字段混进 `configurable`。

LangGraph v3 event streaming 官方提供 typed projections，例如：

```txt
stream.messages
stream.values
stream.tool_calls
stream.output
stream.subgraphs
```

它们的目的就是让应用代码不用直接解析 raw protocol event 里的
`method/params/data` 嵌套字典。后续真实 adapter 应优先考虑消费 projections，而不是
直接遍历 raw stream。只有在需要严格到达顺序、某个 projection 不覆盖需求、或当前异步
API 暂时不支持目标组合时，才直接消费 raw protocol event。

但即使使用 projections，SlotFlow 仍然需要自己的 `AgentEvent` 业务边界：

```txt
LangGraph projection object
-> SlotFlow AgentEvent
-> BusinessSseEvent
-> SSE frame
```

也就是说，需要的是“业务事件适配层”，不一定需要维持当前这种 raw-dict 风格的
`projection_item_to_agent_event` 实现。

## 旧模块 4：fake agent stream

相关文件：

```txt
backend/app/chat/fake_agent.py
backend/tests/test_fake_agent.py
docs/module-04-fake-agent-stream.md
```

模块 4 的职责是：先不接真实 LLM，也不接 DeerFlow harness，而是用 fake agent 稳定模拟 agent 的异步流式输出。

它在链路里的位置：

```txt
前端输入
-> 后端 API
-> 领域模型
-> 内存仓库
-> run 配置
-> fake agent stream
-> SSE 事件
-> 前端状态
-> UI 渲染
```

`FakeAgent.astream(...)` 接收：

```txt
request: ChatStreamRequest
bundle: RunConfigBundle
```

也就是同时拿到用户这次发了什么，以及模块 3 构建出来的 `config + context`。

fake agent 输出的是异步流，每一项是：

```txt
(mode, chunk)
```

当前三种 mode：

```txt
custom   运行准备事件，例如 run.prepared
messages assistant 文本片段
values   最终状态快照
```

`run.prepared` 是流式事件类型，不是 `RunRecord.status`。区别：

```txt
RunRecord.status = 持久化状态
chunk["type"]    = 流里发生的具体事件名
```

`run_id` 的作用：

```txt
把一批流式 chunk 归到同一次执行
让错误能绑定到具体 run
让取消/重试知道操作哪一次 run
让 assistant 最终消息能关联到产生它的 run
方便日志、调试和状态追踪
```

已解释异步流语法：

- `agent.astream(...)` 调用一次，得到一个异步生成器。
- `async for` 会不断向异步生成器要下一项。
- 每次取下一项时，函数会从上一个 `yield` 后面继续执行。
- `modes[-1]` 表示列表最后一个元素，不是第几次调用函数。

真实 LangChain / LangGraph 对照：

```txt
invoke  = 一次性拿最终结果
stream  = 同步流式输出
astream = 异步流式输出
```

无 checkpointer 时，通常需要把完整 `messages` 传给 agent。有 checkpointer 时，`config["configurable"]["thread_id"]` 可以帮助恢复同一条会话的历史状态。

真实 agent 常见输入包括：

```txt
messages
config
context
stream_mode
```

真实 stream mode 常见有：

```txt
messages
values
updates
custom
```

SlotFlow 当前 fake stream 是教学版形状，不追求完全复刻 LangGraph 内部类型，而是先固定后续 SSE 映射边界。

## 测试命令

## 模块 8：前端流式验证页

相关文件：

```txt
frontend/src/lib/chat-stream.ts
frontend/src/app/page.tsx
frontend/next.config.ts
docs/module-08-frontend-stream-smoke.md
```

当前模块 8 先做最小浏览器闭环，不做正式前端架构和最终视觉设计。

核心链路：

```txt
Next 页面
-> createThread()
-> POST /api/chat/threads/{thread_id}/runs/stream
-> 读取 ReadableStream
-> 按 SSE frame 解析 event/data
-> message.delta 更新 assistant 文本
-> state.snapshot 校准最终 assistant 文本
-> 右侧显示最近业务事件日志
```

`next.config.ts` 现在把本地前端请求代理到 FastAPI：

```txt
/api/:path* -> http://localhost:8000/api/:path*
/health     -> http://localhost:8000/health
```

这样第一版页面只调用相对路径，不需要先处理浏览器跨端口 CORS。

页面默认发送 `model_name="deepseek-v4-flash"`，不要用 `fake-model`。原因是 static runtime
不会真正调用模型，DeepSeek runtime 则需要真实支持的模型名；这个默认值可以同时覆盖两种
验证模式。

验证方式：

```bash
cd /home/dell/code/SlotFlow/backend
uv run uvicorn app.main:app --reload --port 8000

cd /home/dell/code/SlotFlow/frontend
pnpm dev
```

打开 `http://localhost:3000`，点击 Send 后应看到左侧 assistant 流式文本，右侧出现
`run.prepared / message.delta / state.snapshot / run.finished`。

下一步如果继续前端，应把模块 8 的临时代码拆成：

```txt
正式 SSE parser 测试
useChatStream hook
消息 reducer
正式聊天 UI
```

## 模块 9：前端 SSE parser

相关文件：

```txt
frontend/src/lib/sse-parser.ts
frontend/src/lib/chat-stream.ts
docs/module-09-frontend-sse-parser.md
```

模块 9 把模块 8 里的 SSE 文本解析抽成纯函数层：

```txt
SSE 文本 buffer
-> drainSseBuffer()
-> parseSseFrame()
-> ChatStreamEvent[]
```

边界现在是：

```txt
chat-stream.ts = I/O 层，负责 fetch / ReadableStream / TextDecoder
sse-parser.ts = 纯解析层，负责 event/data frame -> ChatStreamEvent
page.tsx       = 临时 UI 层，负责展示 messages 和 Event Log
```

`ReadableStream` 的 chunk 不保证刚好等于一条 SSE 事件，所以 parser 会把未结束的半条
frame 留在 `rest`，等下一次 chunk 继续拼。后续做 `useChatStream` hook 时，应复用
`drainSseBuffer()`，不要在 hook 里重新手写 SSE frame 解析。

## 模块 10：SlotFlow harness builder 骨架

相关文件：

```txt
backend/app/harness/__init__.py
backend/app/harness/builder.py
backend/app/harness/config.py
backend/app/harness/features.py
backend/app/harness/state.py
backend/tests/test_harness_builder.py
docs/module-10-slotflow-harness-builder.md
```

模块 10 把真实 LangGraph agent graph 的组装边界从 `chat/runtime.py` 迁到 `app/harness/`。
`runtime.py` 现在只负责选择运行模式、模型和 checkpointer，然后委托：

```txt
create_langgraph_agent_graph()
-> build_slotflow_harness_graph()
-> langchain.agents.create_agent(...)
```

新的依赖方向必须保持：

```txt
chat.routes -> chat.runtime -> harness.builder -> LangGraph create_agent
```

不要让 `harness` 反向依赖 FastAPI route、ChatRepository、SSE encoder 或 frontend。
后续 tools / skills / MCP / middleware 都应该进入 `app/harness/`，而不是塞回
`chat/runtime.py`。

## 模块 11：Harness 安全内置工具

相关文件：

```txt
backend/app/harness/tools/__init__.py
backend/app/harness/tools/builtins.py
backend/app/harness/tools/registry.py
backend/tests/test_harness_tools.py
docs/module-11-harness-tools.md
```

模块 11 加入第一批安全内置工具：

```txt
slotflow_context
```

这个工具只返回 thread_id / run_id / mode / source 的 JSON 摘要，不读文件、不写文件、
不访问网络、不执行 shell、不依赖 sandbox。它的目标是证明 harness tool calling 链路，
不是提供复杂业务能力。

`build_harness_tools(features=..., extra_tools=...)` 是后续 builtin tools、MCP tools、
subagent tools、skills allowed-tools 策略的统一入口。当前 registry 会按 tool.name 去重，
保留更早出现的工具。

重要边界：真实 DeepSeek/OpenAI chat model 支持 `bind_tools()`，但 LangChain 的部分 fake
model 不支持。`harness.builder` 会在模型没有 tool binding 能力时跳过 tools，避免普通 fake
model 测试失败；tool calling 的测试使用专门支持 `bind_tools()` 的 fake model。

## 模块 12：Harness 只读 skills registry

相关文件：

```txt
backend/app/harness/skills/__init__.py
backend/app/harness/skills/types.py
backend/app/harness/skills/parser.py
backend/app/harness/skills/registry.py
backend/tests/test_harness_skills.py
docs/module-12-harness-skills.md
```

模块 12 加入只读 skills registry：

```txt
SKILL.md
-> parse_skill_file()
-> load_enabled_skills()
-> build_skills_prompt()
-> harness system prompt
```

skills 不是工具本身，也不是 sandbox 执行器。当前语义是“能力说明书 + 工具策略提示”。
`allowed-tools` 的三种语义必须保留：

```txt
字段省略 -> inherit
[]       -> none
[a, b]   -> 只允许这些工具
```

当前只把 allowed-tools 写入 prompt，不真正过滤 tool registry。`SLOTFLOW_SKILLS_ROOT`
和 `SLOTFLOW_ENABLED_SKILLS` 由 `chat.runtime` 读取，但 skill 内容扫描和 prompt 构建属于
`app/harness/skills`。

## 模块 13：Harness MCP tools 边界

相关文件：

```txt
backend/app/harness/mcp/__init__.py
backend/app/harness/mcp/config.py
backend/app/harness/mcp/loader.py
backend/app/harness/config.py
backend/app/harness/tools/registry.py
backend/app/harness/builder.py
backend/app/chat/runtime.py
backend/tests/test_harness_mcp.py
docs/module-13-harness-mcp.md
```

模块 13 只落 MCP tools 的入口边界，不连接真实 MCP server：

```txt
SLOTFLOW_MCP_ENABLED / SLOTFLOW_MCP_SERVERS
-> SlotFlowMcpConfig
-> McpToolProvider
-> load_mcp_tools()
-> build_harness_tools()
-> LangGraph create_agent(tools=...)
```

当前默认 provider 是 `EmptyMcpToolProvider`，不会启动进程、不会访问网络、不会连接 MCP。
真实 `MultiServerMCPClient` 以后只需要实现 `McpToolProvider`，再传给 tools registry。

重要边界：

```txt
chat.runtime 只读取 MCP 配置
harness.tools.registry 才加载 MCP tools
harness.builder 负责把 mcp_config / mcp_tool_provider 传下去
FastAPI route / SSE 层不关心 MCP
```

测试保护：

```txt
disabled 时不调用 provider
只把 enabled servers 传给 provider
MCP tools 会接到 slotflow_context 后面
runtime env 会生成 SlotFlowMcpConfig
builder 会把 MCP 配置传进 tools registry
```

模块 13 暂不做真实 stdio/HTTP MCP 连接、复杂 JSON server 配置、工具权限过滤，也不执行
skills allowed-tools 策略。

## 模块 14：Harness middleware registry

相关文件：

```txt
backend/app/harness/middleware/__init__.py
backend/app/harness/middleware/config.py
backend/app/harness/middleware/builtins.py
backend/app/harness/middleware/registry.py
backend/app/harness/config.py
backend/app/harness/builder.py
backend/app/chat/runtime.py
backend/tests/test_harness_middleware.py
docs/module-14-harness-middleware.md
```

模块 14 只落 LangChain agent middleware 的 SlotFlow 本地入口，不搬 DeerFlow 旧 middleware。

当前第一颗内置 middleware：

```txt
SlotFlowRuntimeSummaryMiddleware
```

它只在 `before_agent` 阶段把 `runtime.context` 和 feature flags 摘要写进 graph state：

```txt
state["slotflow"]["runtime"]
```

它不改消息、不拦截模型、不拦截工具。当前作用是证明 middleware registry、开关和真实
LangGraph graph 执行链路已经跑通。

重要边界：

```txt
FastAPI middleware 处理 HTTP 请求
LangChain AgentMiddleware 处理 agent graph 内部执行
这两个不是一类东西
```

runtime 读取：

```txt
SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE=false
```

默认启用。`chat.runtime` 只解析成 `SlotFlowMiddlewareConfig`，不直接创建 middleware。
真正组装发生在：

```txt
build_harness_middleware()
-> build_slotflow_harness_graph()
-> create_agent(middleware=...)
```

测试保护：

```txt
runtime summary 会保留原有 slotflow state
默认加入 SlotFlowRuntimeSummaryMiddleware
config 可以关闭内置 middleware
按 middleware.name 去重，保留更早实例
真实 LangGraph fake graph 会执行 before_agent 并返回 slotflow.runtime
```

模块 14 暂不做 uploads、sandbox、memory、title、tool error handling、dangling tool call、
wrap_model_call 或 wrap_tool_call。这些后续按小模块逐个加。

后端测试：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q
```

模块 4 窄测试：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_fake_agent.py
```

整体验证：

```bash
cd /home/dell/code/SlotFlow
make verify
```

## 新线程恢复建议

如果用户打开新线程，先阅读：

```txt
docs/rewrite-boundary.md
codex-learning-memory.md
```

然后根据用户当前打开的文件，从对应模块继续讲。不要默认写代码，除非用户明确要求实现或修改。
