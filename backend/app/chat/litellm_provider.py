"""The single boundary between SlotFlow and the LiteLLM packages."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

# SlotFlow owns environment loading. LiteLLM must neither hydrate backend/.env nor
# refresh its model map from GitHub; package upgrades update the bundled catalog.
os.environ["LITELLM_MODE"] = "PRODUCTION"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm  # noqa: E402
from langchain_core.messages import BaseMessage  # noqa: E402
from langchain_litellm import ChatLiteLLM as _ChatLiteLLM  # noqa: E402
from litellm.types.router import LiteLLM_Params  # noqa: E402


CUSTOM_RELAY_USER_AGENT = os.environ.get("SLOTFLOW_RELAY_USER_AGENT") or "SlotFlow/1.0"


class ChatLiteLLM(_ChatLiteLLM):
    """ChatLiteLLM with reasoning metadata removed from outbound content blocks.

    langchain-litellm already forwards the canonical top-level ``reasoning_content``
    field, but its serializer only removes ``thinking`` blocks. LangChain-normalized
    ``reasoning`` blocks can therefore leak into a later provider request even though
    they are metadata, not assistant text. DeepSeek rejects that block type before
    LiteLLM can complete a thinking-mode tool loop.
    """

    def _create_message_dicts(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        message_dicts, params = super()._create_message_dicts(messages, stop)
        for message in message_dicts:
            content = message.get("content")
            if message.get("role") != "assistant" or not isinstance(content, list):
                continue

            filtered_content: list[Any] = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type in {
                        "reasoning",
                        "thinking",
                        "redacted_thinking",
                    }:
                        continue
                    if block_type == "non_standard":
                        value = block.get("value")
                        if isinstance(value, dict) and value.get("type") in {
                            "reasoning",
                            "thinking",
                            "redacted_thinking",
                        }:
                            continue
                filtered_content.append(block)
            message["content"] = filtered_content or ""
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
