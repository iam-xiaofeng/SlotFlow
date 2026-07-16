"""Contracts for the fixed Agent Reach host bridge."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.chat.runtime.config import load_agent_reach_config_from_env
from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools import agent_reach as module
from app.harness.tools.agent_reach import (
    AgentReachHostError,
    FixedHostCommandRunner,
    SlotFlowAgentReachConfig,
    build_agent_reach_tools,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.outputs: dict[str, str] = {
            "agent-reach": '{"channels": []}',
            "mcporter": "search results",
            "curl": "# Article",
            "gh": "[]",
            "yt-dlp": json.dumps(
                {
                    "id": "video-id",
                    "title": "Video title",
                    "formats": [{"url": "must-not-leak"}],
                    "subtitles": {"en": []},
                    "automatic_captions": {"zh-Hans": []},
                }
            ),
        }

    def run(self, executable: str, args: list[str]) -> str:
        self.calls.append((executable, list(args)))
        return self.outputs[executable]


def _tools(runner: RecordingRunner, *, network_enabled: bool = True):
    return {
        tool.name: tool
        for tool in build_agent_reach_tools(
            SlotFlowAgentReachConfig(home=Path("/host/agent-reach")),
            sandbox_config=SlotFlowSandboxConfig(network_enabled=network_enabled),
            runner=runner,
        )
    }


def test_bridge_exposes_only_fixed_read_operations() -> None:
    tools = _tools(RecordingRunner())
    assert set(tools) == {
        "agent_reach_status",
        "agent_reach_web_search",
        "agent_reach_read_url",
        "agent_reach_github_search",
        "agent_reach_youtube_metadata",
    }
    assert all("command" not in tool.args for tool in tools.values())


def test_bridge_respects_feature_and_global_network_switches() -> None:
    sandbox = SlotFlowSandboxConfig(network_enabled=True)
    assert (
        build_agent_reach_tools(
            SlotFlowAgentReachConfig(enabled=False),
            sandbox_config=sandbox,
        )
        == []
    )
    assert _tools(RecordingRunner(), network_enabled=False) == {}


def test_status_and_search_build_fixed_argv() -> None:
    runner = RecordingRunner()
    tools = _tools(runner)

    assert json.loads(tools["agent_reach_status"].invoke({})) == {"channels": []}
    assert tools["agent_reach_web_search"].invoke(
        {"query": "current browser agents", "max_results": 99}
    ) == "search results"

    assert runner.calls == [
        ("agent-reach", ["doctor", "--json"]),
        (
            "mcporter",
            [
                "--config",
                "/host/agent-reach/config/mcporter.json",
                "call",
                "exa.web_search_exa",
                "query=current browser agents",
                "numResults=10",
            ],
        ),
    ]


def test_read_url_validates_target_and_uses_jina_reader(monkeypatch) -> None:
    runner = RecordingRunner()
    tools = _tools(runner)
    monkeypatch.setattr(module, "validate_public_url", lambda url, *, config: url)

    assert tools["agent_reach_read_url"].invoke({"url": "https://example.com/a"}) == "# Article"
    executable, args = runner.calls[-1]
    assert executable == "curl"
    assert args[-1] == "https://r.jina.ai/https://example.com/a"
    assert "--max-filesize" in args


def test_github_search_uses_allowlisted_shape_and_limit() -> None:
    runner = RecordingRunner()
    tools = _tools(runner)

    assert tools["agent_reach_github_search"].invoke(
        {"query": "langgraph", "kind": "pull_requests", "max_results": 3}
    ) == "[]"
    assert runner.calls[-1] == (
        "gh",
        [
            "search",
            "prs",
            "langgraph",
            "--limit",
            "3",
            "--json",
            "number,title,url,repository,state,updatedAt",
        ],
    )

    tools["agent_reach_github_search"].invoke({"query": "slotflow"})
    assert runner.calls[-1][1][-1] == "fullName,description,url,stargazersCount,updatedAt"


def test_youtube_returns_bounded_summary_without_format_urls(monkeypatch) -> None:
    runner = RecordingRunner()
    tools = _tools(runner)
    monkeypatch.setattr(module, "validate_public_url", lambda url, *, config: url)

    result = json.loads(
        tools["agent_reach_youtube_metadata"].invoke(
            {"url": "https://www.youtube.com/watch?v=video-id"}
        )
    )
    assert result == {"id": "video-id", "title": "Video title"}
    assert "must-not-leak" not in json.dumps(result)


@pytest.mark.asyncio
async def test_tools_expose_non_blocking_async_path() -> None:
    runner = RecordingRunner()
    tools = _tools(runner)
    result = await tools["agent_reach_web_search"].ainvoke({"query": "SlotFlow"})
    assert result == "search results"


def test_fixed_runner_uses_no_shell_bounds_output_and_redacts_secrets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".agent-reach"
    home.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setenv("EXAMPLE_API_KEY", "super-secret-value")
    monkeypatch.setattr(module.shutil, "which", lambda executable, *, path: f"/usr/bin/{executable}")

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=b"super-secret-value:" + b"x" * 30,
            stderr=b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    runner = FixedHostCommandRunner(
        SlotFlowAgentReachConfig(home=home, max_output_bytes=24, timeout_seconds=7)
    )

    output = runner.run("gh", ["search", "repos", "query"])
    assert captured["argv"] == ["/usr/bin/gh", "search", "repos", "query"]
    assert captured["cwd"] == home.resolve()
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["timeout"] == 7
    assert "[REDACTED]" in output
    assert "[slotflow: output truncated]" in output


def test_fixed_runner_rejects_unlisted_executable(tmp_path: Path) -> None:
    home = tmp_path / ".agent-reach"
    home.mkdir()
    runner = FixedHostCommandRunner(SlotFlowAgentReachConfig(home=home))
    with pytest.raises(AgentReachHostError, match="not allowlisted"):
        runner.run("bash", ["-lc", "whoami"])


def test_runtime_env_loads_agent_reach_limits(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLOTFLOW_AGENT_REACH_ENABLED", "false")
    monkeypatch.setenv("SLOTFLOW_AGENT_REACH_HOME", str(tmp_path))
    monkeypatch.setenv("SLOTFLOW_AGENT_REACH_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("SLOTFLOW_AGENT_REACH_MAX_OUTPUT_BYTES", "4096")

    assert load_agent_reach_config_from_env() == SlotFlowAgentReachConfig(
        enabled=False,
        home=tmp_path,
        timeout_seconds=17,
        max_output_bytes=4096,
    )
