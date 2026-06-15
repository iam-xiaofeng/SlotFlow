"""SlotFlow wrapper around LangChain conversation summarization."""

from __future__ import annotations

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel


SLOTFLOW_SUMMARY_PROMPT = """Summarize the earlier SlotFlow conversation for the next model call.

Keep only durable context: user intent, decisions, created or modified files, tool results,
open questions, and concrete next steps. Preserve exact paths, identifiers, and user
preferences. Omit small talk.

Messages:
{messages}
"""


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
