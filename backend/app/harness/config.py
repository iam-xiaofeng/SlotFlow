"""SlotFlow harness 的最小配置。

这层配置只服务于 agent graph 组装，不读取环境变量，也不直接关心 FastAPI。
环境变量仍由 `chat.runtime` 读取，再转换成这里的显式配置对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.harness.mcp import SlotFlowMcpConfig
from app.harness.middleware import SlotFlowMiddlewareConfig
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.subagents import SlotFlowSubagentConfig
from app.harness.tools.agent_reach import SlotFlowAgentReachConfig
from app.harness.tools.markitdown import SlotFlowMarkItDownConfig

if TYPE_CHECKING:
    from app.harness.memory import SlotFlowMemoryStore
    from app.harness.mcp import McpToolProvider, SlotFlowMcpConfigStore
    from app.harness.skills import SlotFlowSkillsConfigStore


@dataclass(slots=True)
class SlotFlowHarnessConfig:
    """创建 LangGraph agent graph 需要的最小 harness 配置。"""

    system_prompt: str
    skills_root: Path | None = None
    enabled_skills: set[str] | None = None
    skills_config_store: SlotFlowSkillsConfigStore | None = None
    memory_store: SlotFlowMemoryStore | None = None
    mcp_config: SlotFlowMcpConfig = field(default_factory=SlotFlowMcpConfig)
    mcp_tool_provider: McpToolProvider | None = None
    mcp_config_store: SlotFlowMcpConfigStore | None = None
    middleware_config: SlotFlowMiddlewareConfig = field(default_factory=SlotFlowMiddlewareConfig)
    sandbox_config: SlotFlowSandboxConfig = field(default_factory=SlotFlowSandboxConfig)
    agent_reach_config: SlotFlowAgentReachConfig = field(default_factory=SlotFlowAgentReachConfig)
    markitdown_config: SlotFlowMarkItDownConfig = field(default_factory=SlotFlowMarkItDownConfig)
    subagent_config: SlotFlowSubagentConfig = field(default_factory=SlotFlowSubagentConfig)
