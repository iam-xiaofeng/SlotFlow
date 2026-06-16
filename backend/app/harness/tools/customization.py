"""Tools for installing Skills and registering user MCP servers."""

from __future__ import annotations

import json
import re

from langchain_core.tools import BaseTool, tool

from app.harness.mcp import SlotFlowMcpConfigStore
from app.harness.skills import ProtectedSkillError, SlotFlowSkillsConfigStore, load_enabled_skills
from app.harness.tools.network import search_web
from app.harness.sandbox import SlotFlowSandboxConfig


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

    return [skill_match, find_skills, skill_list, skill_install, mcp_add_http]


def find_relevant_skills(
    *,
    query: str,
    max_results: int = 5,
    skills_root,
    skills_config_store: SlotFlowSkillsConfigStore | None = None,
    config: SlotFlowSandboxConfig | None = None,
) -> dict:
    """Prefer locally installed Skills, then fall back to installable Skill search."""

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
    return [item for _, item in matches[:max(1, max_results)]]


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
