# 模块 22：文件上传接口

模块 22 给 SlotFlow 后端加入第一版用户文件上传 API：

```txt
POST /api/uploads
GET  /api/uploads/{file_id}
```

上传文件会落到 SlotFlow workspace：

```txt
.slotflow/workspace/uploads/{file_id}/{filename}
```

并返回稳定的：

```txt
file_id
```

## 这一层解决什么问题

之前 `ChatStreamRequest.files` 只是字符串占位：

```json
{
  "files": ["upload_1"]
}
```

这无法说明文件是否真实存在、放在哪里、大小是多少。

模块 22 先补上上传入口：

```txt
浏览器 multipart upload
-> FastAPI UploadFile
-> SlotFlowUploadStore
-> workspace/uploads
-> UploadedFileRecord
```

模块 23 再把这些 `file_id` 接进 chat run。

## 输入是什么

上传接口接收 multipart form：

```txt
file=<binary file>
```

示例：

```bash
curl -F "file=@notes.txt" http://localhost:8000/api/uploads
```

## 输出是什么

返回：

```json
{
  "id": "file_abc123...",
  "filename": "notes.txt",
  "content_type": "text/plain",
  "size_bytes": 128,
  "workspace_path": "uploads/file_abc123.../notes.txt",
  "created_at": "..."
}
```

`workspace_path` 是相对于 workspace root 的路径，不是用户机器上的绝对路径。

## 存储规则

文件名会做清洗：

```txt
hello world.txt -> hello_world.txt
../bad.txt      -> bad.txt
```

文件大小使用 workspace 的写入上限：

```txt
SlotFlowSandboxConfig.max_write_bytes
```

上传 API 是用户显式上传，不等同于 agent 的 `workspace_write` 工具，所以不受
`writes_enabled` 控制；但它仍然使用 `SlotFlowWorkspace.resolve_path()` 做路径边界保护。

每个上传目录里会有：

```txt
uploads/{file_id}/{filename}
uploads/{file_id}/metadata.json
```

## 主要代码

```txt
backend/app/uploads/models.py
backend/app/uploads/storage.py
backend/app/uploads/routes.py
backend/app/main.py
backend/tests/test_uploads.py
```

## 测试怎么读

测试保护：

```txt
1. 上传后文件 bytes 写入 workspace/uploads
2. 返回稳定 file_id 和 metadata
3. GET /api/uploads/{file_id} 能读回 metadata
4. 超过 max_write_bytes 返回 413
5. 未知 file_id 返回 404
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_uploads.py
```

## 这一模块不做什么

模块 22 明确不做：

```txt
不把二进制文件写进 SQLite repository
不把上传文件接进 chat run
不做文件预览/下载接口
不做用户系统或多租户隔离
不做病毒扫描
不做 MIME 深度识别
```

模块 23 会把 `file_id` 接入 chat run 的 metadata 和 runtime context。
