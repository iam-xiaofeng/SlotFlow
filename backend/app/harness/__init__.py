"""SlotFlow 本地 harness 边界。

`chat` 包负责 HTTP、仓库、run、SSE；`harness` 包只负责创建 LangGraph agent graph。
后续 tools、skills、MCP、middleware 都应该优先收敛到这里，而不是散落到路由层。
"""

from app.harness.builder import build_slotflow_harness_graph
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.state import SlotFlowAgentState

__all__ = [
    "SlotFlowAgentState",
    "SlotFlowHarnessConfig",
    "SlotFlowHarnessFeatures",
    "SlotFlowMiddlewareConfig",
    "build_slotflow_harness_graph",
    "features_from_run_context",
]
