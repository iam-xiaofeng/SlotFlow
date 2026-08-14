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


def default_live_model() -> str:
    """live 默认模型跟着 ``.env`` 的 ``CUSTOM_MODELS`` 走,不硬编码。

    2026-08-14 踩到:默认值一直写死 ``grok-4.5``,而中转早就换成了 ``deepseek-v4-pro``,
    21 条样本全部以 ``NotFoundError`` 收场——报表看起来像 agent 全线崩溃,其实只是模型名过期。
    和评测集里那些过期工具名是同一类腐烂:硬编码的外部事实会漂,而它自己不会喊。
    """

    # CUSTOM_MODELS 可能出现多行(多个中转),`load_dotenv` 后出现的覆盖先出现的,取最后一组的第一个。
    raw = os.environ.get("CUSTOM_MODELS", "")
    first = next((part.strip() for part in raw.split(",") if part.strip()), "")
    return first or "deepseek-v4-pro"


def _provision_workspace(item: dict[str, Any]) -> None:
    """把样本声明的 ``workspace_files`` 写进评测工作区。

    旧数据集的 ``read-file`` 恒红,原因不是 agent 不会读文件,而是**工作区里根本没有那个文件**
    (README 里记作"环境缺口")。评测环境没准备好就去量 agent,量到的是环境不是能力。
    """

    files = item.get("workspace_files") or {}
    if not files:
        return
    from app.harness.sandbox import build_slotflow_workspace

    root = build_slotflow_workspace(None).root
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 真图运行(smoke 用 FakeModel;live 用真模型)——两者只差一个 model 参数
# --------------------------------------------------------------------------- #
# 中转的并发上限。超了它不回 429,而是直接给一个空补全——比报错更难查。
MAX_LIVE_CONCURRENCY = 5

_RUNTIME_CACHE: dict[str, Any] = {}


async def _shared_runtime() -> Any:
    """整轮评测共用一份 runtime_config + 已预加载的 MCP provider。

    MCP 是 stdio 子进程(playwright 那套)。第一版在每条样本里各 `load_runtime_config_from_env()`
    一次,21 条样本就是 21 轮 spawn/teardown,跑到第二条就崩在
    `asyncio.exceptions.CancelledError: Cancelled via cancel scope`。
    后端是「一次 run 一个 provider,finally 关掉」;评测这边整轮只有一个进程,复用一份最省事,
    结束时统一关闭(见 `_close_shared_runtime`)。
    """

    if "config" not in _RUNTIME_CACHE:
        from app.chat.runtime.config import (
            load_runtime_config_from_env,
            refresh_runtime_skills_config,
        )
        from app.harness.mcp import ensure_mcp_tools_loaded

        runtime_config = load_runtime_config_from_env()
        refresh_runtime_skills_config(runtime_config)
        provider = runtime_config.mcp_tool_provider
        if provider is not None:
            await ensure_mcp_tools_loaded(
                config=runtime_config.mcp_config, provider=provider
            )
        _RUNTIME_CACHE["config"] = runtime_config
    return _RUNTIME_CACHE["config"]


async def _close_shared_runtime() -> None:
    runtime_config = _RUNTIME_CACHE.pop("config", None)
    provider = getattr(runtime_config, "mcp_tool_provider", None)
    aclose = getattr(provider, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except Exception:  # noqa: BLE001 - 收尾失败不该影响已经算出来的分数
            pass


async def _run_graph(
    item: dict[str, Any], *, model: Any = None, model_name: str, provider: str | None = None
) -> list[Any]:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.chat.models import ChatStreamRequest
    from app.chat.run_config import build_run_config
    from app.chat.runtime.adapter import create_langgraph_agent_graph

    request = ChatStreamRequest(
        message=item["turns"][0], model_name=model_name, provider=provider, mode="pro"
    )
    bundle = build_run_config(
        thread_id=f"eval-{item['id']}", run_id=f"run-{item['id']}", request=request
    )
    if model is None:  # live:用真模型(显式 provider,避免 grok 被 litellm 误判为 xai 直连)
        from app.chat.runtime.models import create_chat_model

        model = create_chat_model(model_name, run_context=bundle.context, provider=provider)

    # 走**和后端完全同一条**建图路径:同一个 runtime_config、同一个
    # create_langgraph_agent_graph、同一套 MCP 预加载,而不是自己拼一个精简版
    # SlotFlowHarnessConfig。
    #
    # 2026-08-14 踩到:原来这里只传了 system_prompt,于是 skills_root / MCP / 记忆全是空的——
    # skill-two-step 里 `skill_list` 返回空列表、`skill_read` 报 skills_root_not_configured,
    # 看报表像是 agent 不会用 Skill,其实是评测跑的根本不是产品那张图。
    # 评测的价值取决于它跑的东西和线上有多像,这种"跑了个简化版"的偏差最难发现。
    runtime_config = await _shared_runtime()
    graph = create_langgraph_agent_graph(
        model=model,
        run_context=bundle.context,
        runtime_config=runtime_config,
        checkpointer=InMemorySaver(),
        mcp_tool_provider=runtime_config.mcp_tool_provider,
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
        _provision_workspace(item)
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
    items: list[dict[str, Any]], model_name: str, *, provider: str, use_judge: bool,
    concurrency: int = 4,
) -> list[ItemScore]:
    from app.chat.runtime.models import create_chat_model

    judge = create_chat_model(model_name, provider=provider) if use_judge else None
    ctx = EvalContext(provider=_provider_of(model_name), judge_model=judge)

    # 中转有并发限流(超了直接回 429,而且它不报错、只给一个空补全),硬上限 5。
    limit = max(1, min(int(concurrency), MAX_LIVE_CONCURRENCY))

    # `env_overrides` 改的是**进程级** os.environ(压缩阈值那几条),并发跑会互相串味:
    # 一条样本把 SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS 调到 1200,同时在跑的其它样本
    # 也会跟着被压缩。所以按有没有 overrides 分两拨:无副作用的并发跑,有的串行跑。
    parallel_items = [item for item in items if not item.get("env_overrides")]
    serial_items = [item for item in items if item.get("env_overrides")]
    results: dict[str, ItemScore] = {}

    async def score_one(item: dict[str, Any]) -> None:
        try:
            transcript = await _run_live_item(item, model_name, provider)
            s = score_item(transcript, item, ctx=ctx)
            print(f"[live] {item['id']} … {s.passed_count}/{s.total} {'PASS' if s.ok else 'FAIL'}", flush=True)
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整轮评测
            from evals.evaluators import EvalResult

            s = ItemScore(item_id=item["id"], tags=list(item.get("tags", [])))
            s.results.append(EvalResult("run", passed=False, detail=f"运行异常:{type(exc).__name__}: {exc}"))
            print(f"[live] {item['id']} … 运行异常:{exc}", flush=True)
        results[item["id"]] = s

    semaphore = asyncio.Semaphore(limit)

    async def guarded(item: dict[str, Any]) -> None:
        async with semaphore:
            await score_one(item)

    if parallel_items:
        print(f"[live] 并发 {limit} 跑 {len(parallel_items)} 条(无 env_overrides)")
        await asyncio.gather(*(guarded(item) for item in parallel_items))
    if serial_items:
        print(f"[live] 串行跑 {len(serial_items)} 条(带 env_overrides,改进程级环境变量)")
        for item in serial_items:
            await score_one(item)

    await _close_shared_runtime()
    return [results[item["id"]] for item in items if item["id"] in results]


def main() -> None:
    parser = argparse.ArgumentParser(description="SlotFlow agent 评测 runner")
    parser.add_argument("--smoke", action="store_true", help="真图 + FakeModel 验证接线")
    parser.add_argument("--live", action="store_true", help="真模型跑全部样本")
    parser.add_argument("--model", default="", help="live 模型名(默认跟随 .env 的 CUSTOM_MODELS)")
    parser.add_argument("--provider", default="custom", help="live provider(中转模型用 custom)")
    parser.add_argument("--judge", action="store_true", help="live 时启用 LLM-as-judge")
    parser.add_argument("--langsmith", action="store_true", help="live 时把 trace 推到 LangSmith(需已配 key)")
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help=f"并发样本数(默认 4,硬上限 {MAX_LIVE_CONCURRENCY};带 env_overrides 的样本始终串行)",
    )
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
        # .env 载入之后才能解析默认模型(它读的是 CUSTOM_MODELS)。
        model_name = args.model or default_live_model()
        print(f"[live] 已从 .env 载入 {n} 个环境变量;模型={model_name}")
        if args.langsmith:
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ.setdefault("LANGSMITH_PROJECT", "SlotFlow")
            print(f"[live] 已开启 LangSmith 追踪 → project={os.environ.get('LANGSMITH_PROJECT')}")
        scores = asyncio.run(
            run_live(
                items, model_name, provider=args.provider,
                use_judge=args.judge, concurrency=args.concurrency,
            )
        )
        print_scorecard(scores, title=f"live(真模型 {model_name})")
        return

    scores = run_offline(items)
    print_scorecard(scores, title="offline(桩 transcript,确定性)")
    print("\n注:offline 的 transcript 是人工编造的,只证明评测器/打分/报表本身正确;")
    print("    reasoning-roundtrip 是**故意造的红项**(content 里塞了 thinking 块),")
    print("    用来演示评测器确实抓得到——一套全绿的评测集是证明不了自己有效的。")
    print("    真实 agent 分数请跑 --live 或 python -m evals.langsmith_eval。")


if __name__ == "__main__":
    main()
