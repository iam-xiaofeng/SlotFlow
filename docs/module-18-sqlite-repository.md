# 模块 18：SQLite thread / message / run 仓库

模块 18 把模块 2 的内存仓库扩展成 SQLite 版本：

```txt
SQLiteChatRepository
```

它保存的是 SlotFlow 自己的业务数据：

```txt
thread   会话容器
message  会话里的消息
run      一次 assistant 执行记录
```

这层不是 LangGraph checkpointer。checkpointer 保存的是 graph 内部状态，模块 19 再处理。

## 这一层解决什么问题

模块 2 的 `InMemoryChatRepository` 有一个明确限制：

```txt
进程重启后，thread/message/run 全部丢失。
```

模块 18 解决的是业务会话持久化：

```txt
创建 thread
-> 写入用户 message
-> 创建 run
-> stream 结束后写入 assistant message
-> 更新 run 状态
-> 重启后仍能读取这些记录
```

前端后面做会话历史、侧边栏、消息回放时，读的就是这层数据。

## 它在完整链路里的位置

```txt
前端输入
-> FastAPI chat routes
-> ChatRepository
-> SQLiteChatRepository  <-- 当前模块
-> run config
-> runtime / harness / agent
-> SSE
-> ChatRepository
-> SQLiteChatRepository  <-- 保存最终消息和 run 状态
-> 前端状态
```

路由层仍然只依赖 `ChatRepository` 协议，不关心后面是内存还是 SQLite。

## 输入是什么

输入没有变，仍然是模块 2 固定下来的仓库方法：

```py
repository.create_thread(title="学习 SlotFlow")

repository.add_message(
    thread_id,
    role="user",
    content="解释 SQLite repository",
)

repository.create_run(
    thread_id,
    model_name="deepseek-v4-flash",
    mode="pro",
    agent_name="default",
)

repository.update_run_status(run_id, status="completed")
```

模块 18 的重点不是改调用方，而是让同一套调用可以落盘。

## 输出是什么

输出仍然是模块 1 的 Pydantic 记录：

```txt
ThreadRecord
MessageRecord
RunRecord
```

SQLite 只是内部存储格式。外部不直接拿 SQLite row，也不直接拼 SQL。

## 表结构怎么理解

第一版只有三张业务表：

```txt
threads
messages
runs
```

`threads` 保存会话标题和创建/更新时间。

`messages` 保存消息正文、角色、可选 run_id、metadata JSON。它有一个自增 `sequence`，
用来保护消息按写入顺序返回。

`runs` 保存 model/mode/agent/status/error。它也有自增 `sequence`，用来保护 run 按创建顺序返回。

`message.run_id` 当前不做外键约束，因为模块 2 的契约允许先保存一条带外部 run_id 的消息。
后面如果 API 规则收紧，再单独调整这个业务约束。

## 如何启用 SQLite

默认仍然是内存仓库：

```txt
SLOTFLOW_CHAT_REPOSITORY_BACKEND=memory
```

切到 SQLite：

```bash
SLOTFLOW_CHAT_REPOSITORY_BACKEND=sqlite
SLOTFLOW_CHAT_SQLITE_PATH=.slotflow/chat.sqlite3
```

应用启动入口在 `create_app()` 里调用：

```txt
build_chat_repository()
```

所以测试可以继续显式传入仓库，真实启动可以通过环境变量选择仓库。

## 主要代码

```txt
backend/app/chat/repository.py
  ChatRepository
  InMemoryChatRepository
  SQLiteChatRepository
  ChatRepositoryConfig
  build_chat_repository()

backend/app/main.py
  create_app() 默认通过 build_chat_repository() 创建仓库

backend/tests/test_chat_repository.py
  同一套仓库契约同时覆盖 memory 和 sqlite

backend/tests/test_chat_routes.py
  验证 create_app() 可以通过环境变量切到 SQLite
```

## 测试怎么读

仓库契约测试保护：

```txt
空标题 thread 会变成“新会话”
thread 列表按最近活动排序
message 按写入顺序保存
不存在的 thread 不能悄悄创建
run 从 queued 开始，然后可以更新状态
run 失败时可以保存 error
不存在的 run 会抛出明确错误
仓库返回副本，外部不能绕过仓库改内部状态
SQLite 重开连接后仍能读回记录
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_chat_repository.py tests/test_chat_routes.py
```

## 这一模块不做什么

模块 18 明确不做：

```txt
不实现 LangGraph SQLite/Postgres checkpointer
不做数据库迁移框架
不引入 SQLAlchemy
不做用户认证或多租户隔离
不做消息搜索
不做附件/上传文件表
不删除内存仓库
```

模块 19 会处理 graph checkpointer。即使它也可以使用 SQLite，它和模块 18 仍然是两层：

```txt
模块 18：SlotFlow 业务会话数据
模块 19：LangGraph graph 状态检查点
```
