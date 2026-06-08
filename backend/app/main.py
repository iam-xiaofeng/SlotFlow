from fastapi import FastAPI

from app.chat.agent_adapter import AgentAdapter
from app.chat.repository import ChatRepository, build_chat_repository
from app.chat.routes import router as chat_router
from app.chat.runtime import SlotFlowRuntimeConfig, build_agent_adapter


def create_app(
    *,
    chat_repo: ChatRepository | None = None,
    agent_adapter: AgentAdapter | None = None,
    runtime_config: SlotFlowRuntimeConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用，并把学习链路需要的依赖放到 app.state。

    第一阶段不用依赖注入框架，只用 `app.state` 保存两个对象：

    - `chat_repo`：thread / message / run 的业务仓库；
    - `agent_adapter`：模块四定义的 agent 事件适配器。

    测试可以传入自己的仓库和 adapter。真实运行如果没有传 adapter，就走 SlotFlow
    自己的本地 runtime 装配层：默认仍然是 `static`，但后面可以按同一入口切到
    `deepseek` 或后续本地重写的更真实 harness。
    """

    app = FastAPI(title="SlotFlow API")
    app.state.chat_repo = chat_repo or build_chat_repository()
    app.state.runtime_config = runtime_config
    app.state.agent_adapter = agent_adapter or build_agent_adapter(runtime_config)

    @app.get("/health", tags=["Health Check"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(chat_router)
    return app


app = create_app()
