"""Tests for SlotFlow network and extension tools."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.harness.features import SlotFlowHarnessFeatures
from app.harness.mcp import SlotFlowMcpConfigStore
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.skills import SlotFlowSkillsConfigStore
from app.harness.tools.network import fetch_url, search_web, validate_public_url
from app.harness.tools.registry import build_harness_tools


def test_web_fetch_blocks_localhost_by_default() -> None:
    result = fetch_url(
        url="http://localhost:8000/health",
        config=SlotFlowSandboxConfig(),
    )

    assert result["error"] == "private or localhost network targets are blocked"


def test_web_fetch_returns_readable_text_from_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><title>Example</title><body><h1>Hello</h1><script>ignore()</script><p>World</p></body></html>",
        )

    result = fetch_url(
        url="https://example.com/",
        config=SlotFlowSandboxConfig(allow_private_network=True),
        client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs),
    )

    assert result["status_code"] == 200
    assert result["title"] == "Example"
    assert "Hello" in result["content"]
    assert "ignore" not in result["content"]


def test_web_search_extracts_result_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "www.bing.com" in str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<h2><a href=\"https://www.bing.com/ck/a?u="
                "a1aHR0cHM6Ly9leGFtcGxlLmNvbS9za2lsbA\">Example Skill</a></h2>"
            ),
        )

    config = SlotFlowSandboxConfig(allow_private_network=True)
    result = search_web(
        query="research skill",
        max_results=3,
        config=config,
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )

    assert result["results"] == [
        {"title": "Example Skill", "url": "https://example.com/skill"}
    ]
    assert result["provider"] == "bing"


def test_web_search_falls_back_after_provider_error() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "www.bing.com":
            raise httpx.ConnectError("ssl handshake failed", request=request)
        assert request.url.host == "lite.duckduckgo.com"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<a href=\"/l/?uddg=https%3A%2F%2Fexample.com%2Ffallback\">"
                "Fallback Result</a>"
            ),
        )

    result = search_web(
        query="research skill",
        max_results=3,
        config=SlotFlowSandboxConfig(allow_private_network=True),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )

    assert requested_hosts == ["www.bing.com", "lite.duckduckgo.com"]
    assert result["results"] == [
        {"title": "Fallback Result", "url": "https://example.com/fallback"}
    ]
    assert result["provider"] == "duckduckgo_lite"


def test_harness_registers_network_and_extension_tools(tmp_path: Path) -> None:
    tools = build_harness_tools(
        features=SlotFlowHarnessFeatures(
            thinking_enabled=True,
            plan_enabled=False,
            subagent_enabled=False,
        ),
        skills_root=tmp_path / "skills",
        skills_config_store=SlotFlowSkillsConfigStore(
            tmp_path / "skills.json",
            skills_root=tmp_path / "skills",
        ),
        mcp_config_store=SlotFlowMcpConfigStore(tmp_path / "mcp.json"),
        sandbox_config=SlotFlowSandboxConfig(network_enabled=True),
    )

    tool_names = {tool.name for tool in tools}
    assert {
        "web_fetch",
        "web_search",
        "skill_match",
        "find-skills",
        "skill_install",
        "mcp_add_http",
    } <= tool_names


def test_validate_public_url_rejects_non_http_scheme() -> None:
    try:
        validate_public_url("file:///etc/passwd", config=SlotFlowSandboxConfig())
    except ValueError as exc:
        assert "only http and https" in str(exc)
    else:
        raise AssertionError("expected URL validation to reject file scheme")
