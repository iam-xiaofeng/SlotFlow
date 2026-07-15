"""Distribution contracts for the repo-root bootstrap path."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "bootstrap.sh"


def test_bootstrap_has_valid_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(BOOTSTRAP)], check=True)


def test_host_integration_dependencies_are_declared_and_locked() -> None:
    backend = tomllib.loads((REPO_ROOT / "backend" / "pyproject.toml").read_text())
    dependencies = backend["project"]["dependencies"]
    assert any(item.startswith("markitdown[all]") for item in dependencies)
    assert any(item.startswith("markitdown-ocr[llm]") for item in dependencies)

    frontend = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    assert frontend["devDependencies"]["@playwright/mcp"]
    assert frontend["devDependencies"]["playwright"]

    uv_lock = (REPO_ROOT / "backend" / "uv.lock").read_text()
    pnpm_lock = (REPO_ROOT / "frontend" / "pnpm-lock.yaml").read_text()
    assert 'name = "markitdown"' in uv_lock
    assert 'name = "markitdown-ocr"' in uv_lock
    assert "@playwright/mcp" in pnpm_lock


def test_bootstrap_prepares_host_integrations_in_dependency_order() -> None:
    script = BOOTSTRAP.read_text()
    assert 'uv tool install --force --with-executables-from yt-dlp "$AGENT_REACH_SOURCE"' in script
    assert 'mkdir -p "$HOME/.agent-reach"' in script
    assert 'cd "$HOME/.agent-reach"\n  agent-reach install --env=auto' in script
    assert "pnpm exec playwright install-deps chromium" in script
    assert "pnpm exec playwright install chromium" in script
    launcher = REPO_ROOT / "frontend" / "scripts" / "playwright-mcp.mjs"
    assert launcher.stat().st_mode & 0o111
    assert 'chromium.executablePath()' in launcher.read_text()
    subprocess.run(["node", "--check", str(launcher)], check=True)

    main = script[script.index("main() {") :]
    calls = [
        "install_uv",
        "install_node_and_pnpm",
        "install_agent_reach",
        "install_backend_dependencies",
        "install_frontend_dependencies",
        "install_playwright_system_dependencies",
        "install_playwright_browser",
        "prepare_backend_env",
        "setup_docker_sandbox",
    ]
    positions = [main.index(f"  {call}\n") for call in calls]
    assert positions == sorted(positions)
