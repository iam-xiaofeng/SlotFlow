"""Context-window、epoch 回退,以及"工具集恒定"这条策略的契约测试。"""


import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition
from langgraph.types import Send

from app.chat.runtime.models import resolve_model_context_budget
from app.harness.graph import (
    EmptyModelResponseError,
    assert_model_response_not_empty,
    emergency_context_projection,
    is_context_overflow_error,
    make_tools_node,
    project_with_context_epoch,
)
from app.harness.state import SlotFlowAgentState, merge_ordered_unique
from app.harness.subagents.tools import resolve_subagent_tool_spaces
from app.harness.tool_spaces import tool_space_for_name


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


def test_used_skills_reducer_is_ordered_union() -> None:
    assert merge_ordered_unique(None, None) == []
    assert merge_ordered_unique(["a"], None) == ["a"]
    assert merge_ordered_unique(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_concurrent_skill_reads_do_not_raise_invalid_update() -> None:
    """两个 skill_read 落在同一步时,台账通道必须靠 reducer 合并而不是报并发写入。"""

    def fan(_state):
        return [Send("read_a", _state), Send("read_b", _state)]

    def read_a(_state):
        return {"used_skills": ["pdf", "charts"]}

    def read_b(_state):
        return {"used_skills": ["charts", "slides"]}

    graph = StateGraph(SlotFlowAgentState)
    graph.add_node("read_a", read_a)
    graph.add_node("read_b", read_b)
    graph.add_conditional_edges(START, fan, ["read_a", "read_b"])
    graph.add_edge("read_a", END)
    graph.add_edge("read_b", END)
    compiled = graph.compile()

    result = compiled.invoke({"messages": [], "used_skills": ["find-skills"]})

    used = result.get("used_skills")
    assert used[0] == "find-skills"
    assert set(used) == {"find-skills", "pdf", "charts", "slides"}
    assert len(used) == len(set(used))


def _fake_tool(name: str):
    @tool(name)
    def _f(query: str = "") -> str:
        """fake"""
        return f"{name}:{query}"

    return _f


def test_every_bound_tool_is_callable_without_any_activation_step() -> None:
    """工具集恒定:绑上的工具第一步就能直接调,没有加载器、没有 tool_not_activated。"""

    web_search = _fake_tool("web_search")

    def agent(state):
        called = sum(
            1
            for m in (state.get("messages") or [])
            if isinstance(m, AIMessage) and m.tool_calls
        )
        if called == 0:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "web_search", "args": {"query": "today"}, "id": "c1"}],
                    )
                ]
            }
        return {"messages": [AIMessage(content="done")]}

    graph = StateGraph(SlotFlowAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", make_tools_node([web_search]))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    out = graph.compile().invoke({"messages": []}, config={"recursion_limit": 12})

    tool_msgs = [m.content for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    assert any("web_search:today" in content for content in tool_msgs)
    assert not any("not_activated" in content for content in tool_msgs)


def test_tool_space_classification_still_partitions_subagent_tool_faces() -> None:
    """分类函数保留下来给子代理切工具面用(加载器没了,分类还在)。"""

    assert tool_space_for_name("browser_navigate") == "browser"
    assert tool_space_for_name("web_search") == "network"
    assert tool_space_for_name("skill_read") == "extensions"
    assert tool_space_for_name("mcp_call") == "extensions"
    assert tool_space_for_name("write_todos") is None


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


def test_empty_model_response_raises_instead_of_entering_state() -> None:
    """空响应必须当场失败，不能变成一条空 AIMessage 进 state。

    2026-08-14 真机:446KB 文件被 `workspace_read` 整段内联(≈166k token)后,provider 连着
    返回 output_tokens=0 的空消息、HTTP 却是 200。当时是**静默死亡**:空消息没有 tool_calls
    → 路由 finalize → 这一轮"正常结束" → `run.finished` 时 content 为空 → 一条都不落库;
    而那条空消息进了 checkpoint,thread 被永久毒化(后来发"继续啊"仍然吐空)。见 §63。
    """

    with pytest.raises(EmptyModelResponseError):
        assert_model_response_not_empty(AIMessage(content=""))

    with pytest.raises(EmptyModelResponseError):
        assert_model_response_not_empty(AIMessage(content="   \n  "))


def test_non_empty_and_tool_call_responses_pass_the_guard() -> None:
    """只调工具不给正文是完全正常的一步,不能被这道闸误伤。"""

    assert_model_response_not_empty(AIMessage(content="答案"))
    assert_model_response_not_empty(
        AIMessage(
            content="",
            tool_calls=[{"name": "workspace_read", "args": {}, "id": "a", "type": "tool_call"}],
        )
    )
    # reasoning 块被 sanitize 剥掉之后 content 会是块列表,正文仍要能被认出来。
    assert_model_response_not_empty(AIMessage(content=[{"type": "text", "text": "答案"}]))
