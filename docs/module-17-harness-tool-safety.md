# 模块 17：Tool error / dangling tool call 处理

模块 17 给 SlotFlow harness 加入第一版工具调用安全 middleware：

```txt
SlotFlowToolSafetyMiddleware
```

它处理两类问题：

```txt
1. 工具执行抛异常
2. 模型消息里存在没有匹配 ToolMessage 的 dangling tool call
```

这层属于 LangChain `AgentMiddleware`，不是 workspace tool，也不是 skill 策略。

## 这一层解决什么问题

它解决的是：“工具调用失败时，agent graph 不应该直接崩掉，模型应该看到一个结构化的工具错误。”

没有这层时，文件工具可能抛出：

```txt
WorkspacePathError
WorkspaceFileTooLargeError
WorkspaceWriteDisabledError
```

这些异常如果直接冒泡，会让一次 run 失败，模型没有机会解释错误或选择下一步。

模块 17 把失败转成：

```py
ToolMessage(status="error")
```

这样 LangChain 的 tool-call 协议仍然闭合：

```txt
AIMessage(tool_calls=[...])
-> ToolMessage(tool_call_id="...", status="error")
-> 下一次模型调用
```

## 它接收什么输入

middleware 接收 LangChain 传入的两个请求对象：

```txt
ToolCallRequest
ModelRequest
```

`wrap_tool_call()` / `awrap_tool_call()` 处理真实工具执行：

```txt
tool_call  模型请求的工具名、参数、tool_call_id
tool       已注册工具；未注册时为 None
handler    LangChain 后续工具执行链
```

`wrap_model_call()` / `awrap_model_call()` 处理模型调用前的消息列表：

```txt
request.messages
```

如果历史里有 AI tool call 没有匹配的 `ToolMessage`，会在传给模型前补一个 synthetic error
`ToolMessage`，避免模型 provider 因消息协议不完整而拒绝请求。

## 它输出什么数据

工具异常和未知工具会输出 JSON 字符串形式的 `ToolMessage`：

```json
{
  "error": {
    "type": "tool_execution_error",
    "message": "workspace path must not contain '..'",
    "tool_name": "workspace_read",
    "tool_call_id": "call_bad_path",
    "source": "slotflow_tool_safety",
    "exception_type": "WorkspacePathError"
  }
}
```

未知工具的错误类型是：

```txt
unknown_tool
```

dangling tool call 的错误类型是：

```txt
dangling_tool_call
```

这些错误仍然是 tool protocol 的一部分，不是 SSE error 事件。后续 adapter 看到它们时，仍然会
按普通 tool/message/state 流程投影。

## 它在完整链路里的位置

模块 17 位于 harness middleware registry：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> harness builder
-> build_harness_middleware()
-> SlotFlowToolSafetyMiddleware
-> LangGraph create_agent(middleware=...)
-> tool call / model call
-> AgentEvent / SSE / 前端
```

它和模块 16 的关系：

```txt
workspace_read/list/write
  负责做文件操作，并让 workspace 层拒绝不安全路径

SlotFlowToolSafetyMiddleware
  负责把工具异常转换成 ToolMessage(status="error")
```

这两个职责不能混在一起。文件工具不应该自己决定 agent 的错误协议；middleware 也不应该自己
读写文件。

## 配置开关

默认开启：

```txt
SlotFlowMiddlewareConfig.tool_safety_enabled=True
```

环境变量：

```txt
SLOTFLOW_TOOL_SAFETY_MIDDLEWARE=false
```

这个开关和 runtime summary 开关独立：

```txt
SLOTFLOW_RUNTIME_SUMMARY_MIDDLEWARE=false
SLOTFLOW_TOOL_SAFETY_MIDDLEWARE=false
```

## 主要代码

```txt
backend/app/harness/middleware/tool_safety.py
backend/app/harness/middleware/config.py
backend/app/harness/middleware/registry.py
backend/app/harness/middleware/__init__.py
backend/app/chat/runtime.py
backend/tests/test_harness_middleware.py
backend/tests/test_runtime.py
backend/tests/test_harness_builder.py
```

## 测试怎么读

测试保护：

```txt
1. middleware registry 默认加入 tool safety + runtime summary
2. runtime summary 和 tool safety 可以分别关闭
3. 工具执行异常会变成 ToolMessage(status="error")
4. 未注册工具不会调用 handler，会直接返回 unknown_tool ToolMessage
5. dangling AI tool call 会在下一次模型调用前补 synthetic error ToolMessage
6. 真实 LangGraph fake graph 中，workspace_read 非法路径不会打断 graph
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_harness_middleware.py tests/test_harness_builder.py tests/test_runtime.py tests/test_harness_tools.py
```

## 这一模块不做什么

当前明确不做：

```txt
不重试工具
不隐藏错误来源
不把错误降级成普通 assistant 文本
不在 SKILL.md 里定义工具权限
不做 sandbox 审计日志
不做用户可交互 permission prompt
```

如果某个工具或权限设计被判断为错路线，仍然应该硬移除，而不是留一个“解析兼容但不生效”的
长期兼容层。
