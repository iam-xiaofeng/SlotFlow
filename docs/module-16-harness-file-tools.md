# 模块 16：Harness workspace 文件工具

模块 16 把模块 15 的 `SlotFlowWorkspace` 暴露成第一批 LangChain file tools。

当前只做 workspace 范围内的文本文件能力：

```txt
workspace_list
workspace_read
workspace_write
```

其中 `workspace_write` 默认不注册，只有 `SlotFlowSandboxConfig.writes_enabled=True` 时才会出现在
agent 的工具列表里。

## 这一层解决什么问题

它解决的是：“agent 如果需要看 workspace 文件，应该通过什么受控入口？”

模块 15 只定义了安全边界，还没有把能力交给 agent。模块 16 把这个边界接到 harness tools
registry：

```txt
build_harness_tools(...)
-> slotflow_context
-> workspace_list / workspace_read / workspace_write
-> MCP tools
```

文件工具本身不重新判断路径是否安全，而是调用：

```txt
SlotFlowWorkspace.list_entries()
SlotFlowWorkspace.read_text()
SlotFlowWorkspace.write_text()
```

这样路径穿越、symlink 逃逸、读写大小上限、写入开关都由 sandbox/workspace 层统一处理。

## 它接收什么输入

`build_workspace_tools()` 接收：

```txt
SlotFlowSandboxConfig | None
```

三个工具接收的参数：

```txt
workspace_list(path=".")
workspace_read(path)
workspace_write(path, content)
```

这些 `path` 都是 workspace 内部相对路径，不允许传绝对路径、`../`、Windows drive、反斜杠
路径或 NUL 字节。

## 它输出什么数据

工具返回 JSON 字符串。当前保持小而可读，方便后续 SSE/tool projection 归一化。

`workspace_list` 返回：

```json
{
  "path": ".",
  "entries": [
    {"path": "docs", "kind": "directory", "size_bytes": null},
    {"path": "notes/a.txt", "kind": "file", "size_bytes": 5}
  ],
  "source": "slotflow_workspace"
}
```

`workspace_read` 返回：

```json
{
  "path": "docs/a.txt",
  "content": "hello",
  "size_bytes": 5,
  "source": "slotflow_workspace"
}
```

`workspace_write` 返回：

```json
{
  "path": "notes/a.txt",
  "bytes_written": 5,
  "source": "slotflow_workspace"
}
```

如果 workspace 层拒绝操作，异常会继续抛出；模块 17 再统一把 tool exception 转成
`ToolMessage(status="error")`。

## 它在完整链路里的位置

模块 16 位于 harness tools registry：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> SlotFlowHarnessConfig.sandbox_config
-> build_harness_tools()
-> workspace file tools
-> LangGraph create_agent(tools=...)
-> tool call / ToolMessage
-> AgentEvent / SSE / 前端
```

注意：它不是文件上传模块，也不是代码执行 sandbox。它只是让 agent 可以通过受控工具读写
workspace 内的文本文件。

## 主要代码

```txt
backend/app/harness/tools/workspace.py
backend/app/harness/tools/registry.py
backend/app/harness/tools/__init__.py
backend/tests/test_harness_tools.py
backend/tests/test_harness_builder.py
backend/tests/test_harness_mcp.py
```

## 测试怎么读

重点测试：

```txt
1. build_harness_tools 默认加入 slotflow_context、workspace_list、workspace_read
2. extra_tools 仍然可以按 tool.name 覆盖同名内置工具
3. workspace_list 返回目录 entry JSON
4. workspace_read 返回文本内容和字节数
5. workspace_write 默认不注册
6. writes_enabled=True 时 workspace_write 才注册并能写入 workspace root 内部文件
7. MCP tools 排在 workspace tools 后面
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_harness_tools.py tests/test_harness_builder.py tests/test_harness_mcp.py tests/test_harness_sandbox.py
```

## 这一模块不做什么

当前明确不做：

```txt
不执行 shell
不读写 workspace root 外文件
不做二进制文件上传
不做 glob/grep
不做工具审计日志
不吞掉工具异常
不处理 dangling tool call
```

tool error 和 dangling tool call 会在模块 17 进入 middleware 层统一处理。
