# API 调用链路参考(2026-07-04)

> 配套 `docs/cleanup-2026-07-03-report.md` 与 `AGENTS.md`。本文回答一个问题:
> **每个 HTTP/WS 入口从请求到落库/回包,依次经过哪些层。**
> 分层约定:路由(FastAPI)→ 编排/仓库 → runtime/harness → LangGraph 图 → 工具/沙箱。

## 0. 核心链路总览(聊天流式 run)

```txt
POST /api/chat/threads/{thread_id}/runs/stream        app/chat/routes.py::stream_thread_run
├─ ChatRepository.get_thread / create_run / add_message(user)     app/chat/repository.py(SQLite)
├─ 上传校验+落位  validate_uploaded_files_exist / stage_uploaded_files → app/uploads/storage.py
├─ build_run_config → RunConfigBundle{config(thread_id), context(RunContext)}   app/chat/run_config.py
├─ get_agent_adapter → RuntimeBackedAgentAdapter.stream_events    app/chat/runtime/adapter.py
│  ├─ create_model_for_context → ChatLiteLLM(LiteLLM provider/model metadata；统一 Chat Completions)   app/chat/runtime/models.py
│  ├─ create_async_checkpointer(sqlite/postgres/memory/none)      app/chat/runtime/checkpointer.py
│  ├─ refresh_runtime_skills_config / ensure_mcp_tools_loaded
│  └─ build_slotflow_harness_graph                                app/harness/builder.py
│     ├─ build_harness_tools(内置+MCP+skills+subagent 工具注册)  app/harness/tools/*
│     ├─ build_system_prompt(runtime/freshness/memory/extension/operating 各段)
│     └─ build_slotflow_graph(StateGraph 组装)                    app/harness/graph.py
│        START→prepare→triage_gate→pre_model→SlotFlowSummarizationMiddleware(节点)→agent
│        →post_model→{tools→pre_model | pre_model(todo enforcer) | finalize→END}
│        · prepare: runtime_summary/uploads 注入/skills preflight/记忆检索
│        · triage_gate: pro/ultra 首步小模型 triage,不可行动即 interrupt(澄清)
│        · pre_model: todo 提醒+dangling 修复+每步重算 llm_input_messages+记忆注入系统段
│        · summarize 节点: 官方 SummarizationMiddleware(哨兵只进 messages 通道)
│        · agent: RunnableLambda(sync/async),bind_tools 后 ainvoke
│        · post_model: todo 并行守卫/todo 催写/子代理并行削峰
│        · tools: ToolNode+安全包装(未知工具/异常→error ToolMessage;GraphBubbleUp 放行)
│        · finalize: artifact 发现/显式记忆保存(小模型 rewrite)/后台记忆抽取调度
├─ LangGraphEventAgentAdapter.stream_events → astream_events(version="v3")
│  └─ iter_projection_agent_events                                app/chat/agent_adapter/streaming.py
│     · messages 投影 → 按消息子流拆 .reasoning/.text/.tool_calls 三路
│       - text/reasoning → message.delta(channel 区分)
│       - tool_calls 子投影 → tool.status(live 唯一来源;每消息每工具一次)
│     · values 投影 → state.snapshot(+todo.updated 派生)
│     · 摘要节点消息 → context.compressing(只播一次)
│     · 中断(澄清)→ clarification.requested                      projections.py
├─ iter_business_events → encode_sse_event(SSE 帧)               app/chat/sse.py
└─ 回写:message.delta 聚合/state.snapshot 择优 → add_message(assistant,剥内部标签)
   run 状态机 running→completed/failed/cancelled;首轮后 maybe_generate_thread_title
```

## 1. Chat(前缀 /api/chat)

| 端点 | 链路 |
|---|---|
| GET /models | routes.list_models → model_catalog.discover_model_catalog(LiteLLM 检测已配置 provider；内置 metadata 筛选 chat+function-calling；custom 走 endpoint discovery/CUSTOM_MODELS) |
| POST /threads | routes.create_thread → ChatRepository.create_thread(SQLite) |
| GET /threads | routes.list_threads → repo.list_threads(按活跃排序) |
| GET /search | routes.search_threads → repo.search_threads(标题+正文 LIKE) |
| GET /threads/{id} | repo.get_thread(404 包装) |
| DELETE /threads/{id} | repo.delete_thread(级联消息) |
| GET /threads/{id}/messages | repo.list_messages |
| POST /threads/{id}/runs/stream | 见 §0 总览 |

## 2. Memory(前缀 /api/memory)

| 端点 | 链路 |
|---|---|
| GET "" | routes → SlotFlowMemoryStore.list_memories(SQLite,updated_at 倒序) |
| POST "" | routes → store.add_memory(normalize=卫生化:剥指令前缀/压空白/补句号/限长;源运行去重+同内容 touch) |
| PATCH /{id} | store.update_memory(同 normalize) |
| DELETE /{id} | store.delete_memory |

Agent 侧同源:工具 memory_save/list/update/delete(app/harness/tools/memory.py)与
prepare 检索(top-5 关键词打分注入)、finalize 显式保存(小模型 rewrite)/后台抽取
(SlotFlowMemoryExtractor,指令串台双重过滤)都落到同一个 store。

## 3. Workspace(前缀 /api/workspace)

| 端点 | 链路 |
|---|---|
| GET /artifacts?path= | routes.list_artifacts → SlotFlowWorkspace.list_entries(逐层目录;path 必须在 artifacts/ 下) |
| GET /artifacts/read?path= | workspace.read(大小限制/媒体类型判定) |
| GET /artifacts/raw?path= | 文件原样流(iframe/img/下载用;download=1 加附件头) |
| DELETE /artifacts?path= | workspace.delete_file |
| GET /threads | list_thread_workspaces:按 thread 分组 uploads+generated(右侧面板下拉数据源) |

Agent 写入唯一入口:artifact_write 工具 → artifacts/<thread_id>/;沙箱容器以
/workspace/artifacts 挂载同一棵树(线程目录隔离见 §6)。

## 4. Uploads(前缀 /api/uploads)

| 端点 | 链路 |
|---|---|
| POST "" | routes.upload → SlotFlowUploadStore.save(清洗文件名/大小限制)→ uploads/ |
| GET /{file_id} | store.get_upload 元数据 |
| GET /{file_id}/raw | 原文件流 |

run 时:stage_upload_for_run 把上传落位为本次 run 可读元数据 → prepare 节点
uploads_update 把工作区路径注入最新用户消息 → 模型用 workspace_read 读取。

## 5. Skills(/api/skills)与 MCP(/api/mcp/servers)

- Skills:GET 列表 / POST /upload(文件夹上传)/ POST /install(package_url+skill_name)
  / PATCH {name}(enabled/pinned)/ POST /reorder / DELETE {name}。
  落到 skills 目录+配置存储;agent 侧 skill_match/find-skills/skill_install 工具与
  prepare 的 skills preflight 共用注册表。
- MCP:GET/POST/PATCH/reorder/DELETE 管理 streamable HTTP server 记录;
  run 前 ensure_mcp_tools_loaded 连接并把 MCP 工具并入 build_harness_tools。

## 6. Terminal(WS /api/terminal/ws)

WebSocket → app/terminal/routes.py:宿主用户终端(PTY),协议
{type: ready|output|input|resize|exit}。这是用户手动终端,与 agent 的 sandbox_exec
(Docker 容器内执行)完全隔离:
sandbox_exec → LazyDockerSandbox(持久具名容器 slotflow-sandbox-<hash>,
守护进程不可达先 ensure_daemon 自动拉起;exec 锁定 /workspace/work/<thread> 并注入
HOME/SLOTFLOW_THREAD_ARTIFACTS;空闲只 stop 不 rm)。

## 7. 前端消费侧(对照)

- 目录/线程/消息/流式:src/lib/chat-stream.ts(fetch+SSE 解析)→
  hooks/use-chat-stream.ts(事件→UI 状态机:delta 批量 flush/tool.status 芯片/
  todo/澄清卡/快照择优)→ components/chat/*。
- 工作区面板:use-workspace-data + WorkspacePanel(GET /api/workspace/threads 下拉、
  /artifacts/read 预览、/artifacts/raw iframe/下载;终端页签连 /api/terminal/ws)。
