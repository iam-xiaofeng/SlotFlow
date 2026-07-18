"""Context-window, epoch fallback, and tool-space policy contracts."""


import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.chat.runtime.models import resolve_model_context_budget
from app.harness.graph import emergency_context_projection, is_context_overflow_error
from app.harness.state import SlotFlowAgentState, merge_promoted_tool_names
from app.harness.subagents.tools import resolve_subagent_tool_spaces


def test_custom_context_window_uses_per_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON", '{"glm-5.2": 200000}')
    monkeypatch.setenv("SLOTFLOW_CONTEXT_RESERVE_TOKENS", "20000")
    window, budget, source = resolve_model_context_budget("glm-5.2", provider="custom")
    assert (window, budget, source) == (200000, 180000, "env:model-map")


def test_unknown_custom_context_window_uses_conservative_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLOTFLOW_MODEL_CONTEXT_WINDOWS_JSON", raising=False)
    monkeypatch.delenv("SLOTFLOW_MODEL_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setenv("SLOTFLOW_DEFAULT_CONTEXT_WINDOW_TOKENS", "128000")
    monkeypatch.setenv("SLOTFLOW_CONTEXT_RESERVE_TOKENS", "16384")
    window, budget, source = resolve_model_context_budget("unknown-relay-model", provider="custom")
    assert (window, budget, source) == (128000, 111616, "default")


def test_context_overflow_detection_handles_nested_provider_error() -> None:
    error = ExceptionGroup(
        "task group",
        [RuntimeError("????????????????????")],
    )
    assert is_context_overflow_error(error) is True
    assert is_context_overflow_error(RuntimeError("permission denied")) is False


def test_emergency_projection_shrinks_only_model_input() -> None:
    messages = [HumanMessage(content=f"message {index}") for index in range(20)]
    projected = emergency_context_projection(messages, attempt=1)
    assert 0 < len(projected) < len(messages)
    assert len(messages) == 20
    assert projected[-1].content == "message 19"


def test_subagent_tool_spaces_are_bounded_and_never_all() -> None:
    spaces, error = resolve_subagent_tool_spaces("coder", None)
    assert spaces == ("workspace", "sandbox")
    assert error is None
    assert resolve_subagent_tool_spaces("coder", ["all"])[1]
    assert resolve_subagent_tool_spaces(
        "coder", ["workspace", "sandbox", "network", "browser"]
    )[1]


def test_promoted_tool_names_reducer_is_ordered_union() -> None:
    assert merge_promoted_tool_names(None, None) == []
    assert merge_promoted_tool_names(["a"], None) == ["a"]
    assert merge_promoted_tool_names(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_concurrent_tool_space_promotion_does_not_raise_invalid_update() -> None:
    """Two *_tools loaders in one model step must not trip INVALID_CONCURRENT_GRAPH_UPDATE."""

    def fan(_state):
        return [Send("promote_a", _state), Send("promote_b", _state)]

    def promote_a(_state):
        return {"promoted_tool_names": ["web_search", "web_fetch"]}

    def promote_b(_state):
        return {"promoted_tool_names": ["sandbox_exec", "web_search"]}

    graph = StateGraph(SlotFlowAgentState)
    graph.add_node("promote_a", promote_a)
    graph.add_node("promote_b", promote_b)
    graph.add_conditional_edges(START, fan, ["promote_a", "promote_b"])
    graph.add_edge("promote_a", END)
    graph.add_edge("promote_b", END)
    compiled = graph.compile()

    result = compiled.invoke({"messages": [], "promoted_tool_names": ["artifact_write"]})

    promoted = result.get("promoted_tool_names")
    assert promoted[0] == "artifact_write"
    assert set(promoted) == {"artifact_write", "web_search", "web_fetch", "sandbox_exec"}
    assert len(promoted) == len(set(promoted))

