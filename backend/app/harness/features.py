"""把一次 run 的业务上下文转换成 harness feature flags。"""

from __future__ import annotations

from dataclasses import dataclass

from app.chat.models import RunContext


@dataclass(frozen=True, slots=True)
class SlotFlowHarnessFeatures:
    """SlotFlow harness 当前认识的功能开关。

    它来自 `RunContext`，但不等同于 `RunContext`。`RunContext` 是本次 run 的业务上下文；
    features 是 harness builder 决定 tools/middleware 时使用的更窄输入。
    """

    thinking_enabled: bool
    plan_enabled: bool
    subagent_enabled: bool


def features_from_run_context(context: RunContext) -> SlotFlowHarnessFeatures:
    """从模块 3 产出的 `RunContext` 提取 harness 功能开关。"""

    return SlotFlowHarnessFeatures(
        thinking_enabled=context.thinking_enabled,
        plan_enabled=context.is_plan_mode,
        subagent_enabled=context.subagent_enabled,
    )
