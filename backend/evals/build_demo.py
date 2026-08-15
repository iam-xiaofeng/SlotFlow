"""跑一组演示场景,把**真实** transcript 导出成静态展示页用的 JSON。

产物:``demo/transcripts.js``(挂到 `window.SLOTFLOW_DEMO`,静态页直接读,不需要后端)。

用法::

    cd backend
    uv run python -m evals.build_demo --list                 # 看有哪些场景
    uv run python -m evals.build_demo                        # 全跑并导出
    uv run python -m evals.build_demo --only frontend-page   # 只跑一条
    uv run python -m evals.build_demo --keep                 # 保留已有结果,只补跑缺的

**只导出真实运行结果**,不编造。中转限流时会返回空补全(见 evals/README.md ③),
所以导出前会做一次清洗:丢掉空 assistant 消息、剔除 429/限流/连接错误这类与产品能力无关的
噪音——展示页是给人看"这个 agent 能做什么"的,基础设施抖动不属于要展示的内容。
清洗只删噪音,绝不改写模型说过的话。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from evals.dataset import dataset_by_id  # noqa: E402
from evals.run_eval import (  # noqa: E402
    _close_shared_runtime,
    _run_live_item,
    default_live_model,
    load_dotenv,
)

DEMO_OUT = _BACKEND.parent / "demo" / "transcripts.js"

# 展示页要讲的故事。前 8 条复用评测样本(真实跑过、有据可依),后 2 条是专门为展示写的复杂长任务。
SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "offload-recall",
        "from_dataset": True,
        "title": "超长工具结果:中段被省略后再取回",
        "blurb": (
            "沙箱打印 4 万字符,超过阈值后整段结果被卸载成文件句柄,只在上下文里留 head+tail 预览。"
            "暗号藏在正中间(第 1001 行)——必然落在被省略的那段里,模型只能回头用工具去取。"
        ),
        "highlight": "上下文工程",
    },
    {
        "id": "context-archive-recall",
        "from_dataset": True,
        "title": "压缩之后,早期细节还能捞回来",
        "blurb": (
            "把压缩阈值压到 1200 token 逼出真实压缩,中间灌两轮长对话把开头挤出窗口,"
            "最后回头问第一轮给的项目代号和端口。"
        ),
        "highlight": "上下文工程",
    },
    {
        "id": "read-file-paginated",
        "from_dataset": True,
        "title": "大文件不整段读:分页与检索",
        "blurb": "工作区里放一个超过单次读取上限的文件,看它是整段灌进上下文还是按需检索。",
        "highlight": "工具使用",
    },
    {
        "id": "sandbox-verify",
        "from_dataset": True,
        "title": "写完代码在沙箱里真跑一遍",
        "blurb": "不是「看起来对」,而是在 Docker 沙箱里执行用例、拿到真实 stdout 再下结论。",
        "highlight": "工具使用",
    },
    {
        "id": "skill-two-step",
        "from_dataset": True,
        "title": "Skills 两段式:目录常驻,正文按需",
        "blurb": (
            "Skill 目录常驻系统前缀(发现),正文只在需要时用 skill_read 走工具结果(读取)。"
            "这样前缀逐字节稳定,provider 的前缀缓存才可能命中。"
        ),
        "highlight": "架构",
    },
    {
        "id": "clarify-keeps-thinking",
        "from_dataset": True,
        "title": "信息不足时先问,而且思考不丢",
        "blurb": (
            "指令有歧义时主动澄清(HITL,走 LangGraph 原生 interrupt/resume);"
            "澄清那条消息本身带着它的思考——这串「思考框消失」的 bug 修了四个根因。"
        ),
        "highlight": "HITL",
    },
    {
        "id": "refuse-unknowable",
        "from_dataset": True,
        "title": "读不到就说读不到",
        "blurb": "问一个不存在的文件,看它是承认拿不到,还是编一段出来。",
        "highlight": "可靠性",
    },
    {
        "id": "no-tool-chat",
        "from_dataset": True,
        "title": "不该调工具时别乱调",
        "blurb": "精度的另一面:工具多不等于什么都要用,简单问答就该直接答。",
        "highlight": "可靠性",
    },
    # ---- 专门为展示写的两个复杂长任务 ----
    {
        "id": "frontend-page",
        "title": "复杂任务:做一个精美的落地页",
        "blurb": "多步任务:规划 → 写代码 → 产出 artifact。看它怎么把一个开放式设计需求落成可运行的文件。",
        "highlight": "复杂任务",
        "turns": [
            "帮我做一个单文件的产品落地页(纯 HTML + 内联 CSS,不要外部依赖),主题是一个叫 "
            "「Lumen」的智能台灯。要求:深色系、有渐变和微妙的光晕效果、首屏有大标题和 CTA 按钮、"
            "下面三个特性卡片、最后一个简洁页脚;响应式,手机上不能错位。"
            "先规划再动手,最后用 artifact_write 产出成品文件。",
            "不错。再改两处:把 CTA 按钮加一个 hover 时的辉光动画,另外三个特性卡片改成"
            "鼠标移上去会轻微上浮。改完重新产出完整文件。",
        ],
    },
    {
        "id": "agent-research",
        # 这条要多轮联网检索,上下文涨得快,正好撞在中转的空补全上(见 evals/README.md ③)。
        # 单独把空响应重试拉高,不改全局默认。
        "env_overrides": {"SLOTFLOW_EMPTY_RESPONSE_MAX_RETRIES": "6"},
        "title": "复杂任务:深度调研各家 Agent 框架的差异",
        "blurb": "开放式研究:多轮联网检索 + 交叉比对 + 结构化结论,并且要说清楚证据来自哪里。",
        "highlight": "复杂任务",
        "turns": [
            "深度调研一下现在主流的 AI Agent 框架/产品之间的核心差异,重点比较 "
            "LangGraph、OpenAI Agents SDK、Claude Agent SDK 这三个。"
            "我要的不是功能罗列,而是它们在**编排模型**(图/循环/托管)、**状态与持久化**、"
            "**人在环路(HITL)** 这三个维度上的设计取舍。先规划,联网查证,最后给结论。",
            "针对「状态与持久化」这一维度再展开讲讲:它们各自怎么处理长对话的上下文膨胀?"
            "有没有内置的压缩/摘要机制?给出具体做法和它们的代价。",
        ],
    },
]

# 与产品能力无关的基础设施噪音:限流、连接抖动、空补全导致的重试提示。
_NOISE_PATTERNS = [
    re.compile(r"429|rate.?limit|too many requests", re.I),
    re.compile(r"InternalServerError|Connection error|APIConnectionError|ReadTimeout", re.I),
    re.compile(r"模型返回了空响应"),
    re.compile(r"litellm\.[A-Za-z]*Error"),
]


def _is_noise(text: str) -> bool:
    return any(pattern.search(text) for pattern in _NOISE_PATTERNS)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text") or "")
            for part in content
            if isinstance(part, (str, dict))
        )
    return ""


def serialize_message(message: Any) -> dict[str, Any] | None:
    """把一条 LangChain message 压成展示页需要的最小形状;噪音与空消息返回 None。

    ``write_todos`` 和 ``ask_clarification`` 会额外抽成结构化字段(``todos`` / ``clarification``),
    展示页据此渲染成 todo 面板和澄清卡片——它们在真实前端里就是专属 UI,不是普通工具结果。
    """

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    if isinstance(message, HumanMessage):
        text = _text_of(message.content).strip()
        # 上传/系统注入的 <slotflow-*> 协议块是给模型看的,不该出现在展示页。
        text = re.sub(r"<slotflow-[^>]*>.*?</slotflow-[^>]*>", "", text, flags=re.S).strip()
        return {"role": "user", "content": text} if text else None

    if isinstance(message, AIMessage):
        text = _text_of(message.content).strip()
        reasoning = (message.additional_kwargs or {}).get("reasoning_content") or ""
        calls: list[dict[str, Any]] = []
        todos: list[dict[str, str]] = []
        clarification: dict[str, Any] | None = None
        artifacts: list[dict[str, str]] = []
        for tool_call in message.tool_calls or []:
            name = tool_call.get("name")
            if not name:
                continue
            args = tool_call.get("args") or {}
            if name == "write_todos":
                todos = _normalize_todos(args.get("todos"))
            elif name == "ask_clarification":
                clarification = _normalize_clarification(args)
            elif name == "artifact_write":
                artifact = _normalize_artifact(args)
                if artifact:
                    artifacts.append(artifact)
            calls.append({"name": name, "args": args})
        if not text and not calls:
            return None  # 空补全(中转限流的产物),不展示
        if text and _is_noise(text):
            return None
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "reasoning": reasoning.strip(),
            "tool_calls": calls,
        }
        if todos:
            payload["todos"] = todos
        if clarification:
            payload["clarification"] = clarification
        if artifacts:
            payload["artifacts"] = artifacts
        return payload

    if isinstance(message, ToolMessage):
        body = _text_of(message.content)
        if _is_noise(body):
            body = "(该次调用因基础设施抖动被重试,此处略去)"
        return {
            "role": "tool",
            "name": message.name or "tool",
            "content": body,
            "status": getattr(message, "status", None) or "success",
        }

    return None


def _normalize_todos(value: Any) -> list[dict[str, str]]:
    todos: list[dict[str, str]] = []
    if not isinstance(value, list):
        return todos
    for entry in value:
        if isinstance(entry, dict):
            content = str(entry.get("content") or entry.get("text") or "").strip()
            status = str(entry.get("status") or "pending")
        else:
            content, status = str(entry).strip(), "pending"
        if content:
            todos.append({"content": content, "status": status})
    return todos


def _normalize_artifact(args: dict[str, Any]) -> dict[str, str] | None:
    """抽出 artifact_write 的产物,展示页可以直接把 HTML 成品渲染出来预览。"""

    content = str(args.get("content") or "")
    if not content.strip():
        return None
    title = str(args.get("title") or args.get("path") or "artifact").strip()
    path = str(args.get("path") or "").strip()
    lowered = f"{title} {path}".lower()
    is_html = "<html" in content[:600].lower() or ".html" in lowered
    return {
        "title": title,
        "path": path,
        "kind": "html" if is_html else "text",
        "content": content,
    }


def _normalize_clarification(args: dict[str, Any]) -> dict[str, Any]:
    options: list[dict[str, str]] = []
    for index, option in enumerate(args.get("options") or []):
        if isinstance(option, dict):
            label = str(option.get("label") or option.get("text") or "").strip()
            oid = str(option.get("id") or chr(ord("A") + index))
        else:
            label, oid = str(option).strip(), chr(ord("A") + index)
        if label:
            options.append({"id": oid, "label": label})
    return {
        "question": str(args.get("question") or "").strip(),
        "context": str(args.get("context") or "").strip(),
        "options": options,
    }


def serialize_thread(scenario: dict[str, Any], transcript: list[Any]) -> dict[str, Any]:
    messages = [m for m in (serialize_message(m) for m in transcript) if m]
    tools_used: list[str] = []
    for m in messages:
        for call in m.get("tool_calls") or []:
            if call["name"] not in tools_used:
                tools_used.append(call["name"])
    # todo 面板取**最后一次** write_todos 的快照:它是这一轮结束时的计划状态。
    final_todos: list[dict[str, str]] = []
    for m in messages:
        if m.get("todos"):
            final_todos = m["todos"]
    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "blurb": scenario["blurb"],
        "highlight": scenario["highlight"],
        "messages": messages,
        "tools_used": tools_used,
        "final_todos": final_todos,
        "stats": {
            "turns": sum(1 for m in messages if m["role"] == "user"),
            "tool_calls": sum(len(m.get("tool_calls") or []) for m in messages),
            "thinking_chars": sum(len(m.get("reasoning") or "") for m in messages),
        },
    }


def scenario_to_item(scenario: dict[str, Any]) -> dict[str, Any]:
    """演示场景 → `_run_live_item` 认识的样本形状。"""

    if scenario.get("from_dataset"):
        item = dict(dataset_by_id()[scenario["id"]])
        item["id"] = f"demo-{scenario['id']}"
        return item
    return {
        "id": f"demo-{scenario['id']}",
        "turns": scenario["turns"],
        "evaluators": [],
        "env_overrides": scenario.get("env_overrides"),
        "workspace_files": scenario.get("workspace_files"),
    }


def reprocess(threads: dict[str, Any]) -> list[dict[str, Any]]:
    """不重跑模型,只把已导出结果里的 ``tool_calls`` 重新抽成结构化字段。

    抽取逻辑(todo 面板 / 澄清卡片 / artifact 预览)是在第一批跑完之后才补上的,而跑批的进程
    用的是当时导入的旧代码。原始 tool_calls 已经完整存在导出结果里,所以重新抽一遍即可——
    数据还是那次真实运行的数据,不需要再花一次模型调用。
    """

    out: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        thread = threads.get(scenario["id"])
        if not thread:
            continue
        for message in thread.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            todos: list[dict[str, str]] = []
            clarification: dict[str, Any] | None = None
            artifacts: list[dict[str, str]] = []
            for call in message.get("tool_calls") or []:
                args = call.get("args") or {}
                if call.get("name") == "write_todos":
                    todos = _normalize_todos(args.get("todos"))
                elif call.get("name") == "ask_clarification":
                    clarification = _normalize_clarification(args)
                elif call.get("name") == "artifact_write":
                    artifact = _normalize_artifact(args)
                    if artifact:
                        artifacts.append(artifact)
            message.pop("todos", None)
            message.pop("clarification", None)
            message.pop("artifacts", None)
            if todos:
                message["todos"] = todos
            if clarification:
                message["clarification"] = clarification
            if artifacts:
                message["artifacts"] = artifacts
        final_todos: list[dict[str, str]] = []
        for message in thread.get("messages") or []:
            if message.get("todos"):
                final_todos = message["todos"]
        thread["final_todos"] = final_todos
        # 标题/引子以代码里的 SCENARIOS 为准,方便改文案不用重跑。
        thread["title"] = scenario["title"]
        thread["blurb"] = scenario["blurb"]
        thread["highlight"] = scenario["highlight"]
        out.append(thread)
    return out


def load_existing() -> dict[str, Any]:
    if not DEMO_OUT.exists():
        return {}
    raw = DEMO_OUT.read_text(encoding="utf-8")
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return {thread["id"]: thread for thread in payload.get("threads", [])}


def write_output(threads: list[dict[str, Any]], *, model: str) -> None:
    DEMO_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "threads": threads,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    DEMO_OUT.write_text(
        "// 由 `uv run python -m evals.build_demo` 生成 —— 真实运行结果,请勿手改。\n"
        f"window.SLOTFLOW_DEMO = {body};\n",
        encoding="utf-8",
    )
    print(f"已写出 {DEMO_OUT}({len(threads)} 个 thread,{DEMO_OUT.stat().st_size / 1024:.0f} KB)")


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv(_BACKEND / ".env")
    model = args.model or default_live_model()
    print(f"[demo] 模型={model}")

    scenarios = SCENARIOS
    # `--only X` = 只重跑 X,其余全部沿用已导出的结果(而不是把它们冲掉)。
    # 中转会偶发空补全,一条失败就得重跑,没有这个语义每次都要全量重来。
    only = args.only
    if only:
        scenarios = [s for s in scenarios if s["id"] == only]
        if not scenarios:
            print(f"没有场景 {only}")
            return 1

    existing = load_existing() if (args.keep or only) else {}
    threads: list[dict[str, Any]] = []
    for scenario in scenarios:
        if args.keep and scenario["id"] in existing and existing[scenario["id"]].get("messages"):
            print(f"[demo] {scenario['id']} … 复用已有结果")
            threads.append(existing[scenario["id"]])
            continue
        print(f"[demo] {scenario['id']} … 运行中", flush=True)
        try:
            transcript = await _run_live_item(scenario_to_item(scenario), model, args.provider)
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批
            print(f"[demo] {scenario['id']} … 失败:{type(exc).__name__}: {exc}")
            if args.keep and scenario["id"] in existing:
                threads.append(existing[scenario["id"]])
            continue
        thread = serialize_thread(scenario, transcript)
        stats = thread["stats"]
        print(
            f"[demo] {scenario['id']} … {len(thread['messages'])} 条消息 / "
            f"{stats['tool_calls']} 次工具 / 思考 {stats['thinking_chars']} 字"
        )
        threads.append(thread)

    await _close_shared_runtime()

    if args.keep or only:  # 与已有结果合并,并保持 SCENARIOS 的顺序
        by_id = dict(existing)
        by_id.update({t["id"]: t for t in threads})
        threads = [by_id[s["id"]] for s in SCENARIOS if s["id"] in by_id]
    write_output(threads, model=model)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="", help="默认跟随 .env 的 CUSTOM_MODELS")
    parser.add_argument("--provider", default="custom")
    parser.add_argument(
        "--only", default="",
        help="只重跑某个场景 id(其余沿用已导出的结果,不会被冲掉)",
    )
    parser.add_argument("--keep", action="store_true", help="复用已有结果,只补跑缺的")
    parser.add_argument("--list", action="store_true", help="列出所有场景")
    parser.add_argument(
        "--reprocess", action="store_true",
        help="不重跑模型,只用已导出的 tool_calls 重新抽 todo/澄清/artifact 结构",
    )
    args = parser.parse_args()

    if args.reprocess:
        existing = load_existing()
        if not existing:
            print("没有可重处理的结果,先跑一次 build_demo。")
            return 1
        threads = reprocess(existing)
        raw = DEMO_OUT.read_text(encoding="utf-8")
        model = ""
        start = raw.find("{")
        if start >= 0:
            try:
                model = json.loads(raw[start : raw.rfind("}") + 1]).get("model", "")
            except json.JSONDecodeError:
                model = ""
        write_output(threads, model=model)
        return 0

    if args.list:
        for scenario in SCENARIOS:
            source = "评测样本" if scenario.get("from_dataset") else "展示专用"
            print(f"  {scenario['id']:24s} [{source}] {scenario['title']}")
        return 0
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
