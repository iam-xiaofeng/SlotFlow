"""The single boundary between SlotFlow and the LiteLLM packages."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# SlotFlow owns environment loading. LiteLLM must neither hydrate backend/.env nor
# refresh its model map from GitHub; package upgrades update the bundled catalog.
os.environ["LITELLM_MODE"] = "PRODUCTION"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage  # noqa: E402
from langchain_litellm import ChatLiteLLM as _ChatLiteLLM  # noqa: E402
from langchain_litellm.chat_models import litellm as _langchain_litellm_module  # noqa: E402
from litellm.types.router import LiteLLM_Params  # noqa: E402


CUSTOM_RELAY_USER_AGENT = os.environ.get("SLOTFLOW_RELAY_USER_AGENT") or "claude-code/2.1.214"

# Reasoning metadata must never travel as assistant *content*: DeepSeek rejects the
# block types outright and other providers would re-read their own reasoning as
# prose. Opaque reasoning state rides the two top-level carriers LiteLLM
# normalizes for every provider instead: ``reasoning_content`` (text) and
# ``thinking_blocks`` (signed/opaque blocks).
_REASONING_METADATA_BLOCK_TYPES = {"reasoning", "thinking", "redacted_thinking"}


def _thinking_blocks_from_provider_payload(payload: Any) -> list[dict[str, Any]]:
    """Read LiteLLM's typed ``thinking_blocks`` off a response message or delta."""

    if isinstance(payload, dict):
        blocks = payload.get("thinking_blocks")
    else:
        blocks = getattr(payload, "thinking_blocks", None)
    if not isinstance(blocks, list):
        return []
    return [dict(block) for block in blocks if isinstance(block, dict)]


def _convert_dict_to_message_preserving_thinking_blocks(_dict: Any) -> Any:
    message = _upstream_convert_dict_to_message(_dict)
    if isinstance(message, AIMessage):
        blocks = _thinking_blocks_from_provider_payload(_dict)
        if blocks:
            message.additional_kwargs["thinking_blocks"] = blocks
    return message


def _convert_delta_to_message_chunk_preserving_thinking_blocks(
    delta: Any, default_class: Any
) -> Any:
    chunk = _upstream_convert_delta_to_message_chunk(delta, default_class)
    if isinstance(chunk, AIMessageChunk):
        blocks = _thinking_blocks_from_provider_payload(delta)
        if blocks:
            chunk.additional_kwargs["thinking_blocks"] = blocks
        # 故事1「入站清洗」的非流式孪生:推理模型(实测 grok-4.5)把每个思考 delta 也塞进 content 成一个
        # thinking 块,与答案的裸字符串 chunk 交错。思考量一大(带 system 提示时尤甚),langchain 的
        # chunk 合并(``ainvoke`` 内部 / 落库对象 / checkpointer 回放)就把答案压没——content 塌成空串或
        # 混合 list,``sanitize_reasoning_message`` 也无从恢复(正文已在合并阶段丢了)。真机实测:流式逐 token
        # 正文 3/3 正常,但同一问 ``invoke`` 合并 0/3、终答为空。在转换阶段就把思考块从 content 剔除,content
        # 只留纯答案 → 合并变成干净的字符串拼接,``ainvoke``/落库不再丢正文;思考已完整保留在
        # ``additional_kwargs`` 的 ``reasoning_content`` / ``thinking_blocks``,前端思考框走那条 fallback,
        # 流式逐 token 正文不受影响(只删思考块、不动答案)。
        if isinstance(chunk.content, list):
            chunk.content = _without_reasoning_metadata_blocks(chunk.content)
    return chunk


# langchain-litellm 0.7.0 drops LiteLLM's typed ``thinking_blocks`` (the carrier for
# Anthropic/Bedrock signed thinking and Gemini thought state) in both conversion
# directions, which silently disables extended thinking on tool-loop continuations.
# The converters are module globals resolved at call time by
# ChatLiteLLM._stream/_astream/_create_chat_result, so restoring the field requires
# rebinding them here. The dependency is pinned to ==0.7.0 and the provider
# reasoning contract tests drive the real stream path, so an upgrade that moves
# these internals fails loudly instead of silently dropping state again.
_upstream_convert_dict_to_message = _langchain_litellm_module._convert_dict_to_message
_upstream_convert_delta_to_message_chunk = (
    _langchain_litellm_module._convert_delta_to_message_chunk
)
_langchain_litellm_module._convert_dict_to_message = (
    _convert_dict_to_message_preserving_thinking_blocks
)
_langchain_litellm_module._convert_delta_to_message_chunk = (
    _convert_delta_to_message_chunk_preserving_thinking_blocks
)


def _without_reasoning_metadata_blocks(content: list[Any]) -> list[Any] | str:
    """Drop reasoning metadata blocks and re-normalize text-only content to a string.

    Streamed chunk merging leaves assistant text as a BARE string inside the content
    list (``[{"type": "thinking", ...}, "answer"]``). After filtering, ``["answer"]``
    is invalid Chat Completions schema: LiteLLM's DeepSeek transform crashes on the
    non-dict item, strict endpoints reject the request, and lenient relays silently
    drop the text — the model then never sees its own earlier replies (observed as
    lost short-term memory). Text-only content therefore collapses back to a plain
    string; when structural blocks remain, the list keeps its original order with
    bare strings wrapped as text blocks (same semantics as the upstream PR).
    """

    filtered: list[Any] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type in _REASONING_METADATA_BLOCK_TYPES:
                continue
            if block_type == "non_standard":
                value = block.get("value")
                if (
                    isinstance(value, dict)
                    and value.get("type") in _REASONING_METADATA_BLOCK_TYPES
                ):
                    continue
        filtered.append(block)

    has_structural_blocks = any(
        not (
            isinstance(block, str)
            or (isinstance(block, dict) and block.get("type") == "text")
        )
        for block in filtered
    )
    if has_structural_blocks:
        # Empty text items are dropped rather than wrapped: several providers
        # (e.g. Anthropic) reject empty text content blocks outright.
        return [
            {"type": "text", "text": block} if isinstance(block, str) else block
            for block in filtered
            if not _is_empty_text_item(block)
        ]
    return "".join(
        block if isinstance(block, str) else str(block.get("text") or "")
        for block in filtered
    )


def _is_empty_text_item(block: Any) -> bool:
    if isinstance(block, str):
        return not block
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and not (block.get("text") or "")
    )


def _source_thinking_blocks(source: BaseMessage) -> list[Any]:
    additional_kwargs = getattr(source, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict):
        return []
    blocks = additional_kwargs.get("thinking_blocks")
    if isinstance(blocks, list):
        return blocks
    provider_fields = additional_kwargs.get("provider_specific_fields")
    if isinstance(provider_fields, dict):
        fallback = provider_fields.get("thinking_blocks")
        if isinstance(fallback, list):
            return fallback
    return []


def _consolidated_thinking_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    """Collapse streamed thinking-block partials into complete provider blocks.

    LiteLLM's streaming handlers emit an unsigned partial ``thinking`` block per
    reasoning delta and repeat the *full* accumulated text on the block that
    carries the signature, so signed blocks subsume the partials.
    ``redacted_thinking`` blocks arrive complete. Unsigned text is only kept when
    no signed block exists at all; providers that verify signatures drop it
    downstream (`_drop_unsignable_thinking_blocks`).
    """

    complete: list[dict[str, Any]] = []
    unsigned_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "redacted_thinking":
            complete.append({"type": "redacted_thinking", "data": block.get("data") or ""})
        elif block_type == "thinking":
            signature = block.get("signature")
            if isinstance(signature, str) and signature:
                complete.append(
                    {
                        "type": "thinking",
                        "thinking": block.get("thinking") or "",
                        "signature": signature,
                    }
                )
            elif isinstance(block.get("thinking"), str) and block["thinking"]:
                unsigned_parts.append(block["thinking"])
    if complete:
        return complete
    if unsigned_parts:
        return [{"type": "thinking", "thinking": "".join(unsigned_parts)}]
    return []


def repair_streamed_tool_call_names(message: BaseMessage) -> BaseMessage:
    """Backfill tool-call names lost by streamed chunk assembly from the raw payload.

    Some OpenAI-compatible relays (observed live: grok-4.5 via a custom relay) stream tool-call
    deltas such that langchain's parsed ``message.tool_calls[i]["name"]`` comes out EMPTY while the
    raw ``additional_kwargs["tool_calls"][j]["function"]["name"]`` still carries the real name. An
    empty name makes the ToolNode unable to dispatch the call — it fails closed and the call
    surfaces as an unknown-tool error. Recover the name by matching call
    ``id`` (with a strict positional fallback only when every name is missing and counts line up),
    so the model's intended tool actually runs. No-op when names are already present.
    """

    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return message
    raw = message.additional_kwargs.get("tool_calls") if isinstance(message, BaseMessage) else None
    raw = raw if isinstance(raw, list) else []
    name_by_id: dict[str, str] = {}
    names_in_order: list[str] = []
    for entry in raw:
        function = entry.get("function") if isinstance(entry, dict) else None
        raw_name = function.get("name") if isinstance(function, dict) else None
        if isinstance(raw_name, str) and raw_name:
            names_in_order.append(raw_name)
            entry_id = entry.get("id")
            if isinstance(entry_id, str) and entry_id:
                name_by_id[entry_id] = raw_name
    if not names_in_order:
        return message
    missing = [tool_call for tool_call in tool_calls if not tool_call.get("name")]
    allow_positional = len(missing) == len(names_in_order) == len(tool_calls)
    for position, tool_call in enumerate(tool_calls):
        if tool_call.get("name"):
            continue
        recovered = name_by_id.get(tool_call.get("id"))
        if not recovered and allow_positional:
            recovered = names_in_order[position]
        if recovered:
            tool_call["name"] = recovered
    return message


# ``reasoning_content`` 默认**保留**在落库的 assistant 消息上(2026-08-14 改)。
#
# 之前是白名单:只有原生 DeepSeek 保留,其余全剥,理由是"OpenAI 系推理模型会自己重新推理,
# 回喂 CoT 是纯浪费 token"。这个理由在**省 token** 这个维度上仍然成立,但它有两个代价:
#
# 1. DeepSeek 的契约是硬性的——LiteLLM 的 DeepSeek transform(``_fill_reasoning_content``)
#    需要每条历史 assistant 消息都带这个字段,缺了就喂给模型一段空推理链。走中转时
#    ``provider`` 是 ``"custom"``,白名单认不出它背后其实是 DeepSeek,于是**该保留的被剥了**。
# 2. 剥掉之后 checkpoint 里没有,``llm_input_messages``(从 checkpoint 投影)自然也没有,
#    模型连同一轮里自己上一步想过什么都看不到。
#
# 现在默认保留,只对明确会因此报错的 provider 走剥离(目前为空:没有实测到这样的 provider)。
# 想恢复"省 token 优先"的旧行为,设 SLOTFLOW_STRIP_REASONING_PROVIDERS="grok,openai,glm"。
#
# 注意这条闸**只管 ``additional_kwargs`` 里的载体**。content 里的 reasoning/thinking 块
# 仍然一律剥掉——那是线路非法(OpenAI 风格的 Chat Completions 会回
# ``unknown variant 'reasoning'`` → 400)且是体积大头,与本开关无关。
_STRIP_REASONING_PROVIDERS_ENV = "SLOTFLOW_STRIP_REASONING_PROVIDERS"


def _strip_reasoning_providers() -> frozenset[str]:
    raw = os.getenv(_STRIP_REASONING_PROVIDERS_ENV, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _provider_requires_reasoning_roundtrip(provider: str | None) -> bool:
    """True if ``reasoning_content`` should be kept on persisted assistant messages.

    默认全部保留;只有在 ``SLOTFLOW_STRIP_REASONING_PROVIDERS`` 里点名的 provider 才剥。
    """

    stripped = _strip_reasoning_providers()
    if not stripped:
        return True
    if not provider:
        return True
    return provider.split("/", 1)[0].strip().lower() not in stripped


# 可重试的基础设施抖动:限流 / 超时 / 连接 / 5xx。撞上它们不该被 tool_safety 转成模型可见的
# tool_execution_error——尤其 task_tool 子代理内部再调模型时的限流,会让父 agent 把"限流"误当成
# "子任务本身失败"(改写任务重试纯浪费,或放弃并编一个结论),而且那条假失败会永久留在历史里、后
# 续每轮回读一次。改为向上重抛:让整轮像 agent 节点里的瞬时错误一样干净失败、state 不落假 ToolMessage,
# 用户重发即可。只认 litellm 抛的这几类真·瞬时错误(不含 BadRequestError/400 这类永久错),因此普通
# 工具(如 web_fetch 自己把 429 放进结果 dict、并不抛 litellm 异常)不受影响,精准只覆盖"工具内部再调模型"。
_RETRYABLE_INFRA_EXCEPTIONS: tuple[type[BaseException], ...] = (
    litellm.Timeout,
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
)


def is_retryable_infra_error(error: BaseException) -> bool:
    """瞬时基础设施错误(限流/超时/连接/5xx)判定,供 tool_safety 分类:命中→重抛而非转 ToolMessage。"""

    if isinstance(error, BaseExceptionGroup):
        return any(is_retryable_infra_error(exc) for exc in error.exceptions)
    return isinstance(error, _RETRYABLE_INFRA_EXCEPTIONS)


def sanitize_reasoning_message(
    message: BaseMessage, *, provider: str | None = None
) -> BaseMessage:
    """Collapse streamed reasoning out of the PERSISTED assistant message (inbound boundary).

    Reasoning models streamed through a relay (observed live: grok-4.5 via a custom relay) return
    their chain-of-thought as a list of hundreds of per-token ``{"type": "thinking", ...}`` blocks in
    ``content`` plus the full text in ``additional_kwargs["reasoning_content"]``. langchain-litellm
    keeps both on the AIMessage. The OUTBOUND serializer (``_create_message_dicts``) already strips the
    block list to a plain string for the wire, so the provider payload is fine — but the raw message is
    what gets checkpointed and re-projected every turn. Left untouched it inflates ``llm_input_messages``,
    the token count that fires summarization, LangSmith's recorded input, and — because summarization
    keeps the recent messages verbatim — the compacted input never actually shrinks (the reported
    "summary added but nothing was pruned").

    Normalizing here, once, at the point the message enters state fixes all of those at the root:

    * ``content`` block list → the answer string (via the same collapse used outbound), so nothing but
      answer text is persisted. **Always applied** — the block list is wire-illegal for OpenAI-style Chat
      Completions (``unknown variant 'reasoning'`` → 400) and is the main bloat source.
    * ``reasoning_content`` (the single top-level carrier) is **kept by default** (2026-08-14). It used to
      be dropped for everything except native DeepSeek, on the "OpenAI-style reasoners re-reason anyway,
      so echoing the CoT is pure backwash" argument. That argument still holds for token economy, but the
      allowlist keyed off ``run_context.model_provider`` — and a DeepSeek reached through an
      OpenAI-compatible relay reports ``"custom"``, so the one provider that genuinely requires the field
      back was the one getting it stripped. Keeping it also means the checkpoint (and therefore
      ``llm_input_messages``, which is projected from it) carries the reasoning chain, so the model can
      see what it already worked out. Opt back into stripping per provider with
      ``SLOTFLOW_STRIP_REASONING_PROVIDERS="grok,openai,glm"``.

    Signed ``thinking_blocks`` are deliberately preserved: Anthropic/Bedrock extended-thinking tool loops
    require the signed block on the continuation request. No-op for plain string content without reasoning.
    """

    content = getattr(message, "content", None)
    if isinstance(content, list):
        message.content = _without_reasoning_metadata_blocks(content)
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict) and not _provider_requires_reasoning_roundtrip(provider):
        additional_kwargs.pop("reasoning_content", None)
    return message


class ChatLiteLLM(_ChatLiteLLM):
    """ChatLiteLLM with lossless reasoning-state handling at the request boundary.

    Outbound assistant ``content`` carries only text/media. langchain-litellm
    already forwards the top-level ``reasoning_content`` carrier but its
    serializer only removes ``thinking`` blocks, so LangChain-normalized
    ``reasoning`` blocks leak into provider requests (DeepSeek rejects the block
    type before LiteLLM can complete a thinking-mode tool loop), and it drops the
    ``thinking_blocks`` carrier entirely (LiteLLM then silently disables extended
    thinking on Anthropic tool-loop continuations). This subclass strips the
    metadata blocks from content and restores ``thinking_blocks`` — only ever
    echoing back what the provider itself produced, with no per-provider
    branches.
    """

    retry_min_wait_seconds: int = 4
    retry_max_wait_seconds: int = 30
    retry_multiplier_seconds: int = 1

    def _slotflow_retry_decorator(self):
        transient = (
            litellm.Timeout,
            litellm.APIError,
            litellm.APIConnectionError,
            litellm.RateLimitError,
        )
        return retry(
            retry=retry_if_exception_type(transient),
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(
                multiplier=self.retry_multiplier_seconds,
                min=self.retry_min_wait_seconds,
                max=self.retry_max_wait_seconds,
            ),
            reraise=True,
        )

    def completion_with_retry(self, run_manager=None, **kwargs: Any) -> Any:
        """Retry only transient pre-response failures with configurable backoff."""

        del run_manager

        @self._slotflow_retry_decorator()
        def invoke(**call_kwargs: Any) -> Any:
            return self.client.completion(**call_kwargs)

        return invoke(**kwargs)

    async def acompletion_with_retry(self, run_manager=None, **kwargs: Any) -> Any:
        """Async counterpart; stream-iteration failures are intentionally not replayed."""

        del run_manager

        @self._slotflow_retry_decorator()
        async def invoke(**call_kwargs: Any) -> Any:
            return await self.client.acompletion(**call_kwargs)

        return await invoke(**kwargs)

    def _create_message_dicts(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        message_dicts, params = super()._create_message_dicts(messages, stop)
        for source, message in zip(messages, message_dicts):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, list):
                message["content"] = _without_reasoning_metadata_blocks(content)
            thinking_blocks = _consolidated_thinking_blocks(
                _source_thinking_blocks(source)
            )
            if thinking_blocks:
                message["thinking_blocks"] = thinking_blocks
        return message_dicts, params


def canonical_model_id(provider: str, model_id: str) -> str:
    """Return LiteLLM's provider-qualified model id."""

    prefix = f"{provider}/"
    return model_id if model_id.startswith(prefix) else f"{prefix}{model_id}"


def configured_native_provider_names() -> tuple[str, ...]:
    """Return providers LiteLLM detects from the current process environment."""

    valid_models = set(litellm.get_valid_models(check_provider_endpoint=False))
    return tuple(
        sorted(
            provider
            for provider, models in litellm.models_by_provider.items()
            if (
                provider != "custom"
                and valid_models.intersection(models)
                and agent_models_for_provider(provider)
            )
        )
    )


@lru_cache(maxsize=None)
def agent_models_for_provider(provider: str) -> tuple[str, ...]:
    """Return catalog models usable by SlotFlow's tool-calling chat loop."""

    model_ids: set[str] = set()
    for raw_model_id in litellm.models_by_provider.get(provider, []):
        model_id = canonical_model_id(provider, raw_model_id)
        try:
            model_info = litellm.get_model_info(model_id)
            if model_info.get("mode") != "chat":
                continue
            if not litellm.supports_function_calling(model_id):
                continue
        except Exception:  # noqa: BLE001 - one stale upstream entry must not break the catalog
            continue
        model_ids.add(model_id)
    return tuple(sorted(model_ids))


@lru_cache(maxsize=None)
def supports_reasoning_effort(model_id: str) -> bool:
    """Use LiteLLM metadata to detect its unified reasoning control."""

    try:
        supported = litellm.get_supported_openai_params(model=model_id) or []
    except Exception:  # noqa: BLE001 - unknown models keep their provider default
        return False
    return "reasoning_effort" in supported


def infer_provider(model_id: str) -> str:
    """Resolve a provider from a LiteLLM-qualified model id."""

    return litellm.get_llm_provider(model=model_id)[1]


def discover_custom_openai_models(
    *,
    api_key: str,
    api_base: str,
) -> tuple[str, ...]:
    """Ask LiteLLM to discover models from one OpenAI-compatible endpoint."""

    params = LiteLLM_Params(
        model="",
        api_key=api_key,
        api_base=api_base,
        extra_headers={"User-Agent": CUSTOM_RELAY_USER_AGENT},
    )
    models = litellm.get_valid_models(
        check_provider_endpoint=True,
        custom_llm_provider="openai",
        litellm_params=params,
    )
    return tuple(
        sorted(
            {
                model_id.removeprefix("openai/")
                for model_id in models
                if isinstance(model_id, str) and model_id
            }
        )
    )
