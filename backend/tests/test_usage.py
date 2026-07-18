"""Local LLM usage/cache telemetry contracts."""

from types import SimpleNamespace
from uuid import uuid4

from app.chat.usage import RunUsageCollector, normalize_usage


def test_normalize_usage_preserves_unknown_cache_state() -> None:
    usage = normalize_usage({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105})
    assert usage["cached_input_tokens"] is None


def test_normalize_usage_accepts_openai_and_anthropic_cache_fields() -> None:
    assert normalize_usage({
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 80},
    })["cached_input_tokens"] == 80
    assert normalize_usage({
        "input_tokens": 100,
        "cache_read_input_tokens": 70,
        "cache_creation_input_tokens": 20,
    }) == {
        "input_tokens": 100,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": 70,
        "cache_creation_input_tokens": 20,
        "reasoning_tokens": None,
    }


def test_usage_collector_aggregates_calls_without_prompt_content() -> None:
    collector = RunUsageCollector(model_name="glm-5.2", provider="custom")
    run_id = uuid4()
    collector.on_chat_model_start(
        {},
        [[SimpleNamespace(content="secret prompt")]],
        run_id=run_id,
        invocation_params={"tools": [{"name": "tool_a"}]},
    )
    response = SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata={
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
            "input_token_details": {"cache_read": 75},
        }))]],
        llm_output=None,
    )
    collector.on_llm_end(response, run_id=run_id)
    summary = collector.summary()
    assert summary["llm_requests"] == 1
    assert summary["cached_input_tokens"] == 75
    assert summary["cache_hit_requests"] == 1
    assert summary["calls"][0]["cache_status"] == "hit"
    assert "secret prompt" not in str(summary)


def test_context_tokens_tracks_latest_successful_prompt_size() -> None:
    collector = RunUsageCollector(model_name="glm-5.2", provider="custom")

    def record(run_id, input_tokens: int) -> None:
        collector.on_chat_model_start({}, [[]], run_id=run_id, invocation_params={})
        collector.on_llm_end(
            SimpleNamespace(
                generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": 10,
                    "total_tokens": input_tokens + 10,
                }))]],
                llm_output=None,
            ),
            run_id=run_id,
        )

    first, second = uuid4(), uuid4()
    record(first, 1200)
    record(second, 3400)
    summary = collector.summary()
    # 上下文占用取最近一次成功调用的 prompt tokens,而非所有调用的累加。
    assert summary["context_tokens"] == 3400
    assert summary["input_tokens"] == 4600

