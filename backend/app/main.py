import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.chat.agent_adapter import AgentAdapter
from app.chat.repository import ChatRepository, build_chat_repository
from app.chat.routes import router as chat_router
from app.chat.runtime import SlotFlowRuntimeConfig, build_agent_adapter
from app.uploads import SlotFlowUploadStore
from app.uploads.routes import router as upload_router


LOCAL_DEV_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def create_app(
    *,
    chat_repo: ChatRepository | None = None,
    agent_adapter: AgentAdapter | None = None,
    runtime_config: SlotFlowRuntimeConfig | None = None,
    upload_store: SlotFlowUploadStore | None = None,
) -> FastAPI:
    """创建 FastAPI 应用，并把学习链路需要的依赖放到 app.state。

    第一阶段不用依赖注入框架，只用 `app.state` 保存两个对象：

    - `chat_repo`：thread / message / run 的业务仓库；
    - `agent_adapter`：模块四定义的 agent 事件适配器。
    - `upload_store`：用户上传文件的 workspace 存储边界。

    测试可以传入自己的仓库和 adapter。真实运行如果没有传 adapter，就走 SlotFlow
    自己的本地 runtime 装配层，创建 LangGraph/DeepSeek-compatible graph。
    """

    app = FastAPI(title="SlotFlow API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=load_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.chat_repo = chat_repo or build_chat_repository()
    app.state.upload_store = upload_store or SlotFlowUploadStore()
    app.state.runtime_config = runtime_config
    app.state.agent_adapter = agent_adapter or build_agent_adapter(runtime_config)

    @app.get("/health", tags=["Health Check"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(chat_router)
    app.include_router(upload_router)
    return app


def load_cors_origins() -> list[str]:
    raw_origins = os.environ.get("SLOTFLOW_CORS_ORIGINS")
    if raw_origins is None:
        return list(LOCAL_DEV_CORS_ORIGINS)

    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app = create_app()
