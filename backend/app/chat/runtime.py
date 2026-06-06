"""SlotFlow 本地 agent runtime 装配层。

这里不直接依赖 DeerFlow 包，而是把“真实 agent 怎么创建、checkpointer 怎么挂进去”
这类运行时装配问题单独收拢成一个更小的本地边界。

当前只实现两种模式：

- `static`：继续使用稳定的 `StaticProjectionAgentAdapter`
- `deepseek`：用 LangChain/LangGraph + DeepSeek OpenAI-compatible API 创建真实 graph

后面如果要继续吸收 DeerFlow 的有价值能力，也应该优先在这里本地重写，而不是把旧项目
整包 import 进来。
"""

from __future__ import annotations

import os
from collections.abc import Callable, AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from langgraph.checkpoint.memory import InMemorySaver

from app.chat.agent_adapter import (
    AgentEvent,
    AgentAdapter,
    LangGraphEventAgentAdapter,
    StaticProjectionAgentAdapter,
)
from app.chat.models import ChatStreamRequest, RunConfigBundle, RunContext
from app.harness import SlotFlowHarnessConfig, build_slotflow_harness_graph

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.types import Checkpointer


RuntimeMode = Literal["static", "deepseek"]
CheckpointerBackend = Literal["none", "memory"]

DEFAULT_DEEPSEEK_SYSTEM_PROMPT = "你是 SlotFlow 的学习版助手，回答要简洁、具体。"


@dataclass(slots=True)
class SlotFlowRuntimeConfig:
    """SlotFlow 本地 runtime 的最小配置。

    先只保留和当前学习链路直接相关的字段，不提前引入 DeerFlow 旧网关里的大而全配置树。
    """

    adapter_mode: RuntimeMode = "static"
    model_name: str = "deepseek-v4-flash"
    checkpointer_backend: CheckpointerBackend = "memory"
    system_prompt: str = DEFAULT_DEEPSEEK_SYSTEM_PROMPT
    prefer_projection_stream: bool = True
    skills_root: Path | None = None
    enabled_skills: set[str] | None = None


class RuntimeBackedAgentAdapter:
    """按 SlotFlow 本地 runtime 配置在每次 run 时创建真实 adapter。

    这样可以保留路由层当前依赖的 `AgentAdapter` 边界，同时避免把真实模型、graph、
    checkpointer 选择写死在 `create_app()` 启动阶段。
    """

    def __init__(
        self,
        runtime_config: SlotFlowRuntimeConfig,
        *,
        model_factory: Callable[[str], BaseChatModel] | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._model_factory = model_factory or (lambda model_name: create_deepseek_chat_model(model_name=model_name))
        self._checkpointer = (
            create_checkpointer(runtime_config)
            if runtime_config.adapter_mode == "deepseek"
            else None
        )

    async def stream_events(
        self,
        *,
        request: ChatStreamRequest,
        bundle: RunConfigBundle,
    ) -> AsyncIterator[AgentEvent]:
        if self._runtime_config.adapter_mode == "static":
            adapter = StaticProjectionAgentAdapter()
        elif self._runtime_config.adapter_mode == "deepseek":
            model_name = bundle.context.model_name or self._runtime_config.model_name
            graph = create_langgraph_agent_graph(
                model=self._model_factory(model_name),
                runtime_config=self._runtime_config,
                run_context=bundle.context,
                checkpointer=self._checkpointer,
            )
            adapter = LangGraphEventAgentAdapter(
                graph,
                prefer_projection_stream=self._runtime_config.prefer_projection_stream,
            )
        else:
            raise ValueError(f"unsupported adapter mode: {self._runtime_config.adapter_mode!r}")

        async for event in adapter.stream_events(request=request, bundle=bundle):
            yield event


def load_runtime_config_from_env() -> SlotFlowRuntimeConfig:
    """从环境变量读取一个很小的 runtime 配置。

    默认仍然使用 `static`，这样本地开发和测试不需要 API key。
    """

    adapter_mode = os.environ.get("SLOTFLOW_AGENT_MODE", "static")
    if adapter_mode not in ("static", "deepseek"):
        raise ValueError(
            "SLOTFLOW_AGENT_MODE must be 'static' or 'deepseek', "
            f"got {adapter_mode!r}",
        )

    checkpointer_backend = os.environ.get("SLOTFLOW_CHECKPOINTER_BACKEND", "memory")
    if checkpointer_backend not in ("none", "memory"):
        raise ValueError(
            "SLOTFLOW_CHECKPOINTER_BACKEND must be 'none' or 'memory', "
            f"got {checkpointer_backend!r}",
        )

    return SlotFlowRuntimeConfig(
        adapter_mode=adapter_mode,
        model_name=os.environ.get("SLOTFLOW_DEEPSEEK_MODEL", "deepseek-v4-flash"),
        checkpointer_backend=checkpointer_backend,
        system_prompt=os.environ.get("SLOTFLOW_SYSTEM_PROMPT", DEFAULT_DEEPSEEK_SYSTEM_PROMPT),
        skills_root=load_optional_path_from_env("SLOTFLOW_SKILLS_ROOT"),
        enabled_skills=load_optional_csv_set_from_env("SLOTFLOW_ENABLED_SKILLS"),
    )


def load_optional_path_from_env(name: str) -> Path | None:
    """从环境变量读取可选路径。"""

    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def load_optional_csv_set_from_env(name: str) -> set[str] | None:
    """从环境变量读取逗号分隔名单。"""

    value = os.environ.get(name)
    if not value:
        return None
    names = {item.strip() for item in value.split(",") if item.strip()}
    return names or None


def create_checkpointer(
    runtime_config: SlotFlowRuntimeConfig,
) -> Checkpointer | None:
    """创建 SlotFlow 当前需要的最小 checkpointer。

    第一版只支持：

    - `none`：完全无状态
    - `memory`：LangGraph 自带 `InMemorySaver`

    这样先把“graph 运行时可以显式拿到 checkpointer”这件事落下来，后面再按 SlotFlow
    自己的节奏扩到 SQLite/Postgres。
    """

    if runtime_config.checkpointer_backend == "none":
        return None
    if runtime_config.checkpointer_backend == "memory":
        return InMemorySaver()
    raise ValueError(f"unknown checkpointer backend: {runtime_config.checkpointer_backend!r}")


def create_deepseek_chat_model(*, model_name: str) -> BaseChatModel:
    """创建 DeepSeek OpenAI-compatible chat model。"""

    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek runtime")

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        streaming=True,
        timeout=30,
        max_retries=0,
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
