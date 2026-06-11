"""SlotFlow 聊天后端包。

这个包会逐步承接一条完整的聊天链路：

1. HTTP 请求进来；
2. 请求被整理成 run 配置；
3. agent 产出流式事件；
4. 后端把事件翻译成前端容易消费的 SSE；
5. 仓库保存 thread / message / run 的最终结果。

现在已经收敛到 SlotFlow 自己的 LangGraph harness。测试里的 fake 只留在 tests
边界内，不再作为生产 runtime 的一种模式。
"""
