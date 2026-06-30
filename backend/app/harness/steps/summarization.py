"""Step: conversation summarization before the model call when context is large.

This step wraps LangChain's ``SummarizationMiddleware`` so the node-based graph can reuse the
official ``RemoveMessage`` + ``lc_source="summarization"`` tagging logic instead of reimplementing
it. The middleware is the one component kept (as a delegated helper) because it already
implements the fragile token-counting / keep-recent / AI-Tool pairing logic correctly.
"""

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


def build_summarization_middleware(
    model: str | BaseChatModel,
    *,
    trigger_tokens: int,
    keep_messages: int,
    trim_tokens_to_summarize: int,
) -> SummarizationMiddleware:
    """Build the official SummarizationMiddleware with SlotFlow's prompt."""

    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", trigger_tokens),
        keep=("messages", keep_messages),
        summary_prompt=SLOTFLOW_SUMMARY_PROMPT,
        trim_tokens_to_summarize=trim_tokens_to_summarize,
    )
