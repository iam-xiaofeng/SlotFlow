"""Tools for installing Skills and registering user MCP servers."""

from __future__ import annotations

import json

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

    return [find_skills, skill_list, skill_install, mcp_add_http]


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
