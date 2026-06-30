"""SlotFlow wrapper around LangChain conversation summarization.

Thin delegate to ``app.harness.steps.summarization``.
"""

from __future__ import annotations

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from app.harness.steps.summarization import SLOTFLOW_SUMMARY_PROMPT


class SlotFlowSummarizationMiddleware(SummarizationMiddleware):
    """Compress old messages once the conversation approaches the context limit."""

    def __init__(
        self,
        model: str | BaseChatModel,
        *,
        trigger_tokens: int,
        keep_messages: int,
        trim_tokens_to_summarize: int,
    ) -> None:
        super().__init__(
            model=model,
            trigger=("tokens", trigger_tokens),
            keep=("messages", keep_messages),
            summary_prompt=SLOTFLOW_SUMMARY_PROMPT,
            trim_tokens_to_summarize=trim_tokens_to_summarize,
        )
