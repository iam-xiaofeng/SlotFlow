# 模块 1：领域模型和可读 ID

## 这一模块解决什么问题

模块 1 先不碰 FastAPI 路由，也不碰真实 agent。它只做一件事：
把后端内部最核心的几类数据先定下来。

可以把它理解成给后面的链路准备“统一包装盒”：

```txt
thread  -> 一条会话
message -> 会话里的一条消息
run     -> 用户点一次发送后产生的一次执行
context -> 本次执行的业务开关
config  -> 以后传给 LangGraph / harness 的运行配置
```

如果没有这一层，后面写路由、仓库、SSE、前端状态时，每一层都会随手发明自己的字段名。
项目会很快变乱，也很难通过测试观察数据是怎么变化的。

## 它在完整链路里的位置

模块 1 位于链路最底部，是后续所有模块共享的语言：

```txt
前端输入
-> 后端 API
-> 领域模型  <-- 当前模块
-> run 配置
-> fake agent / real harness
-> SSE 事件
-> 前端状态
-> UI 渲染
```

当前模块还不会真的处理 HTTP 请求。它只是先定义好：

```txt
前端将来会发什么请求
后端内部会保存什么记录
agent 运行时需要什么上下文
测试应该抓住哪些基础规则
```

## 文件结构

```txt
backend/app/chat/__init__.py
  说明 chat 后端包以后会承接完整聊天链路。

backend/app/chat/ids.py
  生成 thread_ / msg_ / run_ 这类可读 ID。

backend/app/chat/models.py
  定义 ThreadRecord、MessageRecord、RunRecord、ChatStreamRequest 等 Pydantic 模型。

backend/tests/test_chat_models.py
  验证模块一的数据规则。
```

## 输入是什么

模块 1 的主要输入有两类。

第一类是前端将来会传来的请求数据：

```json
{
  "message": "解释一下 SlotFlow",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "agent_name": "default",
  "files": [],
  "metadata": {}
}
```

其中只有 `message` 是真正必须的。其他字段先给默认值，是为了让第一条链路更轻，
同时保留后面接模型选择、模式切换、文件上传的入口。

第二类是后端自己生成的身份数据：

```txt
thread_ + 12 位随机短串
msg_    + 12 位随机短串
run_    + 12 位随机短串
```

这些 ID 暂时不追求分布式系统级别的设计，只追求本地学习时容易看懂。

## 输出是什么

模块 1 输出的是稳定的数据对象。

`ThreadRecord` 表示一条会话：

```txt
id
title
created_at
updated_at
```

`MessageRecord` 表示一条消息：

```txt
id
thread_id
role
content
run_id
metadata
created_at
```

`RunRecord` 表示一次执行：

```txt
id
thread_id
status
model_name
mode
agent_name
error
created_at
updated_at
```

`RunContext` 表示本次运行的业务开关：

```txt
thread_id
run_id
model_name
mode
agent_name
files
thinking_enabled
is_plan_mode
subagent_enabled
```

`RunConfigBundle` 把两类东西分开：

```txt
config   -> 更接近 LangGraph RunnableConfig，后面 thread_id 会放进 configurable
context  -> SlotFlow 自己的业务开关，后面控制模型、规划、子 agent 等行为
```

这个拆分很重要。它会防止我们把所有字段都塞进一个大字典里，最后不知道哪个字段给
LangGraph 用，哪个字段给业务逻辑用。

## 测试怎么看

测试文件是 `backend/tests/test_chat_models.py`。

它保护的是基础不变量：

```txt
ID 必须带 thread_ / msg_ / run_ 前缀
最小 ChatStreamRequest 只需要 message
mode 只能是 flash / pro / ultra
空白 message 会被拒绝
files 和 metadata 每次请求都必须独立
记录时间统一是 UTC
RunRecord 默认从 queued 开始
RunConfigBundle 保持 config 和 context 分离
```

这些测试不是为了证明 agent 会回答问题。它们只证明“数据盒子”没有变形。
等模块二开始写内存仓库时，仓库会直接保存这些模型。

## 本模块不做什么

模块 1 明确不做：

```txt
不创建 FastAPI 路由
不保存到内存仓库或数据库
不启动 agent
不生成 SSE
不连接真实 DeerFlow harness
```

这样拆的原因是：先把数据形状讲清楚，再看数据怎么被保存、怎么被传给 agent、
怎么变成流式事件。一步一步来，后面的链路会清楚很多。
