# 模块 19：SQLite / Postgres LangGraph checkpointer

模块 19 把 SlotFlow runtime 的 checkpointer 从：

```txt
none / memory
```

扩展成：

```txt
none / memory / sqlite / postgres
```

这里保存的是 LangGraph graph 内部状态，不是模块 18 的业务会话数据。

## 这一层解决什么问题

模块 18 已经能把业务数据落盘：

```txt
thread
message
run
```

但 LangGraph 多轮状态不是直接从这些表恢复的。LangGraph 依赖 checkpointer，根据
`config["configurable"]["thread_id"]` 找回上一次 graph state。

模块 19 解决的是：

```txt
进程重启后，同一个 thread_id 的 graph 状态仍然能恢复。
```

这对多轮对话、工具调用中断恢复、后续 subagent 状态都很重要。

## 它在完整链路里的位置

```txt
前端输入
-> FastAPI chat routes
-> build_run_config()
   -> config.configurable.thread_id
-> RuntimeBackedAgentAdapter
-> create_async_checkpointer()
-> LangGraph create_agent(checkpointer=...)
-> graph stream
-> checkpointer 保存/读取 graph state
-> AgentEvent / SSE
```

关键字段仍然是模块 3 定下来的：

```txt
config["configurable"]["thread_id"]
```

模块 19 不改 run config 的形状，只让这个字段真正能驱动持久化 state。

## 为什么不能直接用同步 SqliteSaver

SlotFlow 当前通过 async API 运行 graph：

```txt
astream_events(...)
```

官方同步 `SqliteSaver` 不支持 async 方法。实际运行会调用：

```txt
aget_tuple()
aput()
aput_writes()
```

所以模块 19 使用：

```txt
AsyncSqliteSaver
AsyncPostgresSaver
```

这也是为什么持久化 checkpointer 的创建进入了 async 工厂：

```txt
create_async_checkpointer()
```

而原来的同步 `create_checkpointer()` 只继续负责：

```txt
none
memory
```

## 输入是什么

runtime 配置新增字段：

```py
SlotFlowRuntimeConfig(
    checkpointer_backend="none" | "memory" | "sqlite" | "postgres",
    checkpointer_sqlite_path=Path(".slotflow/checkpoints.sqlite3"),
    checkpointer_postgres_uri=None,
    checkpointer_setup=True,
)
```

环境变量：

```bash
SLOTFLOW_CHECKPOINTER_BACKEND=sqlite
SLOTFLOW_CHECKPOINTER_SQLITE_PATH=.slotflow/checkpoints.sqlite3
SLOTFLOW_CHECKPOINTER_SETUP=true
```

Postgres：

```bash
SLOTFLOW_CHECKPOINTER_BACKEND=postgres
SLOTFLOW_CHECKPOINTER_POSTGRES_URI=postgresql://user:pass@localhost:5432/slotflow
SLOTFLOW_CHECKPOINTER_SETUP=true
```

`SLOTFLOW_CHECKPOINTER_SETUP=true` 会创建或迁移官方 checkpoint 表。Postgres 第一次使用时必须 setup。

## 输出是什么

runtime 会把官方 saver 传给 harness builder：

```txt
AsyncSqliteSaver
AsyncPostgresSaver
InMemorySaver
None
```

外层仍然只看到：

```txt
AgentAdapter.stream_events(...)
```

路由层不需要知道底层 checkpointer 是什么。

## 和模块 18 的区别

这两个模块都可以用 SQLite，但职责完全不同：

```txt
模块 18：SlotFlow 业务会话数据
  threads / messages / runs
  给 API 和前端会话历史读

模块 19：LangGraph graph 状态
  checkpoints / writes
  给 LangGraph 恢复多轮 state 读
```

不要把这两层合成一个仓库。业务 message 适合给用户展示；checkpoint 适合给 graph 恢复内部状态。

## 主要代码

```txt
backend/app/chat/runtime.py
  SlotFlowRuntimeConfig
  create_checkpointer()
  create_async_checkpointer()
  create_sqlite_checkpointer()
  create_postgres_checkpointer()
  RuntimeBackedAgentAdapter.aclose()

backend/tests/test_runtime.py
  SQLite async saver 表结构测试
  Postgres 配置边界测试
  SQLite checkpointer 跨 adapter 重启恢复状态测试

backend/pyproject.toml
  langgraph-checkpoint-sqlite
  langgraph-checkpoint-postgres
  psycopg[binary]
```

## 测试怎么读

最关键的测试是：

```txt
test_runtime_backed_adapter_sqlite_checkpointer_survives_adapter_restart
```

它做了这个过程：

```txt
第一个 RuntimeBackedAgentAdapter
-> 同一个 thread_id 问第一句
-> SQLite checkpointer 落盘
-> 关闭第一个 adapter

第二个 RuntimeBackedAgentAdapter
-> 用同一个 SQLite checkpoint 文件
-> 同一个 thread_id 问第二句
-> state.snapshot 里仍然包含第一轮 user/assistant 消息
```

这说明模块 19 不是只创建了一个 saver 对象，而是真的让 graph state 跨 runtime 实例保留下来。

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_runtime.py
```

## 这一模块不做什么

模块 19 明确不做：

```txt
不把 checkpoint 表合并到模块 18 的业务仓库
不实现自定义 checkpoint schema
不启动 PostgreSQL 测试服务
不把 Postgres 设为默认值
不做 checkpoint 清理策略
不做跨用户隔离
```

SQLite 适合本地学习和开发；Postgres 是生产方向，但需要真实数据库服务和部署配置。
