"""SlotFlow harness graph behavior switches.

重构后（node + edge graph）这些 flag 不再挑选 middleware，而是由 ``app.harness.graph``
的各节点决定是否执行对应 step（见 ``app.harness/steps/*``）。名字保留为
``SlotFlowMiddlewareConfig`` 以维持导入路径与 env 开关兼容。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlotFlowMiddlewareConfig:
    """Feature switches for the SlotFlow harness graph nodes."""

    runtime_summary_enabled: bool = True
    dangling_tool_call_enabled: bool = True
    tool_safety_enabled: bool = True
    artifact_discovery_enabled: bool = True
    summarization_enabled: bool = True
    summarization_trigger_tokens: int = 600000
    summarization_keep_messages: int = 20
    summarization_trim_tokens: int = 8000
    long_term_memory_enabled: bool = True
    proactive_memory_extraction_enabled: bool = True
    skills_preflight_enabled: bool = True
    clarify_gate_enabled: bool = True
    uploads_enabled: bool = True
    todo_enabled: bool = True
    subagent_limit_enabled: bool = True
    subagent_max_concurrent: int = 3
