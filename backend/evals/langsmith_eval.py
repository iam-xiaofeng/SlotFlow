"""把这套评测挂到 LangSmith 的 Dataset / Experiment 上。

和 ``--langsmith`` 的区别很重要,面试也常被追问:

- ``--langsmith`` 只是**打开链路追踪**(``LANGSMITH_TRACING=true``),你能在 LangSmith 里看到
  每一次模型调用和工具调用的 trace,但**没有数据集、没有实验、没有分数**——换个模型再跑一遍,
  两次结果只能靠人眼在 trace 列表里对。
- 这里做的是**评测**:把 20 条样本注册成一个 LangSmith Dataset,把
  ``evaluators.py`` 里的确定性评测器 + LLM-as-judge 包装成 LangSmith 的 evaluator,
  用 ``langsmith.evaluate()`` 跑成一次 Experiment。于是每个模型/每次改动都是一行可对比的
  实验记录,逐条样本的分数、失败明细、trace 三者在 UI 里是连起来的。

用法::

    cd backend
    uv run python -m evals.langsmith_eval --model grok-4.5                 # 建/更新数据集并跑实验
    uv run python -m evals.langsmith_eval --model grok-4.5 --judge         # 额外开 LLM-as-judge
    uv run python -m evals.langsmith_eval --dataset-only                   # 只同步数据集,不跑

需要 ``LANGSMITH_API_KEY``(从 ``backend/.env`` 读)。
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
from evals.evaluators import EvalContext, _REGISTRY, final_answer_text  # noqa: E402
from evals.run_eval import _run_live_item, load_dotenv  # noqa: E402

DATASET_NAME = "SlotFlow-agent-evals"


def sync_dataset(client: Any) -> Any:
    """建立(或复用)数据集,并把 20 条样本同步上去。

    以样本 id 作为 example 的稳定标识:重复运行不会产生重复 example,改了样本内容会更新。
    """

    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    else:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="SlotFlow 自建 agent 评测集:工具正确性/精度收敛/规划 HITL/记忆上下文/契约",
        )

    existing = {
        (example.metadata or {}).get("sample_id"): example
        for example in client.list_examples(dataset_id=dataset.id)
    }
    created = updated = 0
    for item in DATASET:
        inputs = {"turns": item["turns"]}
        outputs = {"reference": item.get("reference", "")}
        metadata = {"sample_id": item["id"], "tags": item.get("tags", []), "desc": item["desc"]}
        found = existing.get(item["id"])
        if found is None:
            client.create_example(
                dataset_id=dataset.id, inputs=inputs, outputs=outputs, metadata=metadata
            )
            created += 1
        else:
            client.update_example(
                example_id=found.id, inputs=inputs, outputs=outputs, metadata=metadata
            )
            updated += 1
    print(f"[langsmith] 数据集 {DATASET_NAME}:新增 {created} 条,更新 {updated} 条")
    return dataset


def build_target(model_name: str, provider: str):
    """LangSmith 的被测目标:吃一条 example 的 inputs,吐出这次运行的 transcript。"""

    by_id = dataset_by_id()

    def target(inputs: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        # example 的 metadata 带着 sample_id,借它拿回完整样本(env_overrides / 预置文件等)。
        sample_id = (metadata or {}).get("sample_id")
        item = by_id.get(sample_id) if sample_id else None
        if item is None:  # 兜底:按 turns 反查,再不行就临时拼一条
            item = next(
                (i for i in DATASET if i["turns"] == inputs.get("turns")),
                {"id": "adhoc", "turns": inputs.get("turns", []), "evaluators": []},
            )
        transcript = asyncio.run(_run_live_item(item, model_name, provider))
        return {
            "transcript": transcript,
            "answer": final_answer_text(transcript),
            "sample_id": item["id"],
        }

    return target


def build_evaluators(ctx: EvalContext):
    """把样本自己声明的评测器包成 LangSmith evaluator。

    LangSmith 的 evaluator 是"对每个 example 都跑一遍"的,而我们的评测器是**逐样本声明**的
    (读文件那条才评 no_tool_errors,纯聊天那条才评 forbids_tools)。所以这里包一个分发器:
    对当前 example 只跑它自己声明的那几个,其余返回 None 表示不适用——LangSmith 会忽略 None,
    不会把"没评"算成"没通过"。
    """

    by_id = dataset_by_id()

    def dispatch(run: Any, example: Any) -> list[dict[str, Any]]:
        outputs = run.outputs or {}
        transcript = outputs.get("transcript") or []
        sample_id = outputs.get("sample_id") or (example.metadata or {}).get("sample_id")
        item = by_id.get(sample_id)
        if item is None:
            return []
        feedback: list[dict[str, Any]] = []
        for name, params in item["evaluators"]:
            result = _REGISTRY[name](transcript, item, params, ctx=ctx)
            if result.skipped:
                continue
            feedback.append(
                {"key": result.name, "score": 1 if result.passed else 0, "comment": result.detail}
            )
        # 样本级总分:全部通过才算 1,和本地打分表的 `ok` 口径一致。
        feedback.append(
            {
                "key": "sample_pass",
                "score": 1 if all(f["score"] == 1 for f in feedback) else 0,
                "comment": f"{sum(f['score'] for f in feedback)}/{len(feedback)} 项通过",
            }
        )
        return feedback

    return [dispatch]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="grok-4.5", help="被测模型(默认 grok-4.5)")
    parser.add_argument("--provider", default="custom", help="provider(中转模型用 custom)")
    parser.add_argument("--judge", action="store_true", help="启用 LLM-as-judge 评测器")
    parser.add_argument("--dataset-only", action="store_true", help="只同步数据集,不跑实验")
    parser.add_argument("--only", default="", help="只跑某个样本 id")
    parser.add_argument("--concurrency", type=int, default=2, help="并发样本数(默认 2)")
    args = parser.parse_args()

    loaded = load_dotenv(_BACKEND / ".env")
    print(f"[langsmith] 已从 .env 载入 {loaded} 个环境变量")
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("缺少 LANGSMITH_API_KEY(写进 backend/.env 即可)。")
        return 1
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGSMITH_PROJECT", "SlotFlow")

    from langsmith import Client, evaluate

    client = Client()
    dataset = sync_dataset(client)
    if args.dataset_only:
        return 0

    judge = None
    if args.judge:
        from app.chat.runtime.models import create_chat_model

        judge = create_chat_model(args.model, provider=args.provider)
    ctx = EvalContext(provider=_provider_of_model(args.model), judge_model=judge)

    data: Any = dataset.name
    if args.only:
        data = [
            example
            for example in client.list_examples(dataset_id=dataset.id)
            if (example.metadata or {}).get("sample_id") == args.only
        ]
        if not data:
            print(f"数据集里没有样本 {args.only}")
            return 1

    results = evaluate(
        build_target(args.model, args.provider),
        data=data,
        evaluators=build_evaluators(ctx),
        experiment_prefix=f"slotflow-{args.model}",
        max_concurrency=args.concurrency,
        metadata={"model": args.model, "provider": args.provider, "judge": args.judge},
    )
    print(f"\n实验已提交 LangSmith:{getattr(results, 'experiment_name', '(见 UI)')}")
    print(f"数据集:{DATASET_NAME} · project={os.environ.get('LANGSMITH_PROJECT')}")
    return 0


def _provider_of_model(model_name: str) -> str:
    return "deepseek" if "deepseek" in model_name.lower() else "custom"


if __name__ == "__main__":
    raise SystemExit(main())
