"""评测集自身的体检。

存在的理由:2026-08-14 复查发现旧评测集有一条 ``write-file`` 期望 ``workspace_write`` ——
**这个工具从来不存在**,所以那条样本永远不可能通过,而 live 报表把它记成"agent 失败"。
还有两条离线桩仍在演示早已删除的 ``network_tools`` / ``workspace_tools`` loader。

评测集会随架构漂移,而它自己不会报错——只会安静地量错。这里把几条不变量钉死。
"""

from __future__ import annotations

import pytest

from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.harness.features import SlotFlowHarnessFeatures
from app.harness.tools.registry import build_harness_tools
from evals.dataset import DATASET
from evals.evaluators import _REGISTRY, attempted_tool_names


def _live_tool_names() -> set[str]:
    bundle = build_run_config(
        thread_id="eval-selfcheck", run_id="r", request=ChatStreamRequest(message="x", mode="ultra")
    )
    tools = build_harness_tools(
        features=SlotFlowHarnessFeatures(plan_enabled=True, subagent_enabled=True, thinking_enabled=True),
        run_context=bundle.context,
    )
    return {tool.name for tool in tools}


def test_dataset_has_twenty_unique_samples() -> None:
    ids = [item["id"] for item in DATASET]
    assert len(ids) == 20
    assert len(set(ids)) == 20, "样本 id 必须唯一(LangSmith 用它作为 example 的稳定标识)"


def test_every_expected_tool_actually_exists() -> None:
    """评测器期望的每个工具名都必须在运行期 registry 里。

    这条就是 write-file 那个 bug 的钉子:期望一个不存在的工具,样本恒红,而报表会把它
    记成 agent 的问题。
    """

    live = _live_tool_names()
    unknown: dict[str, set[str]] = {}
    for item in DATASET:
        for name, params in item["evaluators"]:
            expected = set(params.get("names") or [])
            missing = expected - live
            if missing:
                unknown[item["id"]] = missing
    assert not unknown, f"评测器引用了不存在的工具:{unknown};registry 现有 {sorted(live)}"


def test_every_stub_only_calls_real_tools() -> None:
    """离线桩里出现的工具名同样必须真实存在。

    桩是"评测器能抓到什么"的演示。它演示一个已经删掉的机制(比如渐进式披露的 loader),
    读的人会以为那套东西还在。
    """

    live = _live_tool_names()
    stale: dict[str, set[str]] = {}
    for item in DATASET:
        called = set(attempted_tool_names(item.get("stub") or []))
        missing = called - live
        if missing:
            stale[item["id"]] = missing
    assert not stale, f"离线桩调用了不存在的工具:{stale}"


def test_every_evaluator_name_is_registered() -> None:
    unknown = {
        item["id"]: name
        for item in DATASET
        for name, _ in item["evaluators"]
        if name not in _REGISTRY
    }
    assert not unknown, f"样本引用了未注册的评测器:{unknown}"


@pytest.mark.parametrize("item", DATASET, ids=[item["id"] for item in DATASET])
def test_sample_is_well_formed(item: dict) -> None:
    assert item["turns"], f"{item['id']} 没有任何用户轮次"
    assert item["evaluators"], f"{item['id']} 没有声明任何评测器"
    assert item.get("stub"), f"{item['id']} 缺少离线桩(offline 档跑不了)"
    # reference 只有 llm_judge 用;但声明了 judge 就必须给参考答案,否则裁判无从对照。
    if any(name == "llm_judge" for name, _ in item["evaluators"]):
        assert item.get("reference"), f"{item['id']} 用了 llm_judge 却没有 reference"
