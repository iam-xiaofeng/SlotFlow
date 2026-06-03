"""SlotFlow 聊天后端包。

这个包会逐步承接一条完整的聊天链路：

1. HTTP 请求进来；
2. 请求被整理成 run 配置；
3. agent 产出流式事件；
4. 后端把事件翻译成前端容易消费的 SSE；
5. 仓库保存 thread / message / run 的最终结果。

现在先从最小但清晰的内存实现开始。等这些边界稳定以后，再把内部的
fake agent 换成真实 DeerFlow harness。
"""
