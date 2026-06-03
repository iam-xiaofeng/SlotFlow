# 模块 3：run 配置构建器

## 这一模块解决什么问题

模块 1 定义了数据形状，模块 2 让 thread / message / run 可以被保存。
模块 3 开始处理“准备调用 agent”之前的最后一步：把一次用户请求整理成
agent 能理解的运行配置。

这里最容易糊涂的是两个词：

```txt
config
context
```

在 SlotFlow 里，先按这个规则理解：

```txt
config  -> 给 LangGraph / checkpointer 这类运行时系统用
context -> 给 SlotFlow 自己的业务逻辑用
```

它们不能混成一个大字典。混在一起以后，后面接真实 harness 时你会很难判断：
这个字段是 LangGraph 需要，还是我们自己的 agent 逻辑需要。

## 它在完整链路里的位置

模块 3 位于仓库和 agent 之间：

```txt
前端输入
-> 后端 API
-> 领域模型
-> 内存仓库
-> run 配置  <-- 当前模块
-> fake agent / real harness
-> SSE 事件
-> 前端状态
-> UI 渲染
```

后续 FastAPI stream 接口大概会按这个顺序工作：

```txt
1. 接收前端 message
2. 保存 user message
3. 创建 run
4. build_run_config(thread_id, run_id, request)
5. 把 config + context 交给 fake agent 或真实 harness
```

模块 3 只负责第 4 步。

## 文件结构

```txt
backend/app/chat/run_config.py
  mode_to_feature_flags(...)
  build_run_config(...)

backend/tests/test_run_config.py
  run config 构建规则测试
```

## 输入是什么

`build_run_config(...)` 接收三样东西：

```py
thread_id
run_id
request: ChatStreamRequest
```

示例：

```py
request = ChatStreamRequest(
    message="开始研究",
    model_name="gpt-learning",
    mode="ultra",
    agent_name="researcher",
    files=["upload_1"],
)

bundle = build_run_config(
    thread_id="thread_123",
    run_id="run_456",
    request=request,
)
```

`message` 本身不会进入 `RunContext`。因为模块三只整理运行配置，不负责保存或拼接
聊天历史。用户消息会由模块二的仓库保存，后续 fake agent / real harness 再从当前
请求或历史中读取需要的内容。

## 输出是什么

输出是 `RunConfigBundle`，里面有两部分：

```txt
config
context
```

`config` 当前长这样：

```py
{
    "configurable": {
        "thread_id": "thread_123",
    }
}
```

这个位置很关键。后面接 LangGraph checkpointer 时，多轮记忆要靠
`config["configurable"]["thread_id"]` 找到同一条会话。

`context` 当前长这样：

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

这些字段是 SlotFlow 业务层关心的运行开关。比如：

```txt
model_name         -> 这次使用哪个模型
mode               -> 用户选择的能力档
thinking_enabled   -> 是否启用思考
is_plan_mode       -> 是否启用规划
subagent_enabled   -> 是否启用子 agent
```

## mode 如何变成功能开关

当前三档规则很简单：

```txt
flash
  thinking_enabled = False
  is_plan_mode = False
  subagent_enabled = False

pro
  thinking_enabled = True
  is_plan_mode = True
  subagent_enabled = False

ultra
  thinking_enabled = True
  is_plan_mode = True
  subagent_enabled = True
```

这不是最终产品规则，只是学习阶段的清晰映射。等真实 harness 接上以后，这些布尔值
可以继续映射到 `RuntimeFeatures`、middleware 或 agent 参数。

## 测试看什么

测试文件是 `backend/tests/test_run_config.py`。

它保护这些规则：

```txt
mode 三档必须稳定翻译成功能开关
thread_id 必须放进 config["configurable"]
run_id / model_name / mode / agent_name / files 必须放进 context
files 进入 context 时要复制一份
flash 模式不启用思考、规划、子 agent
```

这些测试很小，但很重要。因为真实 agent 接上以后，如果 thread_id 放错位置，
错误可能不是立刻爆炸，而是“多轮记忆悄悄失效”。这种问题最难排查，所以现在先用
测试固定住。

## 本模块不做什么

模块 3 明确不做：

```txt
不启动 agent
不读取仓库
不写 FastAPI 路由
不生成 SSE
不处理数据库
不决定真实模型供应商
```

下一步模块 4 会写 LangGraph v3 event adapter，让这个 `config + context` 真正进入
“agent 事件流”的阶段。到那一步，`context` 里的模型名、模式、run_id 会跟着事件一路
流向 SSE 和前端。
