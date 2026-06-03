# SlotFlow 重写边界

SlotFlow 是一个学习版重写项目：保留 DeerFlow 后端 harness 的核心能力，
但在第一阶段删掉旧产品边界和 LangGraph Platform 兼容协议。

这份文档不是装饰性的 README。以后每次要不要加一层、要不要保留某个旧接口、
要不要一次性做完一大块，都先回到这里判断。

## 学习目标

SlotFlow 不只是一个更小的实现，它也是一条可跟着走的全栈学习路线。

每个模块都要帮助我们看清这条主线：

```txt
前端输入
-> 后端 API
-> run 配置
-> harness / agent
-> SSE 事件
-> 前端流式状态
-> UI 渲染
```

每做一个模块，都必须先回答四个问题：

```txt
这个模块解决什么问题？
它接收什么输入？
它输出什么数据？
它在「前端 -> 后端 -> agent -> SSE -> 前端」这条链路里的位置是什么？
```

## 固定学习工作流

从现在开始，SlotFlow 不采用“一口气生成所有代码”的方式推进。

原因很直接：一次性生成完，文件会很多，但你很难知道每一层为什么存在、
数据在哪里变了形、测试到底保护了什么。这个项目更适合按小模块推进：
每一步都要能解释、能测试、能回退。

进入任何新区域前，先写架构，再写计划，最后才写代码。

先写架构时说明：

```txt
1. 这一块的数据怎么流动
2. 要拆成哪些模块
3. 每个模块负责什么
4. 输入和输出的边界是什么
5. 哪些测试能证明这个边界真的工作
```

再写计划时按这个节奏推进：

```txt
1. 只实现一个小模块
2. 给这个模块加聚焦测试
3. 先跑这个模块自己的窄测试
4. 再跑后端整体测试
5. 在 docs/ 下写这个模块的说明文档
6. 解释代码和测试结果后，再进入下一个模块
```

如果某一步变得太大，先拆边界，不要硬写完一整片代码。

每个模块都要在 `docs/` 下留下单独说明文档，例如：

```txt
docs/module-01-domain-models.md
docs/module-02-in-memory-repository.md
```

模块文档要用自然中文写清楚：它解决什么问题、输入是什么、输出是什么、
位于完整链路的哪个位置、测试文件应该怎么看。

## 项目细分

整个项目先拆成四个学习区域。

后端应用边界：

```txt
FastAPI 应用入口
请求 / 响应模型
thread / message / run 仓库
run 配置构建器
agent 适配层
SSE 事件契约
错误和取消处理
```

agent 运行时边界：

```txt
LangGraph v3 event streaming 适配层
真实 harness agent 构建器
checkpointer 接线
tool / middleware 功能开关
状态快照映射
```

前端边界：

```txt
带类型的 API client
SSE 解析器
聊天流 hook
消息状态 reducer
shadcn/ui 聊天界面
学习阶段用的 state / event 调试面板
```

验证边界：

```txt
纯函数单元测试
仓库持久化规则测试
流式事件到 SSE 的映射测试
FastAPI TestClient HTTP 链路测试
前端类型检查和生产构建
后续用浏览器验证真实 UI
```

## 分步计划

开发按可验证的小模块推进：

```txt
0. 后端健康检查 / API 骨架
1. 领域模型和可读 ID
2. 内存版 thread / message / run 仓库
3. run 配置构建器：thread_id / configurable / context
4. LangGraph v3 event streaming 适配层：typed projections -> AgentEvent
5. 业务 SSE 编码器：AgentEvent -> SSE frame
6. FastAPI 聊天路由
7. 用 TestClient 模拟完整后端链路
8. DeepSeek / LangGraph live smoke test
9. SlotFlow 本地 runtime / harness 装配层 + checkpointer
10. 前端 SSE 解析器
11. 前端 use-chat-stream hook
12. 聊天 UI 展示 message / tool / state
13. 让真实 agent 流式输出跑进 UI
```

`make verify` 是项目健康闸门，不是聊天功能本身。

它当前会检查：

```txt
后端 pytest
前端 typecheck
前端 production build
```

随着模块增加，`make verify` 会逐步覆盖更真实的聊天链路。

## 保留

这些属于 DeerFlow 的核心能力，不应该在第一阶段随手删掉：

```txt
harness agents
harness tools
harness middlewares
模型配置和模型创建
thread state
checkpointer 支持
memory 文件
agent factory / 功能开关
```

`factory.py` 和 `features.py` 不是测试文件，而是运行时装配入口。
它们重要的原因是：砍掉 LangGraph Platform 兼容层以后，新后端必须自己显式传入
checkpointer 来创建 agent graph。

## 重写

这些边界可以重写：

```txt
gateway API
run / stream 编排
SSE 事件契约
前端状态 hook
前端聊天 UI
```

第一阶段的后端流应该直接一些。当前已经验证本地依赖支持
`stream_events(..., version="v3")`：

```txt
HTTP endpoint
-> graph.astream_events(..., version="v3")
-> 优先 typed projections
-> 必要时 raw event method / params.data fallback
-> AgentEvent
-> 业务 SSE 事件
```

这条路径用于先跑通学习链路。只有在明确验证某个真实 harness agent 不支持 v3
typed projections 时，才回退到较底层的 `astream(stream_mode=[...])`，并且要在模块文档
里写清楚具体原因。

不要重新做旧项目这一串中转：

```txt
RunManager -> StreamBridge -> sse_consumer -> LangGraph SDK useStream
```

## 第一批测试要保护什么

测试不是为了“显得专业”，而是为了固定最容易混乱的边界。

第一批测试重点保护这些不变量：

```txt
thread_id 放进 config["configurable"]
创建 graph 时显式传入 checkpointer
messages chunk 变成 message.delta SSE 事件
values chunk 变成 state.snapshot SSE 事件
stream 出错时最终产生 run.error
thread messages 能被保存和再次读取
FastAPI stream 成功后 run 变 completed
FastAPI stream 失败后 run 变 failed
```

旧项目的 `backend/tests` 只作为参考材料，不整目录复制。

## 出错时的处理原则

出错后先问：

```txt
是不是模块边界设计太复杂？
是不是可以删掉一层，用更直接的数据流？
是不是测试目标太大，应该先拆成更小的可验证点？
```

优先选择更简单、更直接的实现。不要为了绕过一个错误马上塞入兼容层、
全局状态、特殊分支或旧协议适配。
