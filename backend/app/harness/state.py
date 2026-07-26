"""SlotFlow harness 的 LangGraph state schema。

重构后（node + edge 版本）：节点间需要传递的中间数据显式放进 state，而不是靠节点闭包
共享可变实例。保留 `messages`（来自 `AgentState`，含 `add_messages` reducer）、`slotflow`
调试命名空间、`todos`。另加节点间通道：

- `llm_input_messages`：`pre_model` 修复/摘要后的消息序列，喂给 `agent`（对齐官方
  `create_react_agent` 的 pre_model_hook 约定）。
- `system_prompt`：`pre_model` 注入长期记忆后的最终 system 段，`agent` 读取。
- `retrieved_memories`：`prepare` 检索到的长期记忆，供 `pre_model` 注入与 `finalize` 引用。
- `artifacts_baseline`：`prepare` 的产物基线快照，供 `finalize` 计算新增产物。
- `todo_enforcement`：`post_model` 的 todo 约束控制通道（`pending` 指令文本 +
  `attempted` 防循环标记）。
- `model_input_suffix`：`pre_model` 组装的"尾部注入"字符串——召回的长期记忆 + 当步
  todo 控制文本。`agent` 把它包成**用户角色的 `<system-reminder>` 消息**、拼在所有会话消息
  之后。三点好处：(1) 让易变内容离开 `system` 前缀，`tools→system→messages` 这段前缀保持
  逐字节稳定，provider 的前缀缓存才可能命中；(2) 消息序列**始终以 user/tool 结尾**，兼容对
  消息顺序更严格的中转 provider；(3) 和 `system_prompt` 一样只走普通字符串通道，绝不进入持久的
  `messages` 会话历史（messages 投影会把 state 消息流给用户、checkpointer 会永久回放——
  2026-07-15 真机泄漏的根因）。
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from langchain.agents import AgentState
from langchain_core.messages import BaseMessage


def merge_promoted_tool_names(
    existing: list[str] | None,
    incoming: list[str] | None,
) -> list[str]:
    """保序去重地并入 `promoted_tool_names`,支持同一步内的并发写入。

    工具空间加载器(`*_tools`)在模型单步里可能被并行调用(多个 tool_calls),各自返回
    `Command(update={"promoted_tool_names": ...})`。没有 reducer 时 LangGraph 会对第二次写入
    抛 `INVALID_CONCURRENT_GRAPH_UPDATE`("Can receive only one value per step")。工具披露是
    加性的(一个 context epoch 内只增不减),所以用有序并集作为 reducer:既合并并发写入,也
    对重复激活保持幂等。
    """

    merged: list[str] = []
    seen: set[str] = set()
    for name in (*(existing or []), *(incoming or [])):
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return merged


class SlotFlowAgentState(AgentState):
    """SlotFlow harness graph state（node + edge 版本）。"""

    slotflow: NotRequired[dict | None]
    todos: NotRequired[list[dict[str, Any]]]
    llm_input_messages: NotRequired[list[BaseMessage] | None]
    system_prompt: NotRequired[str | None]
    retrieved_memories: NotRequired[list[Any] | None]
    artifacts_baseline: NotRequired[set[str] | None]
    todo_enforcement: NotRequired[dict[str, Any] | None]
    context_epoch: NotRequired[dict[str, Any] | None]
    promoted_tool_names: NotRequired[Annotated[list[str] | None, merge_promoted_tool_names]]
    model_input_suffix: NotRequired[str | None]
