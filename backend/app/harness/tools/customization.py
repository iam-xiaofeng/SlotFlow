"""Tools for installing Skills and registering user MCP servers."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

from langchain_core.tools import BaseTool, tool

from app.harness.mcp import SlotFlowMcpConfigStore
from app.harness.skills import (
    ProtectedSkillError,
    SlotFlowSkillsConfigStore,
    invalidate_skill_scan_cache,
    load_enabled_skills,
)
from app.harness.tools.network import fetch_url, search_web
from app.harness.sandbox import SlotFlowSandboxConfig


# Authoritative, stable entry points for the open SKILL.md standard (originated at Anthropic;
# now shared by Claude Code, OpenAI Codex, and GitHub Copilot). A skill published for any of
# them is a plain SKILL.md directory and installs into SlotFlow unchanged — so discovery does
# not need a Codex-specific tool, just awareness of where the ecosystem lives.
SKILL_ECOSYSTEM_SOURCES: tuple[dict[str, str], ...] = (
    {
        "repo": "anthropics/skills",
        "url": "https://github.com/anthropics/skills",
        "note": "Anthropic 官方 Skills(SKILL.md 开放标准的来源)。",
    },
    {
        "repo": "vercel-labs/skills",
        "url": "https://github.com/vercel-labs/skills",
        "note": "skills.sh 注册表 / find-skills 默认源,高星社区 Skills 集合。",
    },
    {
        "repo": "openai/codex (.agents/skills)",
        "url": "https://developers.openai.com/codex/skills",
        "note": "OpenAI Codex 用同一 SKILL.md 标准,从仓库 .agents/skills 等目录加载;为 Codex 写的 Skill 可直接用。",
    },
)

# Short-TTL memo for LOCAL installed-skill matching (no network). The skills preflight and a
# subsequent skill_match tool call in the same turn would otherwise re-load and re-score every
# SKILL.md on disk. TTL keeps it fresh enough that a skill_install (which also clears the cache)
# becomes visible quickly.
_MATCH_CACHE: dict[tuple, tuple[float, list[dict[str, object]]]] = {}
_MATCH_CACHE_TTL_SECONDS = 10.0


def invalidate_skill_match_cache() -> None:
    """Drop cached installed-skill matches (call after installing/removing a Skill)."""

    _MATCH_CACHE.clear()



def build_customization_tools(
    *,
    skills_root,
    skills_config_store: SlotFlowSkillsConfigStore | None,
    mcp_config_store: SlotFlowMcpConfigStore | None,
    sandbox_config: SlotFlowSandboxConfig | None = None,
) -> list[BaseTool]:
    """Build tools that can modify user-controlled SlotFlow extensions."""

    config = sandbox_config or SlotFlowSandboxConfig()

    @tool("find-skills")
    def find_skills(query: str, max_results: int = 5) -> str:
        """Search for installable Skills related to a user need."""

        result = find_installable_skills(
            query=query,
            max_results=max_results,
            config=config,
        )
        return json.dumps(result, ensure_ascii=False)

    @tool("skill_match")
    def skill_match(query: str, max_results: int = 5) -> str:
        """Find relevant installed Skills first; search installable Skills only if needed."""

        result = find_relevant_skills(
            query=query,
            max_results=max_results,
            skills_root=skills_root,
            skills_config_store=skills_config_store,
            config=config,
        )
        return json.dumps(result, ensure_ascii=False)

    @tool("skill_install")
    def skill_install(package_url: str, skill_name: str) -> str:
        """Install a Skill through the skills.sh compatible CLI."""

        if skills_config_store is None:
            return json.dumps(
                {"error": "skills_config_store_not_configured", "source": "slotflow_customization"},
                ensure_ascii=False,
            )
        try:
            target = skills_config_store.install_skill_from_registry(
                package_url=package_url,
                skill_name=skill_name,
            )
        except ProtectedSkillError:
            return json.dumps(
                {
                    "error": "protected_skill",
                    "skill_name": skill_name,
                    "source": "slotflow_customization",
                },
                ensure_ascii=False,
            )
        except (RuntimeError, ValueError) as exc:
            return json.dumps(
                {
                    "error": str(exc),
                    "skill_name": skill_name,
                    "source": "slotflow_customization",
                },
                ensure_ascii=False,
            )

        invalidate_skill_match_cache()
        invalidate_skill_scan_cache()
        return json.dumps(
            {
                "installed": True,
                "skill_name": skill_name,
                "path": str(target),
                "available_from_next_run": True,
                "source": "slotflow_customization",
            },
            ensure_ascii=False,
        )

    @tool("skill_list")
    def skill_list() -> str:
        """List installed SlotFlow Skills and their enabled state."""

        if skills_root is None:
            return json.dumps({"skills": [], "source": "slotflow_customization"}, ensure_ascii=False)
        if skills_config_store is not None:
            skills_config_store.ensure_default_find_skills()
        skills = load_enabled_skills(skills_root=skills_root, enabled_names=None)
        configs = skills_config_store.configs() if skills_config_store is not None else {}
        return json.dumps(
            {
                "skills": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "enabled": configs.get(item.name).enabled if item.name in configs else True,
                        "protected": configs.get(item.name).protected if item.name in configs else False,
                    }
                    for item in skills
                ],
                "source": "slotflow_customization",
            },
            ensure_ascii=False,
        )

    @tool("skill_group")
    def skill_group(
        name: str,
        description: str,
        members: list[str],
        content: str = "",
    ) -> str:
        """Group several existing top-level Skills under a new index Skill.

        A package like a paper-writing pipeline installs as a dozen PARALLEL skills — there is
        no natural parent. Listing them all floods the prompt. After installing such a package,
        create one index skill (you choose its name/description/content) whose members are those
        skills; they move under it and the prompt then lists only this index skill. When it
        matches a task, read the relevant member's SKILL.md (paths are in the index skill's
        "Member skills" section) and follow it. Pick a clear description of what the whole group
        does so future runs know when to open it.
        """

        if skills_config_store is None or skills_root is None:
            return json.dumps(
                {"error": "skills_config_store_not_configured", "source": "slotflow_customization"},
                ensure_ascii=False,
            )
        member_dirs: dict[str, Path] = {}
        for member_name in dict.fromkeys(members):
            match = next(
                (
                    skill
                    for skill in load_enabled_skills(skills_root=skills_root, enabled_names=None)
                    if skill.name == member_name
                ),
                None,
            )
            if match is None:
                return json.dumps(
                    {
                        "error": "member_not_found",
                        "member": member_name,
                        "source": "slotflow_customization",
                    },
                    ensure_ascii=False,
                )
            member_dirs[member_name] = match.skill_dir
        try:
            group_dir = skills_config_store.create_skill_group(
                name=name,
                description=description,
                content=content,
                member_dirs=member_dirs,
            )
        except ProtectedSkillError:
            return json.dumps(
                {"error": "protected_skill", "source": "slotflow_customization"},
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps(
                {"error": str(exc), "source": "slotflow_customization"},
                ensure_ascii=False,
            )

        invalidate_skill_match_cache()
        invalidate_skill_scan_cache()
        return json.dumps(
            {
                "grouped": True,
                "name": name,
                "members": list(member_dirs),
                "path": str(group_dir),
                "available_from_next_run": True,
                "source": "slotflow_customization",
            },
            ensure_ascii=False,
        )

    @tool("mcp_add_http")
    def mcp_add_http(name: str, url: str) -> str:
        """Register a streamable HTTP MCP server for future tool loading."""

        if mcp_config_store is None:
            return json.dumps(
                {"error": "mcp_config_store_not_configured", "source": "slotflow_customization"},
                ensure_ascii=False,
            )
        try:
            server = mcp_config_store.upsert_http_server(name=name, url=url, enabled=True)
        except ValueError as exc:
            return json.dumps(
                {"error": str(exc), "source": "slotflow_customization"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "registered": True,
                "name": server.name,
                "url": (server.config or {}).get("url"),
                "available_from_next_run": True,
                "source": "slotflow_customization",
            },
            ensure_ascii=False,
        )

    @tool("search_skill_repos")
    def search_skill_repos(query: str, max_results: int = 5) -> str:
        """Search GitHub for installable Skill repositories by capability.

        Query by what a Skill DOES (e.g. 'research', 'pdf', 'finance', 'slides'), not by
        the user's topic. Skills use the open SKILL.md standard shared by Claude Code, OpenAI
        Codex (.agents/skills), and GitHub Copilot, so a Skill written for any of them installs
        into SlotFlow unchanged. The result also lists authoritative ecosystem sources
        (Anthropic / Codex / skills.sh) you can browse directly. Returns repositories you can
        then install with skill_install(package_url, skill_name).
        """

        return json.dumps(
            find_skill_repos_on_github(query=query, max_results=max_results, config=config),
            ensure_ascii=False,
        )

    return [
        skill_match,
        find_skills,
        skill_list,
        skill_install,
        skill_group,
        mcp_add_http,
        search_skill_repos,
    ]


def find_skill_repos_on_github(
    *,
    query: str,
    max_results: int,
    config: SlotFlowSandboxConfig,
) -> dict:
    """Search GitHub repositories for installable Skills via the public search API.

    Capability-oriented: callers should pass what a Skill does, not the topic. Returns
    repos (full_name/url/description/stars) that skill_install can consume.
    """

    stripped = re.sub(r"\s+", " ", query).strip()
    if not stripped:
        return {"query": query, "results": [], "error": "empty_query", "source": "slotflow_customization"}

    safe_limit = max(1, min(max_results, 10))
    api_url = (
        "https://api.github.com/search/repositories?q="
        + quote_plus(f"{stripped[:160]} skill")
        + f"&sort=stars&order=desc&per_page={safe_limit}"
    )
    fetched = fetch_url(url=api_url, config=config, include_raw=True)
    if fetched.get("error"):
        return {"query": stripped, "results": [], "error": fetched["error"], "source": "slotflow_customization"}

    try:
        payload = json.loads(fetched.get("_raw_content") or "")
    except (json.JSONDecodeError, TypeError):
        return {"query": stripped, "results": [], "error": "invalid_github_response", "source": "slotflow_customization"}

    items = payload.get("items") if isinstance(payload, dict) else None
    results: list[dict] = []
    if isinstance(items, list):
        for item in items[:safe_limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "repo": item.get("full_name"),
                    "url": item.get("html_url"),
                    "description": item.get("description"),
                    "stars": item.get("stargazers_count"),
                }
            )
    return {
        "query": stripped,
        "results": results,
        "ecosystem_sources": [dict(item) for item in SKILL_ECOSYSTEM_SOURCES],
        "hint": (
            "If a repo matches, install it with skill_install(package_url=<url>, skill_name=<name>). "
            "Any SKILL.md repo (Claude / Codex / Copilot) works — check ecosystem_sources too."
        ),
        "source": "slotflow_customization",
    }


def find_relevant_skills(
    *,
    query: str,
    max_results: int = 5,
    skills_root,
    skills_config_store: SlotFlowSkillsConfigStore | None = None,
    config: SlotFlowSandboxConfig | None = None,
    local_only: bool = False,
) -> dict:
    """Prefer locally installed Skills, then fall back to installable Skill search.

    ``local_only=True`` skips the network installable-skill search. The prepare-node
    preflight uses this so a request with no matching local Skill does not block first-
    token on a ~4s web search; the model can still run the network search itself via the
    ``skill_match`` tool when it actually wants to install something.
    """

    installed_matches = match_installed_skills(
        query=query,
        max_results=max_results,
        skills_root=skills_root,
        skills_config_store=skills_config_store,
    )
    result: dict[str, object] = {
        "query": query,
        "installed_matches": installed_matches,
        "tool": "skill_match",
        "source": "slotflow_customization",
    }
    if installed_matches:
        result["next_action"] = "use_installed_skills"
        result["hint"] = (
            "Use the installed Skill matches for this run before searching or installing more Skills."
        )
        return result

    if local_only:
        result["next_action"] = "optional_skill_match"
        result["hint"] = (
            "No relevant installed Skills were found. If specialized work is needed, call "
            "skill_match to search installable Skills on demand; otherwise proceed."
        )
        return result

    installable = find_installable_skills(
        query=query,
        max_results=max_results,
        config=config,
    )
    result["next_action"] = "review_find_skills_results"
    result["installable_search"] = installable
    result["hint"] = (
        "No relevant installed Skills were found. Review installable_search and install only "
        "when a concrete package_url and skill_name are available and relevant."
    )
    return result


def match_installed_skills(
    *,
    query: str,
    max_results: int = 5,
    skills_root,
    skills_config_store: SlotFlowSkillsConfigStore | None = None,
) -> list[dict[str, object]]:
    if skills_root is None:
        return []

    cache_key = (
        str(skills_root),
        re.sub(r"\s+", " ", query.strip().lower()),
        max(1, max_results),
        id(skills_config_store),
    )
    cached = _MATCH_CACHE.get(cache_key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    if skills_config_store is not None:
        skills_config_store.ensure_default_find_skills()
        configs = skills_config_store.configs()
    else:
        configs = {}

    query_terms = _skill_match_terms(query)
    matches: list[tuple[int, dict[str, object]]] = []
    for skill in load_enabled_skills(skills_root=skills_root, enabled_names=None):
        if skill.name == "find-skills":
            continue
        configured = configs.get(skill.name)
        if configured is not None and not configured.enabled:
            continue
        haystack = f"{skill.name} {skill.description}".lower()
        score = _skill_match_score(
            query=query,
            query_terms=query_terms,
            name=skill.name,
            haystack=haystack,
        )
        if score <= 0:
            continue
        matches.append(
            (
                score,
                {
                    "name": skill.name,
                    "description": skill.description,
                    "path": str(skill.skill_dir),
                    "score": score,
                    "enabled": configured.enabled if configured is not None else skill.enabled,
                    "source": configured.source if configured is not None else "local",
                },
            )
        )

    matches.sort(key=lambda item: (-item[0], str(item[1]["name"])))
    result = [item for _, item in matches[:max(1, max_results)]]
    _MATCH_CACHE[cache_key] = (time.monotonic() + _MATCH_CACHE_TTL_SECONDS, result)
    return result


def _skill_match_score(
    *,
    query: str,
    query_terms: set[str],
    name: str,
    haystack: str,
) -> int:
    score = 0
    lowered_query = query.lower()
    if name.lower() in lowered_query:
        score += 8
    for term in query_terms:
        if term in haystack:
            score += 1 if len(term) <= 3 else 2
    return score


def _skill_match_terms(query: str) -> set[str]:
    lowered = query.lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    cjk_text = "".join(re.findall(r"[\u4e00-\u9fff]+", lowered))
    terms.update(
        cjk_text[index : index + 2]
        for index in range(max(0, len(cjk_text) - 1))
    )
    bridge_terms = {
        "经济": {"economy", "economic", "macro", "macroeconomic", "finance", "financial"},
        "金融": {"finance", "financial", "market"},
        "股票": {"stock", "equity", "market"},
        "研究": {"research", "analysis"},
        "分析": {"analysis", "research"},
        "报告": {"report", "writing"},
        "数据": {"data", "dataset"},
        "中国": {"china", "chinese"},
    }
    for needle, additions in bridge_terms.items():
        if needle in query:
            terms.update(additions)
    return {term for term in terms if len(term) >= 2}


def find_installable_skills(
    *,
    query: str,
    max_results: int = 5,
    config: SlotFlowSandboxConfig | None = None,
) -> dict:
    """Run the shared find-skills search used by tools and preflight."""

    result = search_web(
        query=f"{query} site:skills.sh OR site:github.com/vercel-labs/skills",
        max_results=max_results,
        config=config or SlotFlowSandboxConfig(),
    )
    result["hint"] = (
        "Use skill_install only when you have a concrete package_url and skill_name. "
        "If the search result is ambiguous, ask the user before installing."
    )
    result["tool"] = "find-skills"
    return result
