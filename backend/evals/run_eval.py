"""SlotFlow agent 评测 runner:offline / smoke / live 三档 + 打分表。

    cd backend
    .venv/bin/python -m evals.run_eval                 # offline:对桩 transcript 打分(确定、免费)
    .venv/bin/python -m evals.run_eval --smoke         # smoke :真图 + FakeModel,验证端到端接线
    .venv/bin/python -m evals.run_eval --live --model grok-4.5   # live:真模型跑全部 10 条
    .venv/bin/python -m evals.run_eval --live --model grok-4.5 --langsmith  # 额外把 trace 推到 LangSmith

offline / smoke 不联网、不花钱;live 会真的调用模型(读取 backend/.env 里的 key)。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from evals.dataset import DATASET, dataset_by_id  # noqa: E402
from evals.evaluators import EvalContext, ItemScore, final_answer_text, score_item  # noqa: E402


# --------------------------------------------------------------------------- #
# .env 极简加载(python-dotenv 未安装;live 时需要 key)
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path) -> int:
    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value  # 后出现的覆盖先出现的,对齐 shell `source .env`(当前活跃=最后一组中转)
            loaded += 1
    return loaded


def _provider_of(model_name: str) -> str:
    return "deepseek" if "deepseek" in model_name.lower() else "custom"


# --------------------------------------------------------------------------- #
# 真图运行(smoke 用 FakeModel;live 用真模型)——两者只差一个 model 参数
# --------------------------------------------------------------------------- #
async def _run_graph(
    item: dict[str, Any], *, model: Any = None, model_name: str, provider: str | None = None
) -> list[Any]:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.chat.models import ChatStreamRequest
    from app.chat.run_config import build_run_config
    from app.harness.builder import build_slotflow_harness_graph
    from app.harness.config import SlotFlowHarnessConfig

    system_prompt = "你是 SlotFlow 智能助手,善用工具完成用户任务;信息不足时先澄清,不要臆造。"
    request = ChatStreamRequest(
        message=item["turns"][0], model_name=model_name, provider=provider, mode="pro"
    )
    bundle = build_run_config(
        thread_id=f"eval-{item['id']}", run_id=f"run-{item['id']}", request=request
    )
    if model is None:  # live:用真模型(显式 provider,避免 grok 被 litellm 误判为 xai 直连)
        from app.chat.runtime.models import create_chat_model

        model = create_chat_model(model_name, run_context=bundle.context, provider=provider)
    graph = build_slotflow_harness_graph(
        model=model,
        run_context=bundle.context,
        harness_config=SlotFlowHarnessConfig(system_prompt=system_prompt),
        checkpointer=InMemorySaver(),
    )
    result: dict[str, Any] = {"messages": []}
    for turn in item["turns"]:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": turn}]},
            config=bundle.config,
            context=bundle.context,
        )
    return list(result["messages"])


async def _run_live_item(item: dict[str, Any], model_name: str, provider: str) -> list[Any]:
    """应用该条的 env_overrides(如压缩阈值),用真模型跑,再恢复环境。"""

    overrides = item.get("env_overrides") or {}
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: str(v) for k, v in overrides.items()})
    try:
        return await _run_graph(item, model=None, model_name=model_name, provider=provider)
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


# --------------------------------------------------------------------------- #
# 打分表
# --------------------------------------------------------------------------- #
def print_scorecard(scores: list[ItemScore], *, title: str) -> None:
    print(f"\n===== {title} =====")
    header = f"{'#':>2}  {'样本ID':<26}{'标签':<20}{'得分':>6}  {'结果':<6} 失败/跳过明细"
    print(header)
    print("-" * len(header) * 2)
    item_pass = ev_pass = ev_total = 0
    for idx, s in enumerate(scores, 1):
        fails = [f"{r.name}({r.detail})" for r in s.scored if not r.passed]
        skips = [r.name for r in s.results if r.skipped]
        note = "; ".join(fails)
        if skips:
            note += f"  [跳过:{','.join(skips)}]"
        flag = "PASS" if s.ok else "FAIL"
        tags = ",".join(s.tags)
        print(f"{idx:>2}  {s.item_id:<26}{tags:<20}{s.passed_count:>3}/{s.total:<2}  {flag:<6} {note}")
        item_pass += 1 if s.ok else 0
        ev_pass += s.passed_count
        ev_total += s.total
    print("-" * len(header) * 2)
    print(f"汇总:样本 {item_pass}/{len(scores)} 条通过 | 评测器 {ev_pass}/{ev_total} 项通过")


# --------------------------------------------------------------------------- #
# 三档入口
# --------------------------------------------------------------------------- #
def run_offline(items: list[dict[str, Any]]) -> list[ItemScore]:
    ctx = EvalContext(provider="custom")  # 桩按 custom 处理:回灌即判红
    return [score_item(item["stub"], item, ctx=ctx) for item in items]


async def run_smoke() -> list[ItemScore]:
    """真图 + FakeModel 跑一条 no-tool 样本,证明 图→抽取→打分 全链路通。"""

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    item = dataset_by_id()["no-tool-chat"]
    answer = "二分查找是在有序数组中,每次比较中间元素并折半缩小搜索区间来定位目标的算法。"
    model = FakeListChatModel(responses=[answer])
    transcript = await _run_graph(item, model=model, model_name="fake/smoke")
    print(f"[smoke] 真图返回终答:{final_answer_text(transcript)!r}")
    return [score_item(transcript, item, ctx=EvalContext(provider="custom"))]


async def run_live(
    items: list[dict[str, Any]], model_name: str, *, provider: str, use_judge: bool
) -> list[ItemScore]:
    from app.chat.runtime.models import create_chat_model

    judge = create_chat_model(model_name, provider=provider) if use_judge else None
    ctx = EvalContext(provider=_provider_of(model_name), judge_model=judge)
    scores: list[ItemScore] = []
    for item in items:
        print(f"[live] 运行 {item['id']} … ", end="", flush=True)
        try:
            transcript = await _run_live_item(item, model_name, provider)
            s = score_item(transcript, item, ctx=ctx)
            print(f"{s.passed_count}/{s.total} {'PASS' if s.ok else 'FAIL'}")
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整轮评测
            s = ItemScore(item_id=item["id"], tags=list(item.get("tags", [])))
            from evals.evaluators import EvalResult

            s.results.append(EvalResult("run", passed=False, detail=f"运行异常:{type(exc).__name__}: {exc}"))
            print(f"运行异常:{exc}")
        scores.append(s)
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="SlotFlow agent 评测 runner")
    parser.add_argument("--smoke", action="store_true", help="真图 + FakeModel 验证接线")
    parser.add_argument("--live", action="store_true", help="真模型跑全部样本")
    parser.add_argument("--model", default="grok-4.5", help="live 模型名(默认 grok-4.5)")
    parser.add_argument("--provider", default="custom", help="live provider(中转模型用 custom)")
    parser.add_argument("--judge", action="store_true", help="live 时启用 LLM-as-judge")
    parser.add_argument("--langsmith", action="store_true", help="live 时把 trace 推到 LangSmith(需已配 key)")
    parser.add_argument("--only", default="", help="只跑某个样本 id")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    args = parser.parse_args()

    items = DATASET
    if args.only:
        items = [i for i in items if i["id"] == args.only]
    if args.limit:
        items = items[: args.limit]

    if args.smoke:
        scores = asyncio.run(run_smoke())
        print_scorecard(scores, title="smoke(真图 + FakeModel)")
        return

    if args.live:
        n = load_dotenv(_BACKEND / ".env")
        print(f"[live] 已从 .env 载入 {n} 个环境变量;模型={args.model}")
        if args.langsmith:
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ.setdefault("LANGSMITH_PROJECT", "SlotFlow")
            print(f"[live] 已开启 LangSmith 追踪 → project={os.environ.get('LANGSMITH_PROJECT')}")
        scores = asyncio.run(run_live(items, args.model, provider=args.provider, use_judge=args.judge))
        print_scorecard(scores, title=f"live(真模型 {args.model})")
        return

    scores = run_offline(items)
    print_scorecard(scores, title="offline(桩 transcript,确定性)")
    print("\n注:offline 的 transcript 是人工编造的,只证明评测器/打分/报表本身正确;")
    print("    1 号(工具执行失败)与 10 号(回灌思考)是故意造的红项,演示评测器确实抓得到。")
    print("    真实 agent 分数请跑 --live。")


if __name__ == "__main__":
    main()
