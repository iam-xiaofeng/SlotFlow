# 上游提交记录：reasoning 状态往返（状态：已提交，2026-07-15）

> **已发布**：
> - Issue: https://github.com/langchain-ai/langchain-litellm/issues/222
> - PR（Fixes #222，来自 fork `iam-xiaofeng/langchain-litellm` 分支 `fix/reasoning-roundtrip`）:
>   https://github.com/langchain-ai/langchain-litellm/pull/223 —— 上游 CI 全绿
>   （Python 3.10-3.13 测试、format、lockfile、CodeQL）。
> - PR 内容：请求侧过滤 `reasoning`/`non_standard` 包装、text-only content 折叠回字符串
>   （修静默丢正文/短期记忆问题）、`thinking_blocks` 顶层回传 + 流式分片合并；响应侧
>   `_convert_dict_to_message`/`_convert_delta_to_message_chunk` 捕获 `thinking_blocks`。
>   新增 8 个单测，更新 1 个既有形状断言（text-only 折叠属预期行为变化）。
> - LiteLLM 防御性建议（Draft 2）未单独提交：根因修复已在 langchain-litellm PR 覆盖，
>   如上游讨论需要再补。
>
> 背景见 `HARNESS_NOTES.md` §43/§46。本地 workaround 位于
> `backend/app/chat/litellm_provider.py`（content 清理 + `thinking_blocks` 载体往返），回归门是
> `backend/tests/test_provider_reasoning_contract.py`。上游修复发布后应升级依赖、收缩本地
> wrapper，并保留契约矩阵作为回归门。
>
> 事实核对基线：`langchain-litellm==0.7.0`、`litellm==1.92.0`、`langchain-core==1.4.7`。
> 以下为当时的草稿正文，实际提交内容以上方链接为准。

---

## Draft 1 — langchain-litellm issue（可附 PR）

**Title:** ChatLiteLLM round-trip drops reasoning state: normalized `reasoning` blocks leak into
provider requests (DeepSeek 400) and LiteLLM `thinking_blocks` are discarded (Anthropic extended
thinking silently disabled on tool loops)

**Body:**

### Environment

- langchain-litellm 0.7.0, litellm 1.92.0, langchain-core 1.4.7
- Multi-turn tool loop (LangGraph ReAct-style), thinking/reasoning enabled

### Summary

`ChatLiteLLM` converts LiteLLM's `reasoning_content` into a `{"type": "thinking"}` content block on
the way in (`_inject_reasoning_content_into_content`, `chat_models/litellm.py:114-134, 200-204`),
but the request-side serializer `_convert_message_to_dict` only filters
`tool_use/tool_call/thinking/redacted_thinking` (`litellm.py:346-352`). Two consequences:

**Defect 1 — normalized `reasoning` blocks leak into provider requests.** langchain-core's
standard-content normalization (`.content_blocks`, no `"litellm"` translator registered → best-effort
path) renders the injected `thinking` block as `{"type": "reasoning"}` and wraps unrecognized blocks
as `{"type": "non_standard"}`. Apps that persist standardized content (LangGraph state, middleware,
any `content_blocks` materialization) feed those back through `ChatLiteLLM`, and the filter misses
them. LiteLLM's DeepSeek transform collapses content lists by concatenating only `text` fields
(`prompt_templates/common_utils.py:163-184`), so an assistant message whose content is *only* a
reasoning block is sent as a list verbatim and DeepSeek rejects the request before generation:

```text
litellm.BadRequestError: DeepseekException
messages[2]: unknown variant `reasoning`, expected `text`
```

Minimal repro (no network needed — inspect the payload):

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_litellm import ChatLiteLLM

model = ChatLiteLLM(model="deepseek/deepseek-reasoner", api_key="x")
messages, _ = model._create_message_dicts(
    [
        HumanMessage(content="analyze"),
        AIMessage(
            content=[{"type": "reasoning", "reasoning": "chain of thought"}],
            additional_kwargs={"reasoning_content": "chain of thought"},
            tool_calls=[{"name": "read_file", "args": {"path": "README.md"}, "id": "call_1"}],
        ),
        ToolMessage(content="ok", tool_call_id="call_1"),
    ],
    None,
)
print(messages[1]["content"])
# [{'type': 'reasoning', 'reasoning': 'chain of thought'}]  <- rejected by DeepSeek
```

**Defect 2 — LiteLLM's `thinking_blocks` carrier is dropped in both directions.** LiteLLM
normalizes signed/opaque reasoning state (Anthropic/Bedrock signed thinking, Gemini thought state)
into the typed `thinking_blocks` field on messages and deltas, and restores assistant-level
`thinking_blocks` into native provider requests (`prompt_templates/factory.py:2530-2688`).
`_convert_dict_to_message` / `_convert_delta_to_message_chunk` never capture the field, and
`_convert_message_to_dict` never emits it. With tools + extended thinking on Anthropic, LiteLLM
detects the missing blocks and **silently disables extended thinking for the continuation turn**
(`llms/anthropic/chat/transformation.py:1759-1776`, verbose-only warning) — multi-turn agent quality
degrades with no error.

### Proposed fix (happy to send a PR)

1. Request side: extend the content filter with `reasoning` and unwrap `non_standard` whose `value`
   is a reasoning/thinking block (they are metadata, and `reasoning_content` is already forwarded
   top-level at `litellm.py:385-390`).
2. Response side: copy `thinking_blocks` from the LiteLLM message/delta into
   `additional_kwargs["thinking_blocks"]`.
3. Request side: emit `additional_kwargs["thinking_blocks"]` as the top-level `thinking_blocks`
   field on assistant dicts, consolidating streamed partials first — LiteLLM repeats the full
   accumulated text on the signature-bearing block (`llms/anthropic/chat/handler.py:654-696`), so
   keep signed + `redacted_thinking` blocks and drop unsigned partials; only merge unsigned text
   when no signed block exists.

---

## Draft 2 — LiteLLM issue（防御性建议，可选）

**Title:** `convert_content_list_to_str` silently forwards content lists it cannot convert
(DeepSeek: `unknown variant 'reasoning', expected 'text'`)

**Body:**

`handle_messages_with_content_list_to_str_conversion`
(`litellm_core_utils/prompt_templates/common_utils.py:84-94`) only rewrites `message["content"]`
when the extracted text is truthy:

```python
texts = convert_content_list_to_str(message=message)
if texts:
    message["content"] = texts
```

`convert_content_list_to_str` concatenates only `block.get("text")` (`common_utils.py:163-184`).
When an assistant message's content list contains no `text` blocks at all (e.g. a stray
`{"type": "reasoning"}` block produced by an upstream adapter), the result is `""` → falsy → the
original list is sent verbatim to providers whose schema only accepts string content or `text`
parts. DeepSeek then fails the whole request during deserialization:

```text
messages[2]: unknown variant `reasoning`, expected `text`
```

The offending input is out-of-contract, but the failure mode is opaque (a provider-side serde error
instead of a clear client-side message). Suggested hardening, either of:

- collapse to the extracted string unconditionally for providers that require string content
  (`message["content"] = texts` without the truthiness guard), dropping unconvertible blocks with a
  verbose warning naming the dropped block types; or
- raise a clear client-side error (`BadRequestError` with the unsupported block type and message
  index) before the request leaves LiteLLM.

Repro: send `messages=[..., {"role": "assistant", "content": [{"type": "reasoning", "reasoning":
"x"}], "tool_calls": [...]}, ...]` through `litellm.completion(model="deepseek/deepseek-reasoner",
thinking={"type": "enabled"})`.
