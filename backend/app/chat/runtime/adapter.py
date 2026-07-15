"""SlotFlow 本地 agent runtime 的装配核心。

按 `SlotFlowRuntimeConfig` 在每次 run 时创建真实模型、checkpointer 和 LangGraph graph，
并保持路由层依赖的 `AgentAdapter` 边界不变。真实 graph 的组装委托给 `app.harness`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from app.chat.agent_adapter import AgentAdapter, AgentEvent, LangGraphEventAgentAdapter
from app.chat.models import ChatStreamRequest, RunConfigBundle, RunContext
from app.chat.runtime.checkpointer import (
    aclose_checkpointer,
    close_checkpointer,
    create_async_checkpointer,
    create_checkpointer,
)
from app.chat.runtime.config import (
    SlotFlowRuntimeConfig,
    load_runtime_config_from_env,
    refresh_runtime_mcp_config,
    refresh_runtime_skills_config,
)
from app.chat.runtime.models import create_chat_model, create_model_for_context
from app.harness import SlotFlowHarnessConfig, build_slotflow_harness_graph
from app.harness.mcp import ensure_mcp_tools_loaded

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.types import Checkpointer


class RuntimeBackedAgentAdapter:
    """按 SlotFlow 本地 runtime 配置在每次 run 时创建真实 adapter。

    这样可以保留路由层当前依赖的 `AgentAdapter` 边界，同时避免把真实模型、graph、
    checkpointer 选择写死在 `create_app()` 启动阶段。
    """

    def __init__(
        self,
        runtime_config: SlotFlowRuntimeConfig,
        *,
        model_factory: Callable[..., BaseChatModel] | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._model_factory = model_factory or create_chat_model
        self._checkpointer = (
            create_checkpointer(runtime_config)
            if runtime_config.checkpointer_backend in ("none", "memory")
            else None
        )

    def close(self) -> None:
        """关闭 runtime 持有的持久化 checkpointer 连接。"""

        close_checkpointer(self._checkpointer)

    async def aclose(self) -> None:
        """异步关闭 runtime 持有的持久化 checkpointer 连接。"""

        await aclose_checkpointer(self._checkpointer)

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        model_name = bundle.context.model_name or self._runtime_config.model_name
        checkpointer = await self._ensure_checkpointer()
        refresh_runtime_skills_config(self._runtime_config)
        await self._ensure_mcp_tools_loaded()
        graph = create_langgraph_agent_graph(
            model=create_model_for_context(
                self._model_factory,
                model_name=model_name,
                run_context=bundle.context,
            ),
            runtime_config=self._runtime_config,
            run_context=bundle.context,
            checkpointer=checkpointer,
        )
        adapter = LangGraphEventAgentAdapter(graph)

        async for event in adapter.stream_events(request=request, bundle=bundle):
            yield event

    async def _ensure_checkpointer(self) -> Checkpointer | None:
        if self._runtime_config.checkpointer_backend in ("none", "memory"):
            return self._checkpointer
        if self._checkpointer is None:
            self._checkpointer = await create_async_checkpointer(self._runtime_config)
        return self._checkpointer

    async def _ensure_mcp_tools_loaded(self) -> None:
        refresh_runtime_mcp_config(self._runtime_config)
        await ensure_mcp_tools_loaded(
            config=self._runtime_config.mcp_config,
            provider=self._runtime_config.mcp_tool_provider,
        )


def create_langgraph_agent_graph(
    *,
    model: str | BaseChatModel,
    runtime_config: SlotFlowRuntimeConfig,
    run_context: RunContext,
    checkpointer: Checkpointer | None = None,
):
    """创建 SlotFlow 本地的真实 LangGraph agent graph。

    `chat.runtime` 只负责选择运行模式和模型。真实 graph 的组装委托给 `app.harness`，
    这样 tools / skills / MCP / middleware 后续都能收敛到 harness 边界里。
    """

    return build_slotflow_harness_graph(
        model=model,
        run_context=run_context,
        harness_config=SlotFlowHarnessConfig(
            system_prompt=runtime_config.system_prompt,
            skills_root=runtime_config.skills_root,
            enabled_skills=runtime_config.enabled_skills,
            skills_config_store=runtime_config.skills_config_store,
            memory_store=runtime_config.memory_store,
            mcp_config=runtime_config.mcp_config,
            mcp_tool_provider=runtime_config.mcp_tool_provider,
            mcp_config_store=runtime_config.mcp_config_store,
            middleware_config=runtime_config.middleware_config,
            sandbox_config=runtime_config.sandbox_config,
            subagent_config=runtime_config.subagent_config,
        ),
        checkpointer=checkpointer,
    )


def build_agent_adapter(
    runtime_config: SlotFlowRuntimeConfig | None = None,
    *,
    model_factory: Callable[[str], BaseChatModel] | None = None,
) -> AgentAdapter:
    """按 SlotFlow 本地 runtime 配置创建默认 agent adapter。

    返回值仍然满足 `AgentAdapter` 边界，但真实 graph 的创建推迟到每次 run 调用时。
    """

    resolved = runtime_config or load_runtime_config_from_env()
    return RuntimeBackedAgentAdapter(
        resolved,
        model_factory=model_factory,
    )
