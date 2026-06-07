# 模块 15：Sandbox / workspace 边界

模块 15 给 SlotFlow harness 加入第一版 sandbox / workspace 边界。

它不暴露任何 LangChain tool，也不执行代码。当前只先定义一件事：

```txt
未来文件工具、代码执行工具、subagent workspace 只能碰哪里，怎么判断路径安全
```

SlotFlow 不把工具权限放在 `SKILL.md` 里。Skill 只描述能力；agent/run/harness config 决定
agent 能看到哪些工具；sandbox / permission layer 决定工具能访问哪些资源。

模块 15 先做 workspace 安全边界，是为了模块 16 加文件工具时，不让每个工具各自处理路径
穿越、文件大小、写入开关这些问题。

## 这一层解决什么问题

它解决的是：“SlotFlow 后端里，安全 workspace 的边界在哪里？”

如果直接在文件工具里写：

```txt
open(user_path)
```

那么每个工具都要自己判断：

```txt
这个路径是不是绝对路径？
有没有 ../ 穿越？
会不会通过 symlink 跳出 workspace？
能不能写？
文件太大怎么办？
```

模块 15 把这些规则先集中到：

```txt
app/harness/sandbox/
```

后续模块 16 的文件工具应该调用 `SlotFlowWorkspace`，而不是自己拼路径。

## 它接收什么输入

`SlotFlowSandboxConfig` 接收：

```txt
workspace_root       workspace 根目录
writes_enabled       是否允许写入
max_read_bytes       单文件读取字节上限
max_write_bytes      单文件写入字节上限
```

runtime 从环境变量读取：

```txt
SLOTFLOW_WORKSPACE_ROOT
SLOTFLOW_WORKSPACE_WRITES_ENABLED
SLOTFLOW_WORKSPACE_MAX_READ_BYTES
SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES
```

默认规则：

```txt
workspace_root  -> .slotflow/workspace
writes_enabled  -> false
max_read_bytes  -> 1 MiB
max_write_bytes -> 1 MiB
```

注意：runtime 只读取配置，不创建 workspace 对象，也不碰文件。

## 它输出什么数据

`SlotFlowWorkspace` 提供四个边界方法：

```txt
resolve_path()
list_entries()
read_text()
write_text()
```

`resolve_path()` 是核心。它会拒绝：

```txt
空路径
绝对路径
../ 穿越
Windows drive 风格路径
反斜杠路径
NUL 字节
symlink 跳出 workspace root
```

`read_text()` 会检查文件大小：

```txt
size <= max_read_bytes
```

`write_text()` 默认禁止写入；只有配置显式打开：

```txt
writes_enabled=True
```

才会写文件，并且仍然检查：

```txt
encoded_size <= max_write_bytes
```

## 它在完整链路里的位置

模块 15 现在位于 harness 配置和 tools registry 边界：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> SlotFlowRuntimeConfig.sandbox_config
-> SlotFlowHarnessConfig.sandbox_config
-> build_harness_tools(..., sandbox_config=...)
-> 后续文件工具 / workspace 工具
```

当前 `build_harness_tools()` 只是接收 `sandbox_config`，还不创建文件工具。这样做是为了先把
配置传递路径固定住，模块 16 再接工具时只扩展 tools registry。

## 主要代码

```txt
backend/app/harness/sandbox/__init__.py
backend/app/harness/sandbox/config.py
backend/app/harness/sandbox/workspace.py
backend/app/harness/config.py
backend/app/chat/runtime.py
backend/app/harness/tools/registry.py
backend/tests/test_harness_sandbox.py
```

核心对象：

```txt
SlotFlowSandboxConfig
SlotFlowWorkspace
WorkspaceEntry
WorkspacePathError
WorkspaceWriteDisabledError
WorkspaceFileTooLargeError
```

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_sandbox.py
```

它保护九件事：

```txt
1. 合法相对路径解析到 workspace root 内
2. 绝对路径、穿越路径、Windows drive、反斜杠、NUL 字节会被拒绝
3. workspace 内 symlink 不能跳出 root
4. 可以列目录
5. 可以读取小文件
6. 超过 max_read_bytes 的文件不能读
7. 写入默认关闭
8. 显式打开写入后仍受 max_write_bytes 限制
9. runtime 会读取 sandbox env，并把 sandbox_config 传到 harness/tools registry
```

窄测试命令：

```bash
cd /home/dell/code/SlotFlow/backend
uv run pytest -q tests/test_harness_sandbox.py tests/test_runtime.py tests/test_harness_builder.py tests/test_harness_tools.py tests/test_harness_skills.py
```

## 这一模块不做什么

当前明确不做：

```txt
不把 workspace 暴露成 LangChain tool
不执行 shell
不执行 Python 代码
不接 Docker / Firecracker / nsjail
不做网络隔离
不接上传接口
不做工具审计日志
不处理 tool error / dangling tool call
```

这些能力会后续逐步加。模块 15 只先把 workspace 的路径和读写安全边界固定住。
