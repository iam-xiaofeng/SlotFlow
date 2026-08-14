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



def _response(usage: dict) -> SimpleNamespace:
    return SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata=usage))]]
    )


def test_context_tokens_ignores_subagent_and_summarization_calls() -> None:
    """上下文占用只认主 agent 那次调用。

    一轮里最后跑的很可能是 task_tool 子代理(子图节点也叫 agent,但它在工具内部)或压缩节点,
    它们的 prompt 与会话窗口占用毫无关系——用"最近一次成功调用"会让前端仪表突然跌下去。
    """

    collector = RunUsageCollector(model_name="m", provider="custom")

    # ① 主 agent 调用：这才是当前上下文
    collector.on_chat_model_start({}, [[]], run_id="main", metadata={"langgraph_node": "agent"})
    collector.on_llm_end(_response({"input_tokens": 120000, "output_tokens": 10}), run_id="main")

    # ② 工具内部的子代理调用（子图节点同样叫 agent）
    collector.on_tool_start({}, "", run_id="tool")
    collector.on_chat_model_start({}, [[]], run_id="child", metadata={"langgraph_node": "agent"})
    collector.on_llm_end(_response({"input_tokens": 3000, "output_tokens": 5}), run_id="child")
    collector.on_tool_end("done", run_id="tool")

    # ③ 压缩节点调用
    collector.on_chat_model_start(
        {}, [[]], run_id="summ", metadata={"langgraph_node": "SlotFlowSummarizationMiddleware"}
    )
    collector.on_llm_end(_response({"input_tokens": 8000, "output_tokens": 200}), run_id="summ")

    summary = collector.summary()

    assert summary["context_tokens"] == 120000
    # 累计口径不受影响：三次调用都算进 input_tokens 总和。
    assert summary["input_tokens"] == 131000
    assert summary["llm_requests"] == 3


def test_context_tokens_falls_back_when_no_graph_node_metadata() -> None:
    """非 graph 场景(纯脚本/单测桩)没有 langgraph_node，退回最近一次成功调用而不是显示空。"""

    collector = RunUsageCollector(model_name="m", provider="custom")
    collector.on_chat_model_start({}, [[]], run_id="only")
    collector.on_llm_end(_response({"input_tokens": 42, "output_tokens": 1}), run_id="only")

    assert collector.summary()["context_tokens"] == 42
