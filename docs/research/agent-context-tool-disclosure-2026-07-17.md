# Agent 上下文压缩与渐进式工具披露调研

- 调研日期：2026-07-17（Asia/Shanghai）
- 目标：为 SlotFlow 设计不依赖额外模型/服务的上下文归档、cache-friendly context epoch、工具空间渐进披露和 Subagent 工具裁剪。
- 本文记录公开代码与文档中可验证的实现；不把未公开产品内部行为当成事实。

## SlotFlow 真实基线

2026-07-17 通过官方 LangSmith 只读检查最新 `glm-5.2` 调用：单次输入 107,372 tokens、输出 166 tokens；模型输入有 137 条内部消息（1 System、10 Human、61 AI、65 Tool）。ToolMessage 内容约 153,605 字符，AIMessage 内容约 102,561 字符；54 个工具的完整 Schema 合计约 30,617 字符。结论：历史 AI/Tool 中间消息是最大头，工具 Schema 是第二层稳定开销。

## DeerFlow（公开 main，检查日期 2026-07-17）

来源：

- https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/tools/builtins/tool_search.py
- https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/config/tool_search_config.py
- https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/tools/tools.py

可验证结论：

1. DeerFlow 将带 MCP metadata 的工具放入 `DeferredToolCatalog`，未提升前不向模型绑定完整 Schema。
2. System Prompt 的 `<available-deferred-tools>` 只列工具名称；模型通过 `tool_search` 查询。
3. `tool_search` 返回匹配工具的完整 OpenAI function Schema，并通过 LangGraph `Command(update=...)` 把 `catalog_hash + promoted names` 写入每个 thread 的 graph state。
4. promotion 使用 catalog hash 隔离工具目录版本；组装异常时 fail-closed，避免悄悄重新绑定全部 MCP Schema。
5. 支持基于 MCP routing metadata 的 Top-K 自动提升；Lead Agent 与 Subagent 走同一 deferred assembly 边界。
6. 当前公开实现主要 defer MCP 工具，不代表所有内置工具都分层。

## Pi Coding Agent（公开文档，检查日期 2026-07-17）

来源：

- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md#dynamic-tool-loading
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md

可验证结论：

1. Extension 可以注册很多工具但只保留少量 active tools；loader/search tool 执行时调用 `pi.setActiveTools()`。
2. 动态激活要求 additive：同一 session/上下文中新增工具，不频繁移除；Pi 会把新增工具名称记录在 loader tool result 上，并在下一次模型请求前应用 active set。
3. 支持原生 deferred loading 的 provider/model 可以把新定义锚定在 tool-search result 位置，保持初始 Prompt Prefix；不支持时回退为下一次请求发送完整 active tool list，这可能使 provider cache 前缀失效一次。
4. Pi 明确建议 loader 整个 session 保持 active、工具只增不换；动态工具不应附加会重建 System Prompt 的 active-only guideline。
5. Pi 要求工具输出截断；其 compaction 将完整 session entry 保留在持久记录中，模型看到 summary + `firstKeptEntryId` 之后的最近消息。Compaction 默认使用 LLM，因此 SlotFlow 严格 RPM 模式不能直接照搬为隐藏额外调用。

## 开源 Codex（公开 `openai/codex` main，检查日期 2026-07-17）

来源：

- https://github.com/openai/codex/blob/main/codex-rs/tools/src/tool_search.rs
- https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/tool_search.rs
- https://github.com/openai/codex/blob/main/codex-rs/core/src/context_manager/history.rs
- https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs
- https://github.com/openai/codex/blob/main/codex-rs/tools/src/tool_output.rs

可验证结论：

1. Tool Search 使用 BM25，本地索引工具名、描述和参数 Schema 属性名，返回 `LoadableToolSpec`；Function/Namespace 中可延迟的定义标记 `defer_loading=true`。
2. 支持 namespace tool，把同一来源的多个工具组织成命名空间；tool-search handler 在 catalog 未变化时复用缓存索引。
3. `ContextManager` 将完整 history 与 model-facing projection 分开，维护 history version、token usage、tool call/output 配对和 normalization。
4. 压缩或 rollback 会替换 model-facing history 并提升 history version；工具输出有统一 truncation policy，call/output 配对不能被单边裁掉。
5. Compaction 产生 replacement history/compacted item；公开实现包含本地/远端压缩路径。本文只据公开代码描述，不推断 Codex 托管产品的私有实现。

备注：2026-07-17 尝试通过官方 Codex manual helper 获取 `https://developers.openai.com/codex/codex-manual.md`，HEAD 返回 HTTP 403；因此 Codex 部分仅引用公开 `openai/codex` 仓库，不扩展到未验证产品行为。

## SlotFlow 设计决定

### Context Epoch

- canonical archive/checkpoint 永久保存完整历史；模型上下文按 epoch 管理。
- epoch 内 append-only，不频繁重写旧 messages；达到模型输入预算、发生回滚/模型切换或显式压缩时才建立新 epoch，接受一次缓存失效。
- 新工具输出第一次进入模型上下文时就使用 bounded preview + archive id；完整 payload 写入 thread 隔离的业务 Archive，避免事后重写旧 ToolMessage。
- 压缩摘要明确提醒模型：需要旧细节时调用受控 `context_archive_search/context_archive_read`。模型不得直接读取 checkpoint SQLite。

### 模型 Context Window

优先级：

1. `.env` 的 per-model 显式 override（custom relay 必需的可靠来源）；
2. LiteLLM 随包 model metadata（只读本地 catalog，不联网）；
3. provider 返回的已验证 metadata；
4. 都未知时使用保守默认并标记 `source=default/unknown`，由真实 context-overflow 错误触发一次压缩后重试，不猜测无限窗口。

Context budget 不是等于 window：必须保留输出和工具循环余量，按 window 的配置比例或显式 reserve tokens 得到输入触发阈值。

### 分层延迟工具空间

固定内置空间各有一个小加载器：core/orchestration、workspace/files、sandbox/compute、browser/interactive、network/API、documents/media、extensions/skills/MCP、memory。加载器描述稳定列出本空间工具名称和一行能力，不依赖严格关键词；模型按精确名称请求激活。

MCP/插件采用二级目录：统一 `extension_tools` 先列 source/server/namespace，再列或激活该 source 下的工具，避免 MCP server 增多时把全部工具名和 Schema 放进基础 Prompt。Schema promotion 写入 graph state，同一 epoch 只增不减；原生 deferred protocol 可用时使用 loadable references，否则下一次正常绑定已提升 Schema。

### Skills

SlotFlow 当前 `build_skills_prompt()` 已调用 `top_level_skills()`：System Prompt 只列顶层 Skills；位于父 Skill 目录下的成员/子 Skill 不单独进入 Prompt。成员内容由顶层 Skill 的索引说明引导模型按需读取。这是资源渐进披露，不等同于 function-tool activation；若 Skill 声明受限工具，工具可见性仍需由 tool-space policy 单独控制。

### Subagent

Subagent 默认无 todo、HITL、长期记忆和递归 `task_tool`；主 Agent 在 `task_tool` 中声明允许的 tool spaces。后端必须限制空间数量、禁止 wildcard/all、按 profile/role 交集收窄，并让每个 child 独立维护 promotion state，防止主 Agent“全给”或跨 child 污染。

### 重试边界

- context overflow：先建立新 epoch/压缩，再重试；相同输入不盲重放。
- 429/连接超时/上游 5xx：仅在模型调用尚未产生任何 chunk、尚未执行工具时按可配置退避重试。
- Mid-stream、已执行工具、取消、认证/参数错误：不整轮重放，避免重复副作用。
- missing cache usage 必须记为 `unknown`，不能伪装为 miss。
