# 模块 2：内存版 thread / message / run 仓库

## 这一模块解决什么问题

模块 1 定义了数据长什么样。模块 2 开始处理“这些数据先放在哪里”。

现在我们还不引入数据库。原因不是数据库不重要，而是此刻更重要的是看清楚：

```txt
thread 是会话容器
message 属于某个 thread
run 也属于某个 thread
message / run 变化时，thread 的更新时间也要变化
```

这个模块就像一个临时但规则清楚的储物柜。后面接 FastAPI 路由时，路由层只要调用
仓库，不需要自己管理一堆字典。以后换成 SQLite 或 Postgres，也可以让外部 API
尽量不变。

## 它在完整链路里的位置

模块 2 位于后端 API 和后续 agent 执行之间：

```txt
前端输入
-> 后端 API
-> 领域模型
-> 内存仓库  <-- 当前模块
-> run 配置
-> fake agent / real harness
-> SSE 事件
-> 前端状态
-> UI 渲染
```

等模块六写 FastAPI 路由时，大概会是这种调用关系：

```txt
POST /threads
-> repository.create_thread()

POST /threads/{thread_id}/stream
-> repository.add_message(role="user")
-> repository.create_run()
-> 后续 agent stream
-> repository.add_message(role="assistant")
-> repository.update_run_status()
```

## 文件结构

```txt
backend/app/chat/repository.py
  InMemoryChatRepository
  ThreadNotFoundError
  RunNotFoundError

backend/tests/test_chat_repository.py
  仓库行为测试
```

## 输入是什么

仓库接收的是模块 1 已经定义好的数据概念，而不是原始 HTTP 请求。

创建 thread：

```py
repository.create_thread(title="学习 SlotFlow")
```

追加 message：

```py
repository.add_message(
    thread_id,
    role="user",
    content="解释一下 run_id",
)
```

创建 run：

```py
repository.create_run(
    thread_id,
    model_name="fake-model",
    mode="pro",
    agent_name="default",
)
```

更新 run：

```py
repository.update_run_status(run_id, status="completed")
repository.update_run_status(run_id, status="failed", error="agent crashed")
```

## 输出是什么

仓库输出模块 1 的 Pydantic 模型：

```txt
ThreadRecord
MessageRecord
RunRecord
```

有一个细节很重要：仓库返回的是模型副本，不是内部对象本身。

这样做是为了防止这种问题：

```py
thread = repository.get_thread(thread_id)
thread.title = "外部随手改掉"
```

如果仓库直接把内部对象交出去，上面这行代码会绕过仓库规则，直接污染内部状态。
现在返回副本，外部怎么改都不会影响仓库里保存的记录。

## 为什么现在用内存

内存仓库有三个学习价值：

```txt
1. 运行快，测试简单
2. 不需要先解释数据库连接、迁移、事务
3. 可以先固定业务行为，再决定数据库实现
```

它当然不是最终生产方案。进程重启以后，内存数据会消失。这个限制我们明确接受，
因为当前目标是把后端链路走清楚。

## 测试怎么看

测试文件是 `backend/tests/test_chat_repository.py`。

它保护这些规则：

```txt
空标题 thread 会变成“新会话”
thread 列表按最近活动排序
message 按写入顺序保存
不存在的 thread 不能悄悄创建
run 从 queued 开始，然后可以更新状态
run 失败时可以保存 error
不存在的 run 会抛出明确错误
仓库返回副本，外部不能绕过仓库改内部状态
```

这些测试仍然不是端到端测试。它们只说明仓库这一个小模块可信。

## 本模块不做什么

模块 2 明确不做：

```txt
不创建 FastAPI 路由
不接收 HTTP 请求
不启动 fake agent
不生成 SSE
不写数据库
不处理用户认证
```

下一步模块 3 会基于 thread_id 和 run_id 构建 run config，让数据开始进入
“准备调用 agent”的阶段。
