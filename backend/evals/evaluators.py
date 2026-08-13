"""评测器实现 + 从 transcript 抽取信号的工具。

评测器统一签名:``fn(transcript, item, params, *, ctx) -> EvalResult``。
``transcript`` 是一次运行产生的原生 message 列表(``result["messages"]`` 或离线桩)。
``ctx`` 携带运行期信息(provider、judge_model 等)。

设计原则(面试要点):**能用代码判的绝不用 LLM 判**——
下面 6 个里只有最后一个 ``llm_judge`` 是 LLM-as-judge,其余全是确定性代码评测器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


@dataclass(slots=True)
class EvalContext:
    """运行期上下文:provider 决定是否允许 reasoning_content;judge_model 可选。"""

    provider: str = "custom"
    judge_model: Any | None = None  # BaseChatModel;None 时 llm_judge 跳过


@dataclass(slots=True)
class EvalResult:
    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False


# --------------------------------------------------------------------------- #
# transcript 抽取器
# --------------------------------------------------------------------------- #
def attempted_tool_names(transcript: list[Any]) -> list[str]:
    """模型**试图调用**的工具名(从 AIMessage.tool_calls 收集,含 loader)。"""

    names: list[str] = []
    for msg in transcript:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                name = tc.get("name") if isinstance(tc, dict) else None
                if name:
                    names.append(name)
    return names


def tool_execution_errors(transcript: list[Any]) -> list[str]:
    """工具执行失败记录:ToolMessage.status == error,或内容含 tool_not_activated。"""

    errors: list[str] = []
    for msg in transcript:
        if isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            is_error = getattr(msg, "status", None) == "error" or "tool_not_activated" in content
            if is_error:
                errors.append(f"{msg.name}: {content[:60]}")
    return errors


def final_answer_text(transcript: list[Any]) -> str:
    """最后一条"无 tool_calls 的 AIMessage"的文本;content 为 list 时只取 text 段。"""

    for msg in reversed(transcript):
        if isinstance(msg, AIMessage) and not (msg.tool_calls or []):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    part if isinstance(part, str) else str(part.get("text") or "")
                    for part in content
                    if isinstance(part, (str, dict))
                )
    return ""


def _reasoning_bloat_reason(transcript: list[Any], provider: str) -> str:
    """返回非空字符串表示"存在回灌膨胀";空串表示干净。仅对非 deepseek 生效。"""

    for msg in transcript:
        if not isinstance(msg, AIMessage):
            continue
        kwargs = msg.additional_kwargs or {}
        if provider != "deepseek" and "reasoning_content" in kwargs:
            return "落库消息残留 reasoning_content"
        if isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") in {"thinking", "reasoning", "redacted_thinking"}:
                    return f"落库 content 残留 {block.get('type')} 块"
    return ""


# --------------------------------------------------------------------------- #
# 评测器
# --------------------------------------------------------------------------- #
def eval_expects_tools(transcript, item, params, *, ctx) -> EvalResult:
    """期望的工具是否都被调用过(默认 superset:期望 ⊆ 实际)。"""

    expected = set(params["names"])
    attempted = set(attempted_tool_names(transcript))
    missing = expected - attempted
    return EvalResult(
        "expects_tools",
        passed=not missing,
        detail="" if not missing else f"缺少工具调用:{sorted(missing)};实际:{sorted(attempted)}",
    )


def eval_forbids_tools(transcript, item, params, *, ctx) -> EvalResult:
    """不应调用任何工具(精度:防过度触发)。"""

    attempted = attempted_tool_names(transcript)
    return EvalResult(
        "forbids_tools",
        passed=not attempted,
        detail="" if not attempted else f"不该调用工具却调用了:{attempted}",
    )


def eval_no_tool_errors(transcript, item, params, *, ctx) -> EvalResult:
    """没有工具执行失败(tool_not_activated 会在这里被抓到 → 直指 Issue-1)。"""

    errors = tool_execution_errors(transcript)
    return EvalResult(
        "no_tool_errors",
        passed=not errors,
        detail="" if not errors else "; ".join(errors),
    )


def eval_answer_contains(transcript, item, params, *, ctx) -> EvalResult:
    """终答是否包含关键子串(mode=all 全含 / any 含其一)。"""

    answer = final_answer_text(transcript)
    subs = params["substrings"]
    hit = [s for s in subs if s in answer]
    mode = params.get("mode", "all")
    passed = (len(hit) == len(subs)) if mode == "all" else bool(hit)
    return EvalResult(
        "answer_contains",
        passed=passed,
        detail="" if passed else f"终答缺关键词({mode}={subs});命中={hit};答案={answer[:50]!r}",
    )


def eval_no_reasoning_bloat(transcript, item, params, *, ctx) -> EvalResult:
    """落库消息不得回灌思考(reasoning_content / thinking 块)——防上下文膨胀契约。"""

    reason = _reasoning_bloat_reason(transcript, ctx.provider)
    return EvalResult("no_reasoning_bloat", passed=not reason, detail=reason)


_JUDGE_PROMPT = """你是严格的评分员。判断【实际回答】是否在语义上正确回应了【问题】,并与【参考答案】一致。
只输出一个 JSON:{{"score": 0 或 1, "reason": "简短理由"}}。score=1 表示正确,0 表示错误或答非所问。

【问题】{question}
【参考答案】{reference}
【实际回答】{answer}
"""


def eval_llm_judge(transcript, item, params, *, ctx) -> EvalResult:
    """LLM-as-judge:仅当提供了 judge_model 时运行,否则跳过(不计分)。"""

    if ctx.judge_model is None:
        return EvalResult("llm_judge", passed=True, detail="无 judge_model,跳过", skipped=True)
    answer = final_answer_text(transcript)
    prompt = _JUDGE_PROMPT.format(
        question=item["turns"][-1], reference=item.get("reference", ""), answer=answer
    )
    try:
        raw = ctx.judge_model.invoke(prompt)
        text = raw.content if hasattr(raw, "content") else str(raw)
        text = text if isinstance(text, str) else str(text)
        score = 1 if '"score": 1' in text.replace(" ", "").replace('"score":1', '"score": 1') or '"score":1' in text.replace(" ", "") else 0
        return EvalResult("llm_judge", passed=bool(score), detail=text[:80])
    except Exception as exc:  # noqa: BLE001 - judge 失败不该让整条评测崩
        return EvalResult("llm_judge", passed=True, detail=f"judge 异常,跳过:{exc}", skipped=True)


_REGISTRY = {
    "expects_tools": eval_expects_tools,
    "forbids_tools": eval_forbids_tools,
    "no_tool_errors": eval_no_tool_errors,
    "answer_contains": eval_answer_contains,
    "no_reasoning_bloat": eval_no_reasoning_bloat,
    "llm_judge": eval_llm_judge,
}


@dataclass(slots=True)
class ItemScore:
    item_id: str
    tags: list[str]
    results: list[EvalResult] = field(default_factory=list)

    @property
    def scored(self) -> list[EvalResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.scored if r.passed)

    @property
    def total(self) -> int:
        return len(self.scored)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.scored)


def score_item(transcript: list[Any], item: dict[str, Any], *, ctx: EvalContext) -> ItemScore:
    """对一条样本的 transcript 跑完它声明的所有评测器。"""

    score = ItemScore(item_id=item["id"], tags=list(item.get("tags", [])))
    for name, params in item["evaluators"]:
        fn = _REGISTRY[name]
        score.results.append(fn(transcript, item, params, ctx=ctx))
    return score
