# 全库大扫除:改动与发现问题总结(2026-07-03 ~ 07-04)

> 原始任务:对话 `13a9eb55` 的全库大扫除(前后端/测试的过时代码、治标补丁、兼容冗余;
> langchain/langgraph 内置替换;提示词与链路加固;前端交互优化;真实模型全链路验证)。
> 审计原始material见 `SUBAGENT_AUDIT_REPORT_20260703.md`;工程细节见 `HARNESS_NOTES.md` §32;
> API 链路见 `docs/api-call-chains.md`。姊妹文档缺一不可。

## 一、发现并修复的真 bug

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | 摘要一触发,下一次模型调用即崩(OpenAI 兼容/DeepSeek) | RemoveMessage 哨兵列表被写进无 reducer 的 llm_input_messages,原样喂模型 | summarize 写投影前剥哨兵;messages 通道保持 reducer 协议(e0b1c55) |
| 2 | 摘要后模型永远看不见新消息/新工具结果 | llm_input_messages 偶发写入、无人清理、被 checkpoint 持久化 | pre_model 每步从 messages 重算投影(官方 pre_model_hook 约定)(e0b1c55) |
| 3 | 工具执行对前端完全隐形(仅剩静止"思考中") | v3 顶层 tool_calls 投影通道 live 从不产出,历史上 sandbox 提示也从未真正出现过 | tool.status 改挂消息子流 .tool_calls 投影,每消息每工具播报一次;推广到全部工具(带中文文案表);FE 正文恢复时清滞留芯片(ed39d9b+08679ae) |
| 4 | 右侧面板文件预览"只能显示一点点、不能滚动";终端只占上半 | 两个内容区包装 div 不是 flex 容器,内部 flex-1 高度约束断裂 | 包装 div 补 flex flex-col(ed39d9b) |
| 5 | 新对话打开面板出现上一个对话的产物 | 面板兜底"选全局第一个文件"的 effect + 切线程不清选中 | 删兜底 effect;外部选中清空同步清面板;切换/新建/删除对话清 selectedArtifactPath(ed39d9b) |
| 6 | agent"硬是用不了 Docker 也装不了" | 本 WSL 无 systemd→dockerd 从未被拉起,check/install 全依赖 systemctl 死路;外加 Docker Hub 直连超时 | ensure_daemon 三级回退自动拉起(systemctl→service→sudo -n dockerd)+check 自愈+action="start";环境侧 wsl.conf 启用 systemd、daemon.json 配国内镜像、预拉 python:3.12(0e216d4+主机配置) |
| 7 | 纯 reasoning 消息被 repr 成 `[{'type': 'reasoning'…}]` 当正文展示 | 正文通道 repr 兜底 | 正文通道禁止 repr,空即空;reasoning 走专属通道(a008ca3) |
| 8 | `<slotflow-…>` 内部注入块被模型复读进用户可见回复 | 无剥离层 | strip_slotflow_context_blocks(流式与持久化两侧)(a008ca3) |
| 9 | 测试指令(如"不要使用工具")被存成长期记忆,无关对话串台 | 抽取提示词无禁令、无后置过滤 | 提示词禁令+_INSTRUCTION_MARKERS 硬过滤(0e216d4) |
| 10 | 工具态芯片在模型恢复输出后仍显示 running 到 run 结束 | FE 无清除时机 | 首个正文 delta 到达即清(ed39d9b) |

## 二、删除的死代码/冗余(净减约 500 行)

- **后端整删**:`chat/message_utils.py`(131行,零引用);`repair_model_request`;
  clarify_gate 的 GraphBubbleUp 再导出 shim;`dangling_tool_call_enabled`/
  `tool_safety_enabled` 两个无读者开关(连 env 装配与测试);skills_preflight 的
  `uses_default_finder` 测试特化分支;`build_turn_memory_content` 兼容 wrapper;
  append_memory_system_message 的 None 分支;make_agent_node 冗余 base_system;
  记忆库正则语义改写层(~120行,详见 §四)。
- **前端整删**:`chat-sidebar-context.tsx`(零 importer);artifact-panel 的
  `ArtifactWorkspacePanel`/`ArtifactWorkspaceToolbar` 及其唯一消费者 `ui/select.tsx`;
  chat-app 的 artifactPreview 三态与重复网络预取;useChatStream 的
  events/appendEvent/startNewThread/maxEventLogItems;chat-stream 的 getThread;
  parseTodos 的 text 兜底。
- **测试**:`test_subagent_limit.py` 整删(与 test_harness_steps 1:1 重复);
  `_ctx` 死帮手;builder 提示词逐字断言改结构锚点(10+ 句英文措辞钉死→区块标签/
  工具名锚点);摘要夹具名对齐真实节点名。

## 三、langchain/langgraph 内置替换(依赖已最新,无需升级)

- `langgraph._internal._runnable.RunnableCallable` → 官方 `RunnableLambda`
  (agent 节点本就不用 runtime 参数;顺带消掉 RunnableConfig UserWarning 噪音 21→3)。
- 摘要沿用官方 SummarizationMiddleware,修好胶水契约(§一#1#2)。
- 评估后**保留手写**(无内置等价):dangling 修复、子代理并行削峰、task_tool、
  clarify 前置门(HITL 中间件只能拦已发射的 tool call)、write_todos 的 text 别名。

## 四、记忆链路 LLM 化(设计决策)

- 判定归属:**前置澄清**=小模型(已有)+确定性快通道;**检索**=不加阻塞式前置判定,
  注入 top-5 摘要+模型工具自取("给他看一部分,要更多自己拿");**保存**=LLM 唯一
  语义改写者,store 只做卫生化(剥指令前缀/压空白/补句号/限长)。
- 显式"请记住X":正则只探测指令,内容交小模型 rewrite(真机:extraction=llm_rewrite),
  模型不可用回退存原文;显式保存后跳过同轮后台抽取防近似重复。
- 根因修复:指令短语截断(旧正则从"长期记忆"处截断漏出"中记住事实:"残片,
  下游曾为此打 ^中 补丁)→ 触发正则完整吞掉指令短语,补丁删除。

## 五、Docker 沙箱重设计(与用户共同决策)

- **一个持久具名共享容器**(`slotflow-sandbox-<workspace哈希>`):镜像单份、
  已装依赖跨对话保留、空闲只 stop 不 rm(磁盘最省;增长只来自实际安装内容)。
- **线程目录隔离防串台**:exec 锁定 `-w /workspace/work/<thread>`,注入
  HOME 与 SLOTFLOW_THREAD_ARTIFACTS 指向本线程产物目录。
- 守护进程自愈:不可达先 ensure_daemon(三级回退)再重试;check 自动拉起。
- 真机验证:容器执行 `python -c "print(6*7)"` → 42 回传 ✓。

## 六、前端 UI/交互

- 右侧面板常驻入口(聊天区右上角"面板/终端"两按钮)——无产物时终端也可直达;
  跨对话打开文件先 confirm(明示清空未发送附件/排队消息);目录函数名与 prop 改名
  (onOpenArtifacts→onOpenWorkspaceDirectory);requestedMode 外控页签。
- 终端外壳精简:去 Host terminal 标题/cwd/底部提示整行,重连入顶栏,画布拉满。
- 活动指示:思考=扫光文字+超3秒计秒;工具运行=三根细均衡条;上下文压缩/思考卡
  同步换用;prefers-reduced-motion 降级(替代千篇一律的三个点,简洁无AI味)。

## 七、真机全链路验证(用户要求的严格校验)

`scratch/harness/probe_full_chain.py` 用 backend/.env 真实模型模拟前端每一步:
事件契约/流式与落库一致性/思考不漏正文/标题生成/多轮状态/显式记忆(llm_rewrite)/
artifact 落盘可读/沙箱容器执行回传/澄清门触发与恢复——**最终 46/46 全 PASS**。
复跑方式见文件头注释。

## 八、已知剩余事项

- ToolAwareFake* 假模型七处定义可归并到共享 helpers(测试基建,未动)。
- backend/.env 内残留已删除开关 `SLOTFLOW_TOOL_SAFETY_MIDDLEWARE`(读取已移除,
  留着无害,建议手动清)。
- 流程规约:多会话(Windows/WSL)不可同时写仓库,交接经 HANDOFF 文件
  (踩踏事故与恢复记录见 HARNESS_NOTES §32.7)。
