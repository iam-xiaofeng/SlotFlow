"""SlotFlow harness graph builder。

这里是 LangGraph agent graph 的唯一组装入口。`chat.runtime` 负责选择 static/deepseek
运行模式；一旦需要真实 graph，就把 model、checkpointer、RunContext 显式传给这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel

from app.chat.models import RunContext
from app.harness.config import SlotFlowHarnessConfig
from app.harness.features import SlotFlowHarnessFeatures, features_from_run_context
from app.harness.middleware import build_harness_middleware
from app.harness.skills import build_skills_prompt, load_enabled_skills
from app.harness.state import SlotFlowAgentState
from app.harness.tools import build_harness_tools

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
    selected_tools = maybe_disable_tools_for_model(
        model=model,
        tools=build_harness_tools(
            features=features,
            extra_tools=tools,
            mcp_config=harness_config.mcp_config,
            mcp_tool_provider=harness_config.mcp_tool_provider,
            sandbox_config=harness_config.sandbox_config,
        ),
    )

    selected_middleware = build_harness_middleware(
        features=features,
        config=harness_config.middleware_config,
        extra_middleware=middleware,
    )

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


def maybe_disable_tools_for_model(
    *,
    model: str | BaseChatModel,
    tools: list[BaseTool],
) -> list[BaseTool]:
    """如果模型不支持 tool binding，就不要把工具传给 `create_agent`。

    真实 DeepSeek/OpenAI chat model 支持 `bind_tools()`。LangChain 的部分 fake model 只用于
    普通文本测试，没有实现 tool binding；对这些模型强行传工具会在 graph 执行时失败。
    """

    if not tools:
        return []
    if isinstance(model, str):
        return tools
    if type(model).bind_tools is BaseChatModel.bind_tools:
        return []
    return tools


def build_system_prompt(
    *,
    harness_config: SlotFlowHarnessConfig,
    features: SlotFlowHarnessFeatures,
) -> str:
    """构建第一版 harness system prompt。

    这里先只追加一个很小的 feature 摘要，目的是让测试能证明 `RunContext -> features`
    确实进入了 harness builder。正式 skills prompt 会在模块 12 接入。
    """

    enabled_skills = load_enabled_skills(
        skills_root=harness_config.skills_root,
        enabled_names=harness_config.enabled_skills,
    )
    sections = [
        harness_config.system_prompt,
        "",
        "<slotflow-runtime>",
        f"thinking_enabled={features.thinking_enabled}",
        f"plan_enabled={features.plan_enabled}",
        f"subagent_enabled={features.subagent_enabled}",
        "</slotflow-runtime>",
    ]
    skills_prompt = build_skills_prompt(enabled_skills)
    if skills_prompt:
        sections.extend(["", skills_prompt])
    return "\n".join(sections)


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
