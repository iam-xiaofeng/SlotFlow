"""SlotFlow harness middleware package (node+edge refactor).

重构后（2026-06-30，分支 refactor/langgraph-node-edge-graph）agent 编排改为 LangGraph
原生 node + edge graph（见 ``app.harness.graph``），不再使用 ``AgentMiddleware``。
中间件逻辑已抽成 ``app.harness/steps/*`` 无状态纯函数，由图节点直接调用。

本包现在只保留行为开关配置 ``SlotFlowMiddlewareConfig``（graph 节点按这些 flag 决定
是否执行对应 step）。原 ``AgentMiddleware`` 子类与 ``build_harness_middleware`` 注册表
已删除。
"""

from app.harness.middleware.config import SlotFlowMiddlewareConfig

__all__ = ["SlotFlowMiddlewareConfig"]
