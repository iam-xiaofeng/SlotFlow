"""模块 15 测试：SlotFlow sandbox / workspace 边界。"""

from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import app.chat.runtime as runtime_module
import app.harness.builder as builder_module
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import (
    SlotFlowRuntimeConfig,
    load_positive_int_from_env,
    load_runtime_config_from_env,
)
from app.harness.config import SlotFlowHarnessConfig
from app.harness.sandbox import (
    SlotFlowSandboxConfig,
    SlotFlowWorkspace,
    WorkspaceFileTooLargeError,
    WorkspacePathError,
    WorkspaceWriteDisabledError,
)
from app.harness.sandbox.docker import DockerSandboxError, LazyDockerSandbox
from app.harness.sandbox.docker_engine import DockerEngineSetup
from app.harness.tools.sandbox import build_sandbox_tools


class ToolAwareFakeListChatModel(FakeListChatModel):
    """测试用 fake model：普通 fake 文本能力 + 支持 bind_tools 边界。"""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _run_context():
    request = ChatStreamRequest(message="解释 sandbox", mode="pro")
    return build_run_config(
        thread_id="thread_sandbox",
        run_id="run_sandbox",
        request=request,
    ).context


def test_workspace_resolves_relative_paths_under_root(tmp_path: Path) -> None:
    """合法路径会被解析到 workspace root 内部。"""

    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )

    assert workspace.resolve_path("notes/today.md") == (
        tmp_path / "workspace" / "notes" / "today.md"
    ).resolve(strict=False)
    assert workspace.resolve_path(".") == (tmp_path / "workspace").resolve(strict=False)


def test_workspace_rejects_unsafe_user_paths(tmp_path: Path) -> None:
    """用户输入的路径不能是绝对路径、穿越路径或 Windows drive 风格路径。"""

    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )

    unsafe_paths = [
        "",
        "/etc/passwd",
        "../outside.txt",
        "notes/../../outside.txt",
        "C:/temp/file.txt",
        r"notes\file.txt",
        "bad\x00name.txt",
    ]
    for unsafe_path in unsafe_paths:
        try:
            workspace.resolve_path(unsafe_path)
        except WorkspacePathError:
            continue
        raise AssertionError(f"unsafe path was accepted: {unsafe_path!r}")


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    """即使 workspace 内部有 symlink，也不能借它跳出 root。"""

    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    workspace = SlotFlowWorkspace(SlotFlowSandboxConfig(workspace_root=root))

    try:
        workspace.resolve_path("escape/file.txt")
    except WorkspacePathError:
        pass
    else:
        raise AssertionError("symlink escape was accepted")


def test_workspace_read_and_list_enforce_root_and_read_size(tmp_path: Path) -> None:
    """workspace 可以列目录和读小文件，但会拒绝超限文件。"""

    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("ok", encoding="utf-8")
    (root / "big.txt").write_text("too large", encoding="utf-8")
    workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(
            workspace_root=root,
            max_read_bytes=4,
        )
    )

    assert workspace.read_text("docs/a.txt") == "ok"
    assert [(entry.path, entry.kind, entry.size_bytes) for entry in workspace.list_entries(".")] == [
        ("big.txt", "file", 9),
        ("docs", "directory", None),
    ]

    try:
        workspace.read_text("big.txt")
    except WorkspaceFileTooLargeError:
        pass
    else:
        raise AssertionError("oversized read was accepted")


def test_workspace_write_can_be_disabled_and_is_byte_limited(tmp_path: Path) -> None:
    """显式关闭写入后拒绝写；打开后仍受 max_write_bytes 约束。"""

    read_only_workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "readonly", writes_enabled=False)
    )
    try:
        read_only_workspace.write_text("a.txt", "hello")
    except WorkspaceWriteDisabledError:
        pass
    else:
        raise AssertionError("write was accepted while disabled")

    writable_workspace = SlotFlowWorkspace(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "writable",
            writes_enabled=True,
            max_write_bytes=5,
        )
    )
    target = writable_workspace.write_text("nested/a.txt", "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    try:
        writable_workspace.write_text("nested/b.txt", "too large")
    except WorkspaceFileTooLargeError:
        pass
    else:
        raise AssertionError("oversized write was accepted")


def test_runtime_loads_sandbox_config_from_env(monkeypatch, tmp_path: Path) -> None:
    """runtime 只解析 sandbox 配置，不创建 workspace 或工具。"""

    monkeypatch.setenv("SLOTFLOW_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_WRITES_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", "123")
    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_WRITE_BYTES", "456")
    monkeypatch.setenv("SLOTFLOW_CODE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_DOCKER_SANDBOX_IMAGE", "python:3.13-slim")
    monkeypatch.setenv("SLOTFLOW_DOCKER_SANDBOX_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("SLOTFLOW_DOCKER_SANDBOX_NETWORK_ENABLED", "true")
    monkeypatch.setenv("SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL", "true")

    config = load_runtime_config_from_env()

    assert config.sandbox_config == SlotFlowSandboxConfig(
        workspace_root=tmp_path / "workspace",
        writes_enabled=True,
        max_read_bytes=123,
        max_write_bytes=456,
        code_execution_enabled=True,
        docker_image="python:3.13-slim",
        docker_timeout_seconds=12,
        docker_network_enabled=True,
        allow_host_docker_install=True,
    )


def test_sandbox_config_defaults_support_dependency_installation() -> None:
    """默认 Docker 沙箱应支持模型安装 Python 依赖。"""

    config = SlotFlowSandboxConfig()

    assert config.docker_image == "python:3.12"
    assert config.docker_timeout_seconds == 120
    assert config.docker_network_enabled is True
    assert config.allow_host_docker_install is True


def test_positive_int_env_validation(monkeypatch) -> None:
    """字节上限必须是正整数。"""

    monkeypatch.setenv("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", "0")

    try:
        load_positive_int_from_env("SLOTFLOW_WORKSPACE_MAX_READ_BYTES", default=10)
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("non-positive integer was accepted")


def test_runtime_passes_sandbox_config_to_harness(monkeypatch, tmp_path: Path) -> None:
    """runtime 到 harness 的委托不能丢失 sandbox 配置。"""

    captured: dict[str, Any] = {}
    sandbox_config = SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")

    def fake_build_slotflow_harness_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        runtime_module.adapter,
        "build_slotflow_harness_graph",
        fake_build_slotflow_harness_graph,
    )

    runtime_module.create_langgraph_agent_graph(
        model=FakeListChatModel(responses=["ok"]),
        runtime_config=SlotFlowRuntimeConfig(
            system_prompt="base prompt",
            sandbox_config=sandbox_config,
        ),
        run_context=_run_context(),
    )

    assert captured["harness_config"] == SlotFlowHarnessConfig(
        system_prompt="base prompt",
        sandbox_config=sandbox_config,
    )


def test_harness_builder_passes_sandbox_config_to_tool_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """builder 会把 sandbox 配置交给 tools registry，供后续文件工具使用。"""

    captured_tools_kwargs: dict[str, Any] = {}
    sandbox_config = SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")

    def fake_build_harness_tools(**kwargs):
        captured_tools_kwargs.update(kwargs)
        return []

    def fake_build_slotflow_graph(**kwargs):
        return object()

    monkeypatch.setattr(builder_module, "build_harness_tools", fake_build_harness_tools)
    monkeypatch.setattr(builder_module, "build_slotflow_graph", fake_build_slotflow_graph)

    builder_module.build_slotflow_harness_graph(
        model=ToolAwareFakeListChatModel(responses=["ok"]),
        run_context=_run_context(),
        harness_config=SlotFlowHarnessConfig(
            system_prompt="base prompt",
            sandbox_config=sandbox_config,
        ),
    )

    assert captured_tools_kwargs["sandbox_config"] is sandbox_config


def test_lazy_docker_sandbox_starts_only_on_first_exec(tmp_path: Path) -> None:
    """Docker 容器懒加载；uploads/skills 只读，当前 thread artifacts/work 可写回本地。"""

    calls: list[list[str]] = []

    def fake_runner(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(args, 0, stdout="container123\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    root = tmp_path / "workspace"
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    sandbox = LazyDockerSandbox(
        config=SlotFlowSandboxConfig(workspace_root=root, docker_image="python:test"),
        thread_id="thread-1",
        skills_root=skills_root,
        runner=fake_runner,
    )

    assert sandbox.started is False
    assert calls == []

    result = sandbox.exec("python -V")

    assert result["ok"] is True
    assert sandbox.started is True
    assert calls[0][:2] == ["docker", "run"]
    assert calls[1][:2] == ["docker", "exec"]
    run_command = calls[0]
    assert "--init" in run_command
    assert "--network" in run_command
    assert "bridge" in run_command
    assert "PYTHONUNBUFFERED=1" in run_command
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in run_command
    assert "python:test" in run_command
    mounts = [
        run_command[index + 1]
        for index, value in enumerate(run_command)
        if value == "--mount"
    ]
    assert any("/workspace/uploads" in mount and "readonly=true" in mount for mount in mounts)
    assert any("/workspace/skills" in mount and "readonly=true" in mount for mount in mounts)
    assert any("/workspace/artifacts" in mount and "readonly" not in mount for mount in mounts)
    assert any("/workspace/work" in mount and "readonly" not in mount for mount in mounts)
    assert (root / "artifacts" / "thread-1").is_dir()
    assert (root / ".sandbox" / "thread-1").is_dir()


def test_sandbox_exec_tool_is_disabled_by_config(tmp_path: Path) -> None:
    """关闭代码执行时不注册 sandbox_exec。"""

    tools = build_sandbox_tools(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            code_execution_enabled=False,
        )
    )

    assert tools == []


def test_docker_engine_setup_tool_returns_install_script_when_host_install_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Docker 安装入口默认不改宿主机，只返回受控脚本和 opt-in 提示。"""

    monkeypatch.setenv("SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL", "false")
    tools = build_sandbox_tools(
        SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            allow_host_docker_install=False,
        )
    )
    setup_tool = next(tool for tool in tools if tool.name == "docker_engine_setup")

    result = setup_tool.invoke(
        {
            "action": "install",
            "confirm_host_install": True,
        }
    )
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"] == "host Docker Engine install is disabled"
    assert "SLOTFLOW_ALLOW_HOST_DOCKER_INSTALL=false" in payload["hint"]
    assert "apt-get install -y docker.io docker-compose-v2" in payload["script"]


def test_docker_engine_setup_check_reports_missing_docker(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu"\n', encoding="utf-8")
    setup = DockerEngineSetup(
        config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace"),
        which=lambda name: None,
        os_release_path=os_release,
    )

    result = setup.run(action="check")

    assert result["ok"] is False
    assert result["installed"] is False
    assert result["error"] == "docker_cli_missing"
    assert result["host"]["install_manager"] == "apt"
    assert result["host"]["install_supported"] is True


def test_docker_engine_setup_install_script_matches_detected_host(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=fedora\nPRETTY_NAME="Fedora"\n', encoding="utf-8")
    setup = DockerEngineSetup(
        config=SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace"),
        os_release_path=os_release,
    )

    result = setup.run(action="install_script")

    assert result["ok"] is True
    assert result["host"]["install_manager"] == "dnf"
    assert "dnf install -y moby-engine docker-compose-plugin" in result["script"]
    assert "apt-get" not in result["script"]


def test_docker_engine_setup_install_uses_fixed_commands(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """允许自动安装时也只能执行固定 apt/systemctl/usermod 命令，不能运行模型脚本。"""

    os_release = tmp_path / "os-release"
    os_release.write_text('ID=ubuntu\nID_LIKE=debian\n', encoding="utf-8")
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in {"apt-get", "sudo", "docker"} else None

    def fake_runner(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(args, 0, stdout="26.1.0\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("app.harness.sandbox.docker_engine.os.geteuid", lambda: 1000)
    monkeypatch.setenv("USER", "dell")
    setup = DockerEngineSetup(
        config=SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            allow_host_docker_install=True,
        ),
        runner=fake_runner,
        which=fake_which,
        os_release_path=os_release,
    )

    result = setup.run(action="install", confirm_host_install=True)

    assert result["ok"] is True
    assert calls == [
        ["sudo", "-n", "apt-get", "update"],
        ["sudo", "-n", "apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
        ["sudo", "-n", "systemctl", "enable", "--now", "docker"],
        ["sudo", "-n", "usermod", "-aG", "docker", "dell"],
        ["docker", "info", "--format", "{{.ServerVersion}}"],
    ]


def test_lazy_docker_sandbox_uses_no_network_when_disabled(tmp_path: Path) -> None:
    """显式关闭网络时，容器启动参数必须使用 none。"""

    calls: list[list[str]] = []

    def fake_runner(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(args, 0, stdout="container123\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    sandbox = LazyDockerSandbox(
        config=SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            docker_network_enabled=False,
        ),
        runner=fake_runner,
    )

    sandbox.exec("python -V")

    assert "none" in calls[0]


def test_sandbox_exec_tool_returns_structured_error_without_docker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Docker CLI 不存在时工具返回结构化错误，不让 graph 崩溃。"""

    def unavailable_exec(self, command: str, *, timeout_seconds=None):
        raise DockerSandboxError("Docker CLI is required for sandbox_exec")

    monkeypatch.setattr(LazyDockerSandbox, "exec", unavailable_exec)
    tool = build_sandbox_tools(
        SlotFlowSandboxConfig(workspace_root=tmp_path / "workspace")
    )[0]

    result = tool.invoke(
        {
            "command": "python -V",
        }
    )
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["error"] == "Docker CLI is required for sandbox_exec"
    assert "Install/start Docker" in payload["hint"]


def test_lazy_docker_sandbox_runs_real_container_when_docker_is_available(tmp_path: Path) -> None:
    """有 Docker 时跑真实容器，验证 Python/pip/bash 基础能力。"""

    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed in this environment")

    sandbox = LazyDockerSandbox(
        config=SlotFlowSandboxConfig(
            workspace_root=tmp_path / "workspace",
            docker_image="python:3.12",
            docker_timeout_seconds=120,
        ),
        thread_id="live",
    )
    try:
        result = sandbox.exec(
            "python -V && bash --version | head -n 1 && python -m pip --version",
            timeout_seconds=60,
        )
    finally:
        sandbox.close()

    assert result["ok"] is True, result
    assert "Python 3.12" in result["stdout"]
    assert "bash" in result["stdout"].lower()
    assert "pip" in result["stdout"].lower()
