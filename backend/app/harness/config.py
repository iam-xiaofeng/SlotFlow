"""SlotFlow harness 的最小配置。

这层配置只服务于 agent graph 组装，不读取环境变量，也不直接关心 FastAPI。
环境变量仍由 `chat.runtime` 读取，再转换成这里的显式配置对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SlotFlowHarnessConfig:
    """创建 LangGraph agent graph 需要的最小 harness 配置。"""

    system_prompt: str
    skills_root: Path | None = None
    enabled_skills: set[str] | None = None
