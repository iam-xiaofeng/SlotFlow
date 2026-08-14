"""MCP 收敛边界:把 N 个 server 的 M 个工具压成 **两个固定工具**。

**为什么。** MCP 工具原本是逐个 `bind_tools` 进模型的。这条路在 server 数量上去之后必然崩:
每多一个 server,tools 数组就长一截,而 provider 的可缓存前缀正是 `tools → system → messages`
——工具面一变,整段前缀缓存作废。上一版试过用加载器做渐进式披露,结果更糟:加载器为了让模型
知道能激活什么,又把整个目录内联进自己的 description,省下的 schema 原样回到了前缀里,而且
每次激活还额外赔一次缓存(见 `harness/tool_spaces.py` 顶部与 `HARNESS_NOTES.md` §59)。

**结论:模型侧的工具数量必须收敛,MCP 的数量才可以发散。** 这里的做法是——

- `mcp_docs(query, server)`:从已连接 server 的工具定义**自动生成的本地手册**里检索。纯本地
  关键词匹配,不走大模型、不发网络请求。这是"探索"阶段。
- `mcp_call(server, tool, arguments)`:通用代理,宿主侧直接 `ainvoke` 那个真实的 MCP 工具。
  这是"调用"阶段。

于是不管接 1 个还是 100 个 server,模型看到的 schema 永远是这两个,逐字节不变。

**为什么不放进沙箱。** 另一种常见做法是在容器里跑 MCP 客户端、让模型写代码调用。在 SlotFlow
里落不了地:默认 MCP 是 playwright(stdio + pnpm + Chromium),`python:3.12` 容器里根本起不来;
还得把 MCP 凭证注入容器;而且 MCP 从此依赖 Docker——SlotFlow 本来有完整的 Docker 降级路径,
这是倒退。宿主侧我们已经持有 `MultiServerMcpToolProvider` 的活 session,直接调即可。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.harness.mcp.loader import MCP_SERVER_METADATA_KEY

_SOURCE = "slotflow_mcp_proxy"
# 单个工具在手册里的描述截断长度(手册是给模型读的,不是文档站)。
MAX_TOOL_DESCRIPTION_CHARS = 400
# 一次 mcp_docs 最多返回多少个工具条目。
MAX_DOC_RESULTS = 12
# mcp_call 结果的字符上限;超了截断并说明,避免一次 MCP 调用挤爆上下文。
MAX_CALL_RESULT_CHARS = 20_000


def mcp_server_name(tool: BaseTool) -> str:
    """读出 loader 打在工具上的来源 server 名。"""

    metadata = tool.metadata if isinstance(tool.metadata, dict) else {}
    for key in (MCP_SERVER_METADATA_KEY, "server_name", "mcp_server"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def build_mcp_manual(tools: list[BaseTool]) -> dict[str, list[dict[str, Any]]]:
    """把已加载的 MCP 工具整理成"按 server 分组的本地手册"。

    手册直接从工具定义生成(名称 / 描述 / 参数 schema),所以永远和真实 server 同步,
    不需要人工维护一份会过期的文档。
    """

    manual: dict[str, list[dict[str, Any]]] = {}
    for item in tools:
        entry = {
            "tool": item.name,
            "description": _short_description(item),
            "arguments": _argument_summary(item),
        }
        manual.setdefault(mcp_server_name(item), []).append(entry)
    for entries in manual.values():
        entries.sort(key=lambda entry: entry["tool"])
    return manual


def build_mcp_proxy_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Build the fixed two-tool MCP boundary (docs + call) over loaded MCP tools."""

    if not tools:
        return []

    manual = build_mcp_manual(tools)
    by_key = {(mcp_server_name(item), item.name): item for item in tools}
    servers = sorted(manual)
    server_summary = ", ".join(f"{name}({len(manual[name])} tools)" for name in servers)

    @tool("mcp_docs")
    def mcp_docs(query: str = "", server: str = "") -> str:
        """Look up which MCP tools exist and how to call them (local manual, no network).

        Always call this before mcp_call: it returns exact tool names, argument names, types,
        and which arguments are required. Pass `query` describing the capability you need
        ("list issues", "查询数据库") and/or `server` to scope the lookup. With no arguments it
        lists every connected server and its tool count.
        """

        clean_server = server.strip()
        clean_query = query.strip()
        if clean_server and clean_server not in manual:
            return json.dumps(
                {
                    "error": "unknown_server",
                    "server": clean_server,
                    "servers": servers,
                    "source": _SOURCE,
                },
                ensure_ascii=False,
            )

        scoped = (
            {clean_server: manual[clean_server]} if clean_server else dict(manual)
        )
        if not clean_query and not clean_server:
            return json.dumps(
                {
                    "servers": [
                        {"server": name, "tool_count": len(manual[name])} for name in servers
                    ],
                    "usage": (
                        "Call mcp_docs(query=..., server=...) to see the tools of one server, "
                        "then mcp_call(server=..., tool=..., arguments={...})."
                    ),
                    "source": _SOURCE,
                },
                ensure_ascii=False,
            )

        matches = _search_manual(scoped, clean_query)
        return json.dumps(
            {
                "query": clean_query,
                "server": clean_server,
                "servers": servers,
                "tools": matches,
                "usage": (
                    "Call mcp_call(server=..., tool=..., arguments={...}) with exactly these "
                    "argument names. If nothing matched, retry with a broader capability word "
                    "or omit query to list the server's whole catalog."
                ),
                "source": _SOURCE,
            },
            ensure_ascii=False,
        )

    @tool("mcp_call")
    async def mcp_call(server: str, tool: str, arguments: dict[str, Any] | None = None) -> str:
        """Call one tool on one connected MCP server.

        `server` and `tool` must come from mcp_docs — do not guess them. `arguments` is the
        JSON object that tool expects (mcp_docs lists the exact argument names and which are
        required). This one proxy covers every configured MCP server, so the tool list you see
        never grows when servers are added.
        """

        clean_server = server.strip()
        clean_tool = tool.strip()
        target = by_key.get((clean_server, clean_tool))
        if target is None:
            return json.dumps(
                {
                    "error": "unknown_mcp_tool",
                    "server": clean_server,
                    "tool": clean_tool,
                    "known_servers": servers,
                    "known_tools": [name for _server, name in by_key if _server == clean_server][:MAX_DOC_RESULTS],
                    "hint": "Call mcp_docs(query=..., server=...) first and use the exact names it returns.",
                    "source": _SOURCE,
                },
                ensure_ascii=False,
            )

        payload = arguments if isinstance(arguments, dict) else {}
        try:
            result = await target.ainvoke(payload)
        except Exception as exc:  # noqa: BLE001 - MCP 失败要作为工具结果回给模型,而不是炸掉整轮
            return json.dumps(
                {
                    "error": "mcp_call_failed",
                    "server": clean_server,
                    "tool": clean_tool,
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "hint": "Check the argument names/types with mcp_docs before retrying.",
                    "source": _SOURCE,
                },
                ensure_ascii=False,
            )

        text, truncated = _stringify_result(result)
        body: dict[str, Any] = {
            "server": clean_server,
            "tool": clean_tool,
            "result": text,
            "source": _SOURCE,
        }
        if truncated:
            body["truncated"] = True
            body["note"] = (
                f"Result exceeded {MAX_CALL_RESULT_CHARS} characters and was cut off. "
                "Narrow the request (filters, pagination, fewer fields) if you need the rest."
            )
        return json.dumps(body, ensure_ascii=False)

    mcp_docs.description = (
        f"{mcp_docs.description}\n\nConnected MCP servers: {server_summary}."
    )
    return [mcp_docs, mcp_call]


def _short_description(item: BaseTool) -> str:
    description = (item.description or "").strip()
    if len(description) <= MAX_TOOL_DESCRIPTION_CHARS:
        return description
    return f"{description[:MAX_TOOL_DESCRIPTION_CHARS].rstrip()}…"


def _argument_summary(item: BaseTool) -> dict[str, Any]:
    """从工具的参数 schema 里抽出模型真正需要的那几项:名字 / 类型 / 是否必填。"""

    schema = getattr(item, "args_schema", None)
    if not isinstance(schema, dict):
        # pydantic 模型形态(非 MCP 原生 dict schema)时退回 LangChain 的通用投影。
        try:
            schema = item.tool_call_schema.model_json_schema()
        except Exception:  # noqa: BLE001 - 手册生成绝不能因为一个畸形 schema 失败
            return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    summary: dict[str, Any] = {}
    for name, spec in properties.items():
        detail: dict[str, Any] = {"required": name in required_names}
        if isinstance(spec, dict):
            if isinstance(spec.get("type"), str):
                detail["type"] = spec["type"]
            if isinstance(spec.get("description"), str):
                detail["description"] = spec["description"][:200]
            if isinstance(spec.get("enum"), list):
                detail["enum"] = spec["enum"][:12]
        summary[name] = detail
    return summary


def _search_manual(
    manual: dict[str, list[dict[str, Any]]],
    query: str,
) -> list[dict[str, Any]]:
    entries = [
        {**entry, "server": server}
        for server, items in manual.items()
        for entry in items
    ]
    if not query:
        return entries[:MAX_DOC_RESULTS]

    terms = _query_terms(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        haystack = f"{entry['tool']} {entry['description']}".lower()
        score = sum(2 if len(term) > 3 else 1 for term in terms if term in haystack)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], str(item[1]["tool"])))
    if scored:
        return [entry for _score, entry in scored[:MAX_DOC_RESULTS]]
    # 没命中不返回空手:给出目录的头部,让模型看到真实名字再重试,而不是凭空猜一个。
    return entries[:MAX_DOC_RESULTS]


def _query_terms(query: str) -> set[str]:
    lowered = query.lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]+", lowered))
    cjk = "".join(re.findall(r"[一-鿿]+", lowered))
    terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {term for term in terms if len(term) >= 2}


def _stringify_result(result: Any) -> tuple[str, bool]:
    if isinstance(result, str):
        text = result
    elif isinstance(result, (dict, list)):
        try:
            text = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(result)
    else:
        text = str(result)
    if len(text) <= MAX_CALL_RESULT_CHARS:
        return text, False
    return text[:MAX_CALL_RESULT_CHARS], True
