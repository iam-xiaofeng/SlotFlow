# 模块 23：上传文件接入 chat run

## 这个模块解决什么问题？

模块 22 已经能把用户文件保存到 `workspace/uploads`，并返回稳定的 `file_id`。
模块 23 解决下一步：前端在 `/api/chat/threads/{thread_id}/runs/stream` 里带上
`files: [file_id]` 时，后端不能再把它当普通字符串占位，而要在创建 run 前解析成
真实上传文件元数据。

这样一次 run 同时拥有两层信息：

```txt
files
  前端传入的 file_id 列表，用于保持 API 输入简单。

uploaded_files
  后端解析出的结构化元数据，包含 filename/content_type/size/workspace_path。
```

文件二进制仍然只放在 workspace 里，不进入 chat 业务数据库。

## 它接收什么输入？

chat stream 请求仍然接收原来的 `ChatStreamRequest`：

```json
{
  "message": "分析这个文件",
  "model_name": "deepseek-v4-flash",
  "mode": "pro",
  "agent_name": "default",
  "files": ["file_abc123def456"],
  "metadata": {}
}
```

这里的 `files` 必须是模块 22 上传接口返回的 `file_id`。如果某个 ID 不存在，
路由会返回：

```txt
HTTP 404
detail = "upload not found"
```

并且不会保存用户消息，也不会创建 run。

## 它输出什么数据？

成功时，用户消息 metadata 会保存两份文件信息：

```json
{
  "files": ["file_abc123def456"],
  "uploaded_files": [
    {
      "id": "file_abc123def456",
      "filename": "report.md",
      "content_type": "text/markdown",
      "size_bytes": 8,
      "workspace_path": "uploads/file_abc123def456/report.md",
      "created_at": "..."
    }
  ],
  "request_metadata": {}
}
```

`RunContext` 里也会带上同样的 `uploaded_files`。静态 adapter 的
`state.snapshot.state.uploaded_files` 会输出这份元数据，真实 LangGraph runtime
则通过 `context=bundle.context` 读取。

## 它在链路里的位置是什么？

模块 23 位于后端 API 创建 run 之前：

```txt
前端上传文件
-> POST /api/uploads
-> workspace/uploads/{file_id}/...
-> 前端发送 stream，files=[file_id]
-> stream 路由解析 file_id
-> 保存 user message metadata
-> build_run_config(..., uploaded_files=...)
-> AgentAdapter.stream_events(...)
-> SSE state.snapshot 可观察 uploaded_files
```

文件读取不走任意路径。agent 或工具如果要读文件，需要使用
`workspace_path`，并继续受 `SlotFlowWorkspace` 的路径和大小限制保护。

## 当前验证

模块 23 增加和更新了这些测试：

```txt
tests/test_chat_routes.py
  上传文件 -> stream run -> user message metadata/run context 可追踪。
  缺失 file_id -> 404，且不创建 message/run。

tests/test_run_config.py
  build_run_config 会复制 uploaded_files，避免外部对象后续修改污染运行上下文。

tests/test_agent_adapter.py
  StaticProjectionAgentAdapter 的 state.snapshot 会暴露 uploaded_files。

tests/test_uploads.py
  workspace_read 可以用上传返回的 workspace_path 读取文件。

tests/test_harness_middleware.py
  runtime summary state 包含 uploaded_files 字段。
```
