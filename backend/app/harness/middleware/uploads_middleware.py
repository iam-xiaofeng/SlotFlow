"""Middleware that makes uploaded workspace files explicit to the model.

Thin delegate to ``app.harness.steps.uploads``.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from app.chat.models import RunContext
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.state import SlotFlowAgentState
from app.harness.steps.uploads import uploads_update


class SlotFlowUploadsMiddleware(AgentMiddleware[SlotFlowAgentState, RunContext]):
    """Inject current-run upload metadata into the latest user message."""

    name = "SlotFlowUploadsMiddleware"

    def __init__(self, *, sandbox_config: SlotFlowSandboxConfig | None = None) -> None:
        self._sandbox_config = sandbox_config

    @override
    def before_agent(
        self,
        state: SlotFlowAgentState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any] | None:
        context = runtime.context
        if context is None or not context.uploaded_files:
            return None
        return uploads_update(
            state=state,
            context=context,
            sandbox_config=self._sandbox_config,
        )
