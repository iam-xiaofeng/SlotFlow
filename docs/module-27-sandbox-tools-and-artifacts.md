# 模块 27：Sandbox、文件工具和产物

本模块把 SlotFlow 的工具能力推进到“可以处理用户文件和生成产物”的阶段，但仍然保持一个核心原则：模型不能直接访问本机任意路径。

## 当前 sandbox 是什么

`backend/app/harness/sandbox` 目前不是 Docker 或独立进程隔离，而是 workspace 文件边界。

默认 workspace root 是：

```txt
.slotflow/workspace
```

所有本地文件类工具都必须通过 `SlotFlowWorkspace` 访问文件。它负责：

- 只接受相对路径；
- 拒绝 `..`、绝对路径、Windows drive 前缀和反斜杠路径；
- 拒绝 symlink 逃逸；
- 限制读取和写入字节数；
- 默认关闭模型写入能力。

## workspace_read 支持的文件

`workspace_read` 现在返回结构化 JSON，不再只是假设 UTF-8 文本。

支持：

- `.md`、`.txt`、`.json`、`.csv`、`.py`、`.ts` 等文本文件：返回 `content`；
- `.docx`：从 `word/document.xml` 抽取段落文本；
- `.pdf`：用 `pypdf` 抽取页面文本；
- `.jpg`、`.jpeg`、`.png`、`.gif`、`.webp`：返回图片格式、尺寸、字节数，不内联图片二进制。

图片这样处理是为了兼容不支持视觉输入的模型。模型至少能知道文件存在、大小和尺寸；真正理解图片内容以后需要接视觉模型或 OCR 工具。

## 上传文件进入 run 级路径

前端仍然可以先调用上传 API 得到 `file_id`。发送消息时，后端会先创建 run，再把上传文件复制到：

```txt
uploads/<run_id>/<filename>
```

随后写入消息 metadata 和 `RunContext.uploaded_files` 的都是 run 级 workspace 相对路径，例如：

```txt
uploads/run_abc123abc123/report.md
```

agent 看到的 system prompt 中也会列出这些相对路径，并提示可以用 `workspace_read(path)` 读取。

## 新增工具

当前工具 registry 会注册：

- `slotflow_context`
- `workspace_list`
- `workspace_read`
- `workspace_tree`
- `workspace_search`
- `artifact_list`

当 `SLOTFLOW_WORKSPACE_WRITES_ENABLED=true` 时额外注册：

- `workspace_write`
- `artifact_write`

`artifact_write` 会强制把文件写入：

```txt
artifacts/
```

前端“产物”菜单通过 `/api/workspace/artifacts` 读取 `workspace/artifacts` 下的条目。

## MCP 和 sandbox 的关系

MCP 工具不会自动受 SlotFlow 本地 sandbox 保护。

原因是 MCP server 可以是外部 HTTP 服务、独立进程或远端工具，它的文件系统、网络权限和执行逻辑不一定经过 `SlotFlowWorkspace`。所以不能说“打开 MCP 后就已经被本地 sandbox 限制住了”。

当前策略应该是：

- MCP 默认关闭；
- 只有用户显式配置后才加载；
- 未来 MCP 配置要有 allowlist、超时和风险说明；
- 如果我们自己实现 MCP 文件工具，应让它调用 `SlotFlowWorkspace`，这样才算受当前 sandbox 保护；
- 第三方 MCP 工具只能按“受信任外部能力”处理，不能假装它已经被 workspace sandbox 包住。

