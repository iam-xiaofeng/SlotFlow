"""Fixed, read-only bridge to Agent Reach's host-side upstream CLIs.

The model never receives a generic host command. Each StructuredTool below builds a
fixed argv for one read-only operation, and the runner resolves only an allowlisted
executable from user-local/system bin directories. Agent Reach stays on the host so
it can reuse browser login state and ``~/.agent-reach`` without entering Docker.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, StructuredTool

from app.harness.sandbox import SlotFlowSandboxConfig
from app.harness.tools.network import validate_public_url


_ALLOWED_EXECUTABLES = frozenset({"agent-reach", "curl", "gh", "mcporter", "yt-dlp"})
_SECRET_NAME_MARKERS = ("api_key", "apikey", "cookie", "password", "secret", "token")
_READER_PREFIX = "https://r.jina.ai/"


class AgentReachHostError(RuntimeError):
    """Raised when a fixed host integration cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class SlotFlowAgentReachConfig:
    """Runtime limits for SlotFlow's fixed Agent Reach host bridge."""

    enabled: bool = True
    home: Path = field(default_factory=lambda: Path.home() / ".agent-reach")
    timeout_seconds: int = 60
    max_output_bytes: int = 512 * 1024


class AgentReachRunner(Protocol):
    """Narrow command boundary used by the tools and their unit tests."""

    def run(self, executable: str, args: list[str]) -> str:
        """Run one allowlisted executable with a fixed argv and return text output."""


class FixedHostCommandRunner:
    """Execute allowlisted host binaries without a shell or caller-controlled argv."""

    def __init__(self, config: SlotFlowAgentReachConfig) -> None:
        self._config = config
        self._home = config.home.expanduser().resolve()

    def run(self, executable: str, args: list[str]) -> str:
        if executable not in _ALLOWED_EXECUTABLES:
            raise AgentReachHostError(f"host executable {executable!r} is not allowlisted")
        if not self._home.is_dir():
            raise AgentReachHostError(
                f"Agent Reach home {self._home} is missing; rerun ./bootstrap.sh",
            )

        command = self._resolve_executable(executable)
        env = self._subprocess_env()
        try:
            completed = subprocess.run(
                [command, *args],
                cwd=self._home,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self._config.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentReachHostError(
                f"{executable} timed out after {self._config.timeout_seconds}s",
            ) from exc

        stdout, stdout_truncated = _bounded_decode(
            completed.stdout,
            self._config.max_output_bytes,
        )
        stderr, stderr_truncated = _bounded_decode(
            completed.stderr,
            self._config.max_output_bytes,
        )
        output = stdout.strip()
        if completed.returncode != 0:
            detail = stderr.strip() or output or f"exit code {completed.returncode}"
            raise AgentReachHostError(
                f"{executable} failed: {self._redact(detail)}",
            )
        if not output and stderr.strip():
            output = stderr.strip()
        if stdout_truncated or stderr_truncated:
            output = f"{output}\n[slotflow: output truncated]".strip()
        return self._redact(output)

    def _resolve_executable(self, executable: str) -> str:
        search_path = os.pathsep.join(
            [
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / ".volta" / "bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
        resolved = shutil.which(executable, path=search_path)
        if resolved is None:
            raise AgentReachHostError(
                f"{executable} is not installed on the host; rerun ./bootstrap.sh",
            )
        return resolved

    def _subprocess_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = str(Path.home())
        env["PATH"] = os.pathsep.join(
            [
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / ".volta" / "bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
        return env

    @staticmethod
    def _redact(text: str) -> str:
        redacted = text
        for name, value in os.environ.items():
            if not value or len(value) < 6:
                continue
            normalized = name.lower()
            if any(marker in normalized for marker in _SECRET_NAME_MARKERS):
                redacted = redacted.replace(value, "[REDACTED]")
        return redacted


def build_agent_reach_tools(
    config: SlotFlowAgentReachConfig,
    *,
    sandbox_config: SlotFlowSandboxConfig,
    runner: AgentReachRunner | None = None,
) -> list[BaseTool]:
    """Build fixed read-only internet tools backed by Agent Reach host channels."""

    if not config.enabled or not sandbox_config.network_enabled:
        return []

    resolved_runner = runner or FixedHostCommandRunner(config)
    mcporter_config = config.home.expanduser().resolve() / "config" / "mcporter.json"

    def agent_reach_status() -> str:
        """Check Agent Reach channel availability and active backends before internet research."""

        return resolved_runner.run("agent-reach", ["doctor", "--json"])

    def agent_reach_web_search(query: str, max_results: int = 5) -> str:
        """Search the public web through Agent Reach's Exa channel; this is read-only."""

        cleaned = _bounded_text(query, field_name="query", max_chars=500)
        limit = _bounded_int(max_results, minimum=1, maximum=10)
        return resolved_runner.run(
            "mcporter",
            [
                "--config",
                str(mcporter_config),
                "call",
                "exa.web_search_exa",
                f"query={cleaned}",
                f"numResults={limit}",
            ],
        )

    def agent_reach_read_url(url: str) -> str:
        """Read one public HTTP(S) URL as Markdown through Agent Reach's Jina Reader route."""

        target = validate_public_url(url, config=sandbox_config)
        reader_url = f"{_READER_PREFIX}{target}"
        return resolved_runner.run(
            "curl",
            [
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(config.timeout_seconds),
                "--max-filesize",
                str(config.max_output_bytes),
                "--header",
                "User-Agent: claude-code/2.1.214",
                reader_url,
            ],
        )

    def agent_reach_github_search(
        query: str,
        kind: Literal["repositories", "code", "issues", "pull_requests"] = "repositories",
        max_results: int = 10,
    ) -> str:
        """Search public GitHub repositories, code, issues, or pull requests via host gh."""

        cleaned = _bounded_text(query, field_name="query", max_chars=500)
        limit = _bounded_int(max_results, minimum=1, maximum=30)
        command, fields = _github_search_shape(kind)
        return resolved_runner.run(
            "gh",
            ["search", command, cleaned, "--limit", str(limit), "--json", fields],
        )

    def agent_reach_youtube_metadata(url: str) -> str:
        """Read compact metadata for one public YouTube video via Agent Reach's yt-dlp."""

        target = validate_public_url(url, config=sandbox_config)
        hostname = (urlsplit(target).hostname or "").lower()
        if hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            raise ValueError("url must point to youtube.com or youtu.be")
        projection = (
            "%(.{id,title,description,duration,channel,uploader,webpage_url,"
            "upload_date,view_count,like_count,chapters})#j"
        )
        raw = resolved_runner.run(
            "yt-dlp",
            [
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                "--print",
                projection,
                target,
            ],
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentReachHostError("yt-dlp returned invalid JSON") from exc
        return json.dumps(_youtube_summary(data), ensure_ascii=False)

    return [
        _threaded_tool(agent_reach_status),
        _threaded_tool(agent_reach_web_search),
        _threaded_tool(agent_reach_read_url),
        _threaded_tool(agent_reach_github_search),
        _threaded_tool(agent_reach_youtube_metadata),
    ]


def _threaded_tool(func) -> StructuredTool:
    async def coroutine(*args, **kwargs) -> str:
        return await asyncio.to_thread(func, *args, **kwargs)

    coroutine.__name__ = f"a{func.__name__}"
    return StructuredTool.from_function(func=func, coroutine=coroutine)


def _bounded_decode(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def _bounded_text(value: str, *, field_name: str, max_chars: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", cleaned):
        raise ValueError(f"{field_name} contains unsupported control characters")
    return cleaned


def _bounded_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _github_search_shape(kind: str) -> tuple[str, str]:
    shapes = {
        "repositories": ("repos", "fullName,description,url,stargazersCount,updatedAt"),
        "code": ("code", "path,repository,url,textMatches"),
        "issues": ("issues", "number,title,url,repository,state,updatedAt"),
        "pull_requests": ("prs", "number,title,url,repository,state,updatedAt"),
    }
    try:
        return shapes[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported GitHub search kind: {kind}") from exc


def _youtube_summary(data: dict) -> dict[str, object]:
    fields = (
        "id",
        "title",
        "description",
        "duration",
        "channel",
        "uploader",
        "webpage_url",
        "upload_date",
        "view_count",
        "like_count",
        "chapters",
    )
    return {field: data.get(field) for field in fields if data.get(field) is not None}
