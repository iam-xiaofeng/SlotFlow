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
    """工具执行失败记录:ToolMessage.status == error,或内容里带 tool_execution_error。"""

    errors: list[str] = []
    for msg in transcript:
        if isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            is_error = getattr(msg, "status", None) == "error" or "tool_execution_error" in content
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


def _thinking_blocks_reason(transcript: list[Any]) -> str:
    """content 里残留 thinking/reasoning 块 → 返回非空理由。

    这条契约**永远成立**,与 provider 无关:块列表对 OpenAI 风格的 Chat Completions 是线路非法
    (``unknown variant 'reasoning'`` → 400),也是上下文膨胀的大头。``sanitize_reasoning_message``
    永远会把它从 content 里剥掉。
    """

    for msg in transcript:
        if not isinstance(msg, AIMessage) or not isinstance(msg.content, list):
            continue
        for block in msg.content:
            if isinstance(block, dict) and block.get("type") in {
                "thinking",
                "reasoning",
                "redacted_thinking",
            }:
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


def eval_expects_any_tool(transcript, item, params, *, ctx) -> EvalResult:
    """候选工具里**至少调用一个**即通过。

    存在的理由:2026-07-27 那轮 live 有两条"失败"其实是评测器太严——grok 用 ``artifact_write``
    建文件(完全合理)却被只认单一工具名的期望判红。一个任务往往有多条合理路径,评测器锁死
    某一条,量到的就不是 agent 能力而是它跟我的猜测有多像。
    """

    candidates = set(params["names"])
    attempted = set(attempted_tool_names(transcript))
    hit = candidates & attempted
    return EvalResult(
        "expects_any_tool",
        passed=bool(hit),
        detail="" if hit else f"候选 {sorted(candidates)} 一个都没调;实际:{sorted(attempted)}",
    )


def eval_forbids_tools(transcript, item, params, *, ctx) -> EvalResult:
    """不该调用工具。``names`` 省略时表示"任何工具都不该调"(精度:防过度触发)。"""

    attempted = attempted_tool_names(transcript)
    banned = set(params.get("names") or [])
    hit = [n for n in attempted if n in banned] if banned else attempted
    return EvalResult(
        "forbids_tools",
        passed=not hit,
        detail="" if not hit else f"不该调用却调用了:{sorted(set(hit))}",
    )


def eval_max_tool_calls(transcript, item, params, *, ctx) -> EvalResult:
    """工具调用总数不得超过上限——"答完就收工"的可量化代理指标。

    钉的是 2026-08-14 删掉的 todo 强制门:那道门只在模型已经写完最终答案时触发,把完成的
    回合重新拽开,真机上一句「这是什么」被拽了两次、同一个问题答了三遍、9 次模型调用。
    """

    limit = int(params["max"])
    attempted = attempted_tool_names(transcript)
    return EvalResult(
        "max_tool_calls",
        passed=len(attempted) <= limit,
        detail="" if len(attempted) <= limit else f"调了 {len(attempted)} 次工具(上限 {limit}):{attempted}",
    )


def eval_no_tool_errors(transcript, item, params, *, ctx) -> EvalResult:
    """没有工具执行失败(任何 status=error 的 ToolMessage 都会在这里被抓到)。"""

    errors = tool_execution_errors(transcript)
    return EvalResult(
        "no_tool_errors",
        passed=not errors,
        detail="" if not errors else "; ".join(errors),
    )


def eval_tool_result_capped(transcript, item, params, *, ctx) -> EvalResult:
    """任何单条工具结果都不得超过字符上限。

    钉的是 2026-08-14 修的那个洞:``workspace_read`` 曾经完全没有上限,一个 446KB 的上传文件
    被整段内联成 373K 字符的 ToolMessage(≈166k token),之后模型每次返回空响应、thread 被
    永久毒化。上限本身留了余量(卸载句柄 + 分页提示也占字符)。
    """

    limit = int(params.get("max_chars", 32_000))
    oversized = [
        f"{msg.name}: {len(msg.content if isinstance(msg.content, str) else str(msg.content))} 字符"
        for msg in transcript
        if isinstance(msg, ToolMessage)
        and len(msg.content if isinstance(msg.content, str) else str(msg.content)) > limit
    ]
    return EvalResult(
        "tool_result_capped",
        passed=not oversized,
        detail="" if not oversized else f"工具结果超过 {limit} 字符上限:{oversized}",
    )


def eval_no_empty_assistant(transcript, item, params, *, ctx) -> EvalResult:
    """不得出现"既无正文也无工具调用"的空 assistant 消息。

    钉的是同一次真机事故的第二层:空响应会静默终结整轮(路由 finalize → 落库判空 → 一条都不存),
    而且那条空消息会进 checkpoint 把 thread 永久毒化。现在 agent 节点当场抛异常,空消息不进 state。
    """

    empties = [
        idx
        for idx, msg in enumerate(transcript)
        if isinstance(msg, AIMessage)
        and not (msg.tool_calls or [])
        and not _message_text(msg.content).strip()
    ]
    return EvalResult(
        "no_empty_assistant",
        passed=not empties,
        detail="" if not empties else f"存在空 assistant 消息,位置:{empties}",
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text") or "")
            for part in content
            if isinstance(part, (str, dict))
        )
    return ""


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


def eval_no_thinking_blocks(transcript, item, params, *, ctx) -> EvalResult:
    """落库消息的 content 里不得残留 thinking / reasoning 块。

    ⚠️ 注意这条**只管 content 块**,不再管 ``additional_kwargs["reasoning_content"]``。
    2026-08-14 起后者是**默认保留**的(见 ``reasoning_preserved``):原来的白名单只认
    ``provider == "deepseek"``,而经 OpenAI 兼容中转访问的 DeepSeek 上报 ``"custom"``,
    唯一硬性要求回传该字段的 provider 恰恰被剥掉了。两件事必须分开评,合在一个
    ``no_reasoning_bloat`` 里会让新契约和旧契约互相打架。
    """

    reason = _thinking_blocks_reason(transcript)
    return EvalResult("no_thinking_blocks", passed=not reason, detail=reason)


def eval_reasoning_preserved(transcript, item, params, *, ctx) -> EvalResult:
    """思考模式下,落库的 assistant 消息应保留 ``reasoning_content`` 载体。

    这是 2026-08-14 翻转后的新契约:checkpoint 里有它,``llm_input_messages``(从 checkpoint
    投影)自然也有,模型才看得到自己上一步想过什么;DeepSeek 的 API 更是硬性要求每轮回传。
    没有任何 assistant 消息带思考时跳过——模型可能本来就没开思考模式,不该算失败。
    """

    ai_messages = [m for m in transcript if isinstance(m, AIMessage)]
    with_reasoning = [m for m in ai_messages if (m.additional_kwargs or {}).get("reasoning_content")]
    if not with_reasoning:
        return EvalResult(
            "reasoning_preserved",
            passed=True,
            detail="本轮没有任何带思考的 assistant 消息,跳过",
            skipped=True,
        )
    return EvalResult(
        "reasoning_preserved",
        passed=True,
        detail=f"{len(with_reasoning)}/{len(ai_messages)} 条 assistant 消息保留了 reasoning_content",
    )



_JUDGE_PROMPT = """你是严格的评分员。判断【实际回答】是否在语义上正确回应了【问题】,并与【参考答案】一致。
只输出一个 JSON:{{"score": 0 或 1, "reason": "简短理由"}}。score=1 表示正确,0 表示错误或答非所问。

【问题】{question}
【参考答案】{reference}
【实际回答】{answer}
"""


def _parse_judge_score(text: str) -> bool | None:
    """从裁判输出里解析 score。解析不出来返回 None(记为跳过,不算失败)。

    之前这里是一串 ``replace(" ", "")`` 拼出来的子串匹配,``{"score": 10}`` 之类会被误判成 1,
    裁判改用 markdown 代码块包 JSON 也会漏。改成先规规矩矩找 JSON,再退回宽松正则。
    """

    import json
    import re

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.S)
        candidate = brace.group(0) if brace else None
    if candidate:
        try:
            score = json.loads(candidate).get("score")
            if isinstance(score, (int, float)):
                return bool(int(score))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            pass
    loose = re.search(r'"?score"?\s*[:=]\s*([01])\b', text)
    return bool(int(loose.group(1))) if loose else None


def eval_llm_judge(transcript, item, params, *, ctx) -> EvalResult:
    """LLM-as-judge:仅当提供了 judge_model 时运行,否则跳过(不计分)。

    只用在"对不对没法用代码判"的地方(语义正确性)。能用代码判的一律用确定性评测器——
    裁判本身会漂,把它当通用打分器会让整套评测失去可复现性。
    """

    if ctx.judge_model is None:
        return EvalResult("llm_judge", passed=True, detail="无 judge_model,跳过", skipped=True)
    answer = final_answer_text(transcript)
    if not answer.strip():
        return EvalResult("llm_judge", passed=False, detail="终答为空,无可评判内容")
    prompt = _JUDGE_PROMPT.format(
        question=item["turns"][-1], reference=item.get("reference", ""), answer=answer
    )
    try:
        raw = ctx.judge_model.invoke(prompt)
        text = raw.content if hasattr(raw, "content") else str(raw)
        text = text if isinstance(text, str) else str(text)
        score = _parse_judge_score(text)
        if score is None:
            return EvalResult(
                "llm_judge", passed=True, detail=f"裁判输出无法解析,跳过:{text[:80]}", skipped=True
            )
        return EvalResult("llm_judge", passed=score, detail=text[:120])
    except Exception as exc:  # noqa: BLE001 - judge 失败不该让整条评测崩
        return EvalResult("llm_judge", passed=True, detail=f"judge 异常,跳过:{exc}", skipped=True)


_REGISTRY = {
    "expects_tools": eval_expects_tools,
    "expects_any_tool": eval_expects_any_tool,
    "forbids_tools": eval_forbids_tools,
    "max_tool_calls": eval_max_tool_calls,
    "no_tool_errors": eval_no_tool_errors,
    "tool_result_capped": eval_tool_result_capped,
    "no_empty_assistant": eval_no_empty_assistant,
    "answer_contains": eval_answer_contains,
    "no_thinking_blocks": eval_no_thinking_blocks,
    "reasoning_preserved": eval_reasoning_preserved,
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
