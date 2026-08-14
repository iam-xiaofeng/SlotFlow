"""Step: conversation summarization before the model call when context is large.

This step wraps LangChain's ``SummarizationMiddleware`` so the node-based graph can reuse the
official ``RemoveMessage`` + ``lc_source="summarization"`` tagging logic instead of reimplementing
it. The middleware is the one component kept (as a delegated helper) because it already
implements the fragile token-counting / keep-recent / AI-Tool pairing logic correctly.

压缩会把 Skill 正文(几 KB 的 ``skill_read`` 工具结果)整段折叠掉,模型很容易在压缩之后
"忘了自己已经按某个 Skill 的流程在做事"。所以压缩时带一份 **Skills 台账**:哪些 Skill 的
正文读过、正文去哪了、需要时怎么拿回来。台账是双保险——

1. 注进摘要 prompt,让摘要模型把它写进摘要正文(自然语言,模型最容易照做);
2. 压缩视图末尾再追加一行**确定性**台账(不依赖模型听话,漏写也还在)。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

SLOTFLOW_SUMMARY_PROMPT = """Summarize the earlier SlotFlow conversation for the next model call.

Keep only durable context: user intent, decisions, created or modified files, tool results,
open questions, and concrete next steps. Preserve exact paths, identifiers, and user
preferences. Omit small talk. The complete earlier message/tool history remains available through
context_archive_search and context_archive_read; explicitly remind the agent to use those
tools when exact older details are needed.
{skills_ledger}
Messages:
{messages}
"""

SKILLS_LEDGER_PROMPT_BLOCK = """
Skills already loaded in this conversation (their SKILL.md bodies were read with skill_read and
are being dropped from the compacted view): {names}.
You MUST carry this list into the summary verbatim, and state that the full instructions can be
re-read at any time with skill_read(name), or recovered from the original tool result with
context_archive_search / context_archive_read. Also keep any commitment already made to follow
one of these Skills — losing that is how a run silently abandons a Skill halfway through.
"""

SKILLS_LEDGER_MESSAGE = (
    "<slotflow-skills-ledger>\n"
    "Skills whose SKILL.md was already read in this conversation: {names}.\n"
    "Their full text is no longer in this compacted view. If you are following one of them, "
    "call skill_read(name) again to reload the exact instructions before continuing; use "
    "context_archive_search / context_archive_read to recover the original tool result and "
    "anything else the summary dropped. Do not re-derive a Skill's procedure from memory.\n"
    "</slotflow-skills-ledger>"
)


def format_skills_ledger_message(used_skills: Sequence[str]) -> str | None:
    """Build the deterministic ledger block appended to the compacted model view."""

    names = _clean_names(used_skills)
    if not names:
        return None
    return SKILLS_LEDGER_MESSAGE.format(names=", ".join(names))


def build_summarization_middleware(
    model: str | BaseChatModel,
    *,
    trigger_tokens: int,
    keep_messages: int,
    trim_tokens_to_summarize: int,
    used_skills: Sequence[str] = (),
) -> SummarizationMiddleware:
    """Build the official SummarizationMiddleware with SlotFlow's prompt."""

    names = _clean_names(used_skills)
    ledger = (
        SKILLS_LEDGER_PROMPT_BLOCK.format(names=", ".join(names)) if names else ""
    )
    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", trigger_tokens),
        keep=("messages", keep_messages),
        summary_prompt=SLOTFLOW_SUMMARY_PROMPT.format(
            skills_ledger=ledger,
            messages="{messages}",
        ),
        trim_tokens_to_summarize=trim_tokens_to_summarize,
    )


def _clean_names(used_skills: Sequence[str]) -> list[str]:
    names: list[str] = []
    for name in used_skills or ():
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names
