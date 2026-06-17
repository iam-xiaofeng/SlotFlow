"""把一次聊天请求整理成 agent 运行配置。

当后端准备调用 agent 时，应该把哪些信息放到哪里？

这里最容易混乱的是 `config` 和 `context`：

- `config["configurable"]["thread_id"]` 更像 LangGraph 用来定位多轮状态的钥匙；
- `context` 更像 SlotFlow 自己的业务说明书，告诉本次运行用什么模型、什么模式、
  是否启用规划和子 agent。

这个模块故意写成纯函数。它不读仓库、不启动 agent、不知道 FastAPI，方便测试专门
保护“字段放置规则”。
"""

from __future__ import annotations

from app.chat.models import (
    ChatMode,
    ChatStreamRequest,
    RunConfigBundle,
    RunContext,
    UploadedFileContext,
)


def mode_to_feature_flags(mode: ChatMode) -> dict[str, bool]:
    """把用户选择的模式翻译成后端运行开关。

    这里先用很简单的三档规则：

    - `flash`：快，少想，不启用规划，不启用子 agent；
    - `pro`：默认档，启用思考和规划，但不启用子 agent；
    - `ultra`：完整档，启用思考、规划和子 agent。

    这些布尔值会映射到具体 middleware 和工具开关。
    """

    return {
        "thinking_enabled": mode != "flash",
        "is_plan_mode": mode in ("pro", "ultra"),
        "subagent_enabled": mode == "ultra",
    }


def request_thinking_enabled(request: ChatStreamRequest) -> bool:
    """Resolve explicit thinking override, preserving mode defaults for old clients."""

    if request.thinking_enabled is not None:
        return request.thinking_enabled
    return mode_to_feature_flags(request.mode)["thinking_enabled"]


def build_run_config(
    *,
    thread_id: str,
    run_id: str,
    request: ChatStreamRequest,
    uploaded_files: list[UploadedFileContext] | None = None,
) -> RunConfigBundle:
    """构建调用 agent stream 时需要的 `config + context`。

    这里有一个项目级约定：`thread_id` 必须进入 `config["configurable"]`。
    后面真实 checkpointer 接上以后，多轮记忆要靠这个位置找到同一条会话。

    其他业务开关放进 `context`，避免把模型名、模式、功能开关都塞进 configurable，
    最后分不清哪些字段是 LangGraph 运行时需要，哪些字段只是 SlotFlow 业务需要。
    """

    flags = mode_to_feature_flags(request.mode)
    resolved_uploaded_files = [
        uploaded_file.model_copy(deep=True)
        for uploaded_file in (uploaded_files or [])
    ]

    return RunConfigBundle(
        config={
            "configurable": {
                "thread_id": thread_id,
            },
        },
        context=RunContext(
            thread_id=thread_id,
            run_id=run_id,
            model_name=request.model_name,
            mode=request.mode,
            agent_name=request.agent_name,
            files=list(request.files),
            uploaded_files=resolved_uploaded_files,
            thinking_enabled=request_thinking_enabled(request),
            is_plan_mode=flags["is_plan_mode"],
            subagent_enabled=flags["subagent_enabled"],
        ),
    )
