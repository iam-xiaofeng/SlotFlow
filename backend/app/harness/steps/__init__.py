"""SlotFlow harness graph steps.

重构（2026-06-30，分支 refactor/langgraph-node-edge-graph）把 `create_agent` + middleware
编排迁移到 LangGraph 原生 node + edge graph。每个原中间件的核心逻辑先抽成这里的无状态
纯函数，既能在阶段 A 让中间件薄薄地委托调用（保持行为不变、测试全绿），也能在阶段 B
之后被 graph 节点直接复用。详见 `docs/refactor-plan.md`。
"""
