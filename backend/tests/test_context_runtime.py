"""Context-window, epoch fallback, and tool-space policy contracts."""


import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition
from langgraph.types import Send

from app.chat.runtime.models import resolve_model_context_budget
from app.harness.graph import (
    emergency_context_projection,
    is_context_overflow_error,
    make_tools_node,
    project_with_context_epoch,
)
from app.harness.state import SlotFlowAgentState, merge_promoted_tool_names
from app.harness.subagents.tools import resolve_subagent_tool_spaces
from app.harness.tool_spaces import assemble_tool_spaces


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


def _fake_tool(name: str):
    @tool(name)
    def _f(query: str = "") -> str:
        """fake"""
        return f"{name}:{query}"

    return _f


def test_default_partition_keeps_everyday_tools_active_and_gates_heavy_spaces() -> None:
    names = [
        "workspace_read",
        "artifact_write",
        "web_search",
        "sandbox_exec",
        "convert_file_to_markdown",
        "memory_save",
        "browser_navigate",
        "skill_match",
    ]
    setup = assemble_tool_spaces([_fake_tool(n) for n in names])
    # Everyday tools are bound & callable on turn 1 (no loader dance).
    for n in ["workspace_read", "artifact_write", "web_search", "sandbox_exec", "convert_file_to_markdown", "memory_save"]:
        assert n in setup.initial_names, n
    # Only the heavy browser/extensions spaces stay behind a loader.
    assert "browser_navigate" not in setup.initial_names
    assert "skill_match" not in setup.initial_names
    assert {"browser_tools", "extensions_tools"} <= setup.initial_names
    assert set(setup.spaces) == {"browser", "extensions"}


def test_gated_space_becomes_callable_after_loader_promotes_it() -> None:
    """Loader -> promote -> call must actually work through the real ToolNode gate."""

    web_search = _fake_tool("web_search")
    setup = assemble_tool_spaces([web_search], gated_spaces=frozenset({"network"}))
    assert "web_search" not in setup.initial_names  # gated
    assert "network_tools" in setup.initial_names  # loader active

    def agent(state):
        ai = sum(1 for m in (state.get("messages") or []) if isinstance(m, AIMessage) and m.tool_calls)
        if ai == 0:
            return {"messages": [AIMessage(content="", tool_calls=[{"name": "network_tools", "args": {"names": ["web_search"]}, "id": "c1"}])]}
        if ai == 1:
            return {"messages": [AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "today"}, "id": "c2"}])]}
        return {"messages": [AIMessage(content="done")]}

    graph = StateGraph(SlotFlowAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", make_tools_node(list(setup.tools), initial_tool_names=setup.initial_names))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    out = graph.compile().invoke({"messages": [], "promoted_tool_names": []}, config={"recursion_limit": 12})

    assert out.get("promoted_tool_names") == ["web_search"]
    tool_msgs = [m.content for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert any("web_search:today" in c for c in tool_msgs)  # executed, not tool_not_activated
    assert not any("not_activated" in c for c in tool_msgs)


def test_context_epoch_is_reused_across_appended_turns_not_reset() -> None:
    """After compaction, appending A then B must keep A and B and reuse the epoch (no re-summary)."""

    old = [HumanMessage(content=f"old {i}", id=f"o{i}") for i in range(6)]
    summary = AIMessage(content="SUMMARY of old 0..5", id="sum")
    # Epoch built from the (repaired) canonical prefix of the 6 old messages.
    from app.harness.graph import _message_prefix_signature

    epoch = {
        "source_message_count": len(old),
        "source_signature": _message_prefix_signature(old),
        "messages": [summary],
    }
    a = HumanMessage(content="A", id="a")
    b = HumanMessage(content="B", id="b")

    # Turn after compaction: user sent A.
    canonical_after_a = [*old, a]
    projected, used = project_with_context_epoch(canonical_after_a, epoch)
    assert used is True  # epoch reused, not reset
    assert projected[0] is summary
    assert a in projected

    # Next turn: A's answer + B appended.
    canonical_after_b = [*old, a, AIMessage(content="answer to A", id="ra"), b]
    projected2, used2 = project_with_context_epoch(canonical_after_b, epoch)
    assert used2 is True
    assert projected2[0] is summary
    assert a in projected2 and b in projected2  # neither turn is forgotten


def test_context_epoch_resets_when_prefix_signature_changes() -> None:
    old = [HumanMessage(content=f"old {i}", id=f"o{i}") for i in range(4)]
    epoch = {
        "source_message_count": len(old),
        "source_signature": "stale-signature-that-will-not-match",
        "messages": [AIMessage(content="SUMMARY", id="s")],
    }
    canonical = [*old, HumanMessage(content="A", id="a")]
    projected, used = project_with_context_epoch(canonical, epoch)
    assert used is False  # stale epoch -> caller clears it
    assert projected == canonical  # falls back to full (repaired) canonical, nothing dropped

