"""SlotFlow harness 的 LangGraph state schema。

重构后（node + edge 版本）：节点间需要传递的中间数据显式放进 state，而不是靠节点闭包
共享可变实例。保留 `messages`（来自 `AgentState`，含 `add_messages` reducer）、`slotflow`
调试命名空间、`todos`。另加三个节点间通道：

- `llm_input_messages`：`pre_model` 修复/摘要后的消息序列，喂给 `agent`（对齐官方
  `create_react_agent` 的 pre_model_hook 约定）。
- `system_prompt`：`pre_model` 注入长期记忆后的最终 system 段，`agent` 读取。
- `retrieved_memories`：`prepare` 检索到的长期记忆，供 `pre_model` 注入与 `finalize` 引用。
- `artifacts_baseline`：`prepare` 的产物基线快照，供 `finalize` 计算新增产物。
"""

from __future__ import annotations

from typing import Any, NotRequired

from langchain.agents import AgentState
from langchain_core.messages import BaseMessage


class SlotFlowAgentState(AgentState):
    """SlotFlow harness graph state（node + edge 版本）。"""

    slotflow: NotRequired[dict | None]
    todos: NotRequired[list[dict[str, Any]]]
    llm_input_messages: NotRequired[list[BaseMessage] | None]
    system_prompt: NotRequired[str | None]
    retrieved_memories: NotRequired[list[Any] | None]
    artifacts_baseline: NotRequired[set[str] | None]
