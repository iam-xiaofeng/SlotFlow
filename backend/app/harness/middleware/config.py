"""SlotFlow harness middleware configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlotFlowMiddlewareConfig:
    """Feature switches for SlotFlow-owned agent middleware."""

    runtime_summary_enabled: bool = True
    tool_safety_enabled: bool = True
    long_term_memory_enabled: bool = True
