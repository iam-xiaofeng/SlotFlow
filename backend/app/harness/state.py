"""SlotFlow harness 的 LangGraph state schema。"""

from __future__ import annotations

from typing import NotRequired

from langchain.agents import AgentState


class SlotFlowAgentState(AgentState):
    """当前 harness 的最小 graph state。

    第一版只保留 LangChain agent 默认的 `messages`，并预留一个 `slotflow` 命名空间给后续
    tools / skills / MCP / middleware 写入调试信息。不要把 HTTP 请求体或 SSE 元数据塞到这里。
    """

    slotflow: NotRequired[dict | None]
