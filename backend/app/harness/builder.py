"""SlotFlow harness graph builder。

这里是 LangGraph agent graph 的唯一组装入口。`chat.runtime` 负责选择 static/deepseek
运行模式；一旦需要真实 graph，就把 model、checkpointer、RunContext 显式传给这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents import create_agent

from app.chat.models import RunContext
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.state import SlotFlowAgentState

if TYPE_CHECKING:
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.types import Checkpointer


def build_slotflow_harness_graph(
    *,
    model: str | BaseChatModel,
    run_context: RunContext,
    harness_config: SlotFlowHarnessConfig,
    checkpointer: Checkpointer | None = None,
    tools: list[BaseTool] | None = None,
    middleware: list[AgentMiddleware] | None = None,
):
    """创建 SlotFlow 本地 harness graph。

    模块 10 只落下边界，不急着引入真实 tools/skills/MCP/middleware。后续模块会逐步把
    `tools` 和 `middleware` 从外部测试替身替换成 harness 内部 registry 的输出。
    """

    features = features_from_run_context(run_context)
    selected_tools = list(tools or [])
    selected_middleware = list(middleware or [])

    return _create_agent_graph(
        model=model,
        tools=selected_tools,
        middleware=selected_middleware,
        system_prompt=build_system_prompt(
            harness_config=harness_config,
            features=features,
        ),
        checkpointer=checkpointer,
    )


def build_system_prompt(
    *,
    harness_config: SlotFlowHarnessConfig,
    features: SlotFlowHarnessFeatures,
) -> str:
    """构建第一版 harness system prompt。

    这里先只追加一个很小的 feature 摘要，目的是让测试能证明 `RunContext -> features`
    确实进入了 harness builder。正式 skills prompt 会在模块 12 接入。
    """

    return "\n".join(
        [
            harness_config.system_prompt,
            "",
            "<slotflow-runtime>",
            f"thinking_enabled={features.thinking_enabled}",
            f"plan_enabled={features.plan_enabled}",
            f"subagent_enabled={features.subagent_enabled}",
            "</slotflow-runtime>",
        ]
    )


def _create_agent_graph(
    *,
    model: str | BaseChatModel,
    tools: list[BaseTool],
    middleware: list[AgentMiddleware],
    system_prompt: str,
    checkpointer: Checkpointer | None,
):
    """薄封装 LangChain `create_agent`，方便模块测试 monkeypatch 边界参数。"""

    return create_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        system_prompt=system_prompt,
        state_schema=SlotFlowAgentState,
        context_schema=RunContext,
        checkpointer=checkpointer,
    )
