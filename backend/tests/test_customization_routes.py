"""Tests for user-managed Skills and MCP routes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.runtime import SlotFlowRuntimeConfig
from app.harness.mcp import SlotFlowMcpConfig, SlotFlowMcpConfigStore, SlotFlowMcpServerConfig
from app.harness.skills import SlotFlowSkillsConfigStore
from app.main import create_app


def _client(tmp_path: Path) -> tuple[TestClient, SlotFlowRuntimeConfig]:
    base_mcp = SlotFlowMcpConfig(
        enabled=True,
        servers=(
            SlotFlowMcpServerConfig(
                name="filesystem",
                config={
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", ".slotflow/workspace"],
                },
            ),
        ),
    )
    runtime_config = SlotFlowRuntimeConfig(
        skills_root=tmp_path / "skills",
        skills_config_store=SlotFlowSkillsConfigStore(
            tmp_path / "skills.json",
            skills_root=tmp_path / "skills",
        ),
        mcp_config=base_mcp,
        mcp_config_store=SlotFlowMcpConfigStore(
            tmp_path / "mcp.json",
            base_config=base_mcp,
        ),
    )
    return TestClient(create_app(runtime_config=runtime_config)), runtime_config


def test_upload_skill_folder_and_list_user_skill(tmp_path: Path) -> None:
    client, runtime_config = _client(tmp_path)

    response = client.post(
        "/api/skills/upload",
        files=[
            (
                "files",
                (
                    "patent-helper/SKILL.md",
                    b"---\nname: patent-helper\n"
                    b"description: Analyze patent text\n---\n\n# Patent helper\n",
                    "text/markdown",
                ),
            ),
            (
                "files",
                (
                    "patent-helper/references/checklist.md",
                    b"# checklist\n",
                    "text/markdown",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    assert response.json()[0]["name"] == "patent-helper"
    assert (runtime_config.skills_root / "patent-helper" / "SKILL.md").is_file()
    assert (runtime_config.skills_root / "patent-helper" / "references" / "checklist.md").is_file()
    listed = client.get("/api/skills").json()
    patent_helper = next(skill for skill in listed if skill["name"] == "patent-helper")
    assert patent_helper["description"] == "Analyze patent text"
    assert patent_helper["enabled"] is True
    assert patent_helper["protected"] is False


def test_upload_skill_folder_rejects_missing_skill_file(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/api/skills/upload",
        files=[
            (
                "files",
                (
                    "not-a-skill/readme.md",
                    b"# missing frontmatter\n",
                    "text/markdown",
                ),
            )
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "uploaded folder must contain a valid SKILL.md"


def test_upload_single_skill_md_still_works_for_manual_api_calls(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/api/skills/upload",
        files=[
            (
                "files",
                (
                    "SKILL.md",
                    b"---\nname: uploaded\n"
                    b"description: Uploaded skill\n---\n\n# Uploaded\n",
                    "text/markdown",
                ),
            )
        ],
    )

    assert response.status_code == 200
    assert response.json()[0]["name"] == "uploaded"
    assert any(skill["name"] == "uploaded" for skill in client.get("/api/skills").json())


def test_default_find_skills_is_protected_and_toggleable(tmp_path: Path) -> None:
    client, runtime_config = _client(tmp_path)

    listed = client.get("/api/skills").json()
    find_skills = next(skill for skill in listed if skill["name"] == "find-skills")

    assert find_skills["enabled"] is True
    assert find_skills["protected"] is True
    assert find_skills["source"] == "skills.sh"
    assert find_skills["pinned"] is True

    update_response = client.patch("/api/skills/find-skills", json={"enabled": False})

    assert update_response.status_code == 200
    assert update_response.json()["enabled"] is False
    assert "find-skills" not in runtime_config.enabled_skills

    delete_response = client.delete("/api/skills/find-skills")

    assert delete_response.status_code == 403
    assert (runtime_config.skills_root / "find-skills" / "SKILL.md").is_file()


def test_install_skill_uses_skills_cli(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, runtime_config = _client(tmp_path)

    def fake_run(args, *, cwd, check, capture_output, text, timeout):
        _ = check, capture_output, text, timeout
        skill_dir = Path(cwd) / ".agents" / "skills" / "research-helper"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: research-helper\n"
            "description: Research helper\n"
            "---\n\n"
            "# Research helper\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.harness.skills.store.subprocess.run", fake_run)

    response = client.post(
        "/api/skills/install",
        json={
            "package_url": "https://github.com/example/skills",
            "skill_name": "research-helper",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "research-helper"
    assert response.json()["source"] == "skills.sh"
    assert (runtime_config.skills_root / "research-helper" / "SKILL.md").is_file()


def test_install_skill_groups_dependency_skills(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client, runtime_config = _client(tmp_path)

    def fake_run(args, *, cwd, check, capture_output, text, timeout):
        _ = check, capture_output, text, timeout
        root = Path(cwd) / ".agents" / "skills"
        primary_dir = root / "research-helper"
        child_dir = root / "chart-helper"
        primary_dir.mkdir(parents=True)
        child_dir.mkdir(parents=True)
        (primary_dir / "SKILL.md").write_text(
            "---\nname: research-helper\ndescription: Research helper\n---\n\n# Research\n",
            encoding="utf-8",
        )
        (child_dir / "SKILL.md").write_text(
            "---\nname: chart-helper\ndescription: Chart helper\n---\n\n# Chart\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.harness.skills.store.subprocess.run", fake_run)

    response = client.post(
        "/api/skills/install",
        json={
            "package_url": "https://github.com/example/skills",
            "skill_name": "research-helper",
        },
    )

    assert response.status_code == 200
    listed = client.get("/api/skills").json()
    child = next(skill for skill in listed if skill["name"] == "chart-helper")
    assert child["parent"] == "research-helper"
    assert (
        runtime_config.skills_root
        / "research-helper"
        / "dependencies"
        / "chart-helper"
        / "SKILL.md"
    ).is_file()


def test_list_skills_groups_legacy_same_package_dependencies(tmp_path: Path) -> None:
    client, runtime_config = _client(tmp_path)
    for name in ["earnings-preview", "company-valuation"]:
        skill_dir = runtime_config.skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
        runtime_config.skills_config_store.mark_skill(
            name,
            enabled=True,
            source="skills.sh",
            package_url="https://github.com/example/finance-skills",
        )

    listed = client.get("/api/skills").json()
    child = next(skill for skill in listed if skill["name"] == "company-valuation")

    assert child["parent"] == "earnings-preview"


def test_skill_pin_and_reorder_routes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    client.post(
        "/api/skills/upload",
        files=[
            (
                "files",
                (
                    "alpha/SKILL.md",
                    b"---\nname: alpha\ndescription: Alpha\n---\n\n# Alpha\n",
                    "text/markdown",
                ),
            ),
            (
                "files",
                (
                    "beta/SKILL.md",
                    b"---\nname: beta\ndescription: Beta\n---\n\n# Beta\n",
                    "text/markdown",
                ),
            ),
        ],
    )

    listed = client.get("/api/skills").json()
    beta = next(skill for skill in listed if skill["name"] == "beta")
    assert beta["parent"] == "alpha"
    assert (tmp_path / "skills" / "alpha" / "dependencies" / "beta" / "SKILL.md").is_file()

    pin_response = client.patch("/api/skills/beta", json={"pinned": True})
    assert pin_response.status_code == 200
    assert pin_response.json()["pinned"] is True

    reorder_response = client.post("/api/skills/reorder", json={"names": ["beta", "alpha"]})
    assert reorder_response.status_code == 200
    names = [skill["name"] for skill in reorder_response.json() if skill["name"] in {"alpha", "beta"}]
    assert names == ["beta", "alpha"]


def test_create_and_delete_http_mcp_server_refreshes_runtime(tmp_path: Path) -> None:
    client, runtime_config = _client(tmp_path)

    response = client.post(
        "/api/mcp/servers",
        json={
            "name": "search",
            "url": "http://localhost:3333/mcp",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "search",
        "enabled": True,
        "transport": "streamable_http",
        "url": "http://localhost:3333/mcp",
        "source": "user",
        "protected": False,
        "order": 0,
        "pinned": False,
    }
    assert [server.name for server in runtime_config.mcp_config.servers] == ["search"]

    list_response = client.get("/api/mcp/servers")
    assert [server["name"] for server in list_response.json()] == ["search"]
    assert [server["source"] for server in list_response.json()] == ["user"]

    toggle_response = client.patch("/api/mcp/servers/search", json={"enabled": False})

    assert toggle_response.status_code == 200
    assert toggle_response.json()["protected"] is False
    assert [server.name for server in runtime_config.mcp_config.active_servers()] == []

    delete_response = client.delete("/api/mcp/servers/search")

    assert delete_response.status_code == 204
    assert [server.name for server in runtime_config.mcp_config.servers] == []


def test_mcp_pin_reorder_and_reject_removed_filesystem(tmp_path: Path) -> None:
    client, runtime_config = _client(tmp_path)
    first = client.post("/api/mcp/servers", json={"name": "search", "url": "http://localhost:3333/mcp"})
    second = client.post("/api/mcp/servers", json={"name": "docs", "url": "http://localhost:4444/mcp"})
    removed = client.post(
        "/api/mcp/servers",
        json={"name": "filesystem", "url": "http://localhost:5555/mcp"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert removed.status_code == 400

    pin_response = client.patch("/api/mcp/servers/docs", json={"pinned": True})
    assert pin_response.status_code == 200
    assert pin_response.json()["pinned"] is True

    reorder_response = client.post("/api/mcp/servers/reorder", json={"names": ["docs", "search"]})
    assert reorder_response.status_code == 200
    assert [server["name"] for server in reorder_response.json()] == ["docs", "search"]
    assert [server.name for server in runtime_config.mcp_config.servers] == ["docs", "search"]
