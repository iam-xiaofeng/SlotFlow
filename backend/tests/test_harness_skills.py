"""模块 12 测试：SlotFlow 只读 skills registry。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel

import app.chat.runtime as runtime_module
import app.harness.builder as builder_module
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import (
    SlotFlowRuntimeConfig,
    load_optional_csv_set_from_env,
    load_runtime_config_from_env,
)
from app.harness.config import SlotFlowHarnessConfig
from app.harness.skills import (
    build_skills_prompt,
    load_enabled_skills,
    parse_skill_file,
    read_skill,
)
from app.harness.tools.customization import build_customization_tools


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "测试 skill",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"""---
name: {name}
description: {description}
---

# {name}
""",
        encoding="utf-8",
    )
    return skill_file


def _run_context():
    request = ChatStreamRequest(message="解释 skills", mode="pro")
    return build_run_config(
        thread_id="thread_skills",
        run_id="run_skills",
        request=request,
    ).context


def test_parse_skill_file_reads_required_metadata(tmp_path: Path) -> None:
    """parser 只读 SKILL.md 的 name / description。"""

    skill_file = _write_skill(
        tmp_path,
        "alpha",
        description="Alpha skill",
    )
    skill = parse_skill_file(skill_file)

    assert skill.name == "alpha"
    assert skill.description == "Alpha skill"
    assert skill.skill_dir == tmp_path / "alpha"


def test_load_enabled_skills_scans_root_and_filters_by_name(tmp_path: Path) -> None:
    """registry 会扫描 skills root，并用 enabled_names 做第一版启用过滤。"""

    _write_skill(tmp_path, "alpha", description="Alpha skill")
    _write_skill(tmp_path, "beta", description="Beta skill")

    skills = load_enabled_skills(
        skills_root=tmp_path,
        enabled_names={"beta"},
    )

    assert [skill.name for skill in skills] == ["beta"]
    assert skills[0].description == "Beta skill"


def test_build_skills_prompt_keeps_prompt_shape_explicit(tmp_path: Path) -> None:
    """skills prompt 是给模型看的能力说明，不是工具实现本身。"""

    _write_skill(tmp_path, "alpha", description="Alpha skill")
    skills = load_enabled_skills(skills_root=tmp_path)

    prompt = build_skills_prompt(skills)

    assert "<slotflow-skills>" in prompt
    assert "- alpha: Alpha skill" in prompt
    assert "</slotflow-skills>" in prompt


def test_harness_builder_injects_enabled_skills_into_system_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """builder 会把 enabled skills 摘要拼进 system prompt。"""

    _write_skill(tmp_path, "alpha", description="Alpha skill")
    captured: dict[str, Any] = {}

    def fake_build_slotflow_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "build_slotflow_graph", fake_build_slotflow_graph)

    builder_module.build_slotflow_harness_graph(
        model=FakeListChatModel(responses=["ok"]),
        run_context=_run_context(),
        harness_config=SlotFlowHarnessConfig(
            system_prompt="base prompt",
            skills_root=tmp_path,
            enabled_skills={"alpha"},
        ),
    )

    assert "base prompt" in captured["system_prompt"]
    assert "<slotflow-skills>" in captured["system_prompt"]
    assert "Alpha skill" in captured["system_prompt"]


def test_runtime_config_loads_optional_skills_env(monkeypatch, tmp_path: Path) -> None:
    """runtime 只读取环境变量，真正 skills 加载仍交给 harness。"""

    monkeypatch.setenv("SLOTFLOW_SKILLS_ROOT", str(tmp_path))
    monkeypatch.setenv("SLOTFLOW_ENABLED_SKILLS", "alpha, beta")

    config = load_runtime_config_from_env()

    assert config.skills_root == tmp_path
    assert config.enabled_skills == {"alpha", "beta"}
    assert load_optional_csv_set_from_env("SLOTFLOW_ENABLED_SKILLS") == {
        "alpha",
        "beta",
    }


def test_runtime_passes_skills_config_to_harness(monkeypatch, tmp_path: Path) -> None:
    """runtime 到 harness 的委托不能丢失 skills 配置。"""

    captured: dict[str, Any] = {}

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
            skills_root=tmp_path,
            enabled_skills={"alpha"},
        ),
        run_context=_run_context(),
    )

    assert captured["harness_config"] == SlotFlowHarnessConfig(
        system_prompt="base prompt",
        skills_root=tmp_path,
        enabled_skills={"alpha"},
    )


def _write_skill_with_body(root: Path, name: str, *, body: str, extra: dict[str, str] | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for relative, content in (extra or {}).items():
        target = skill_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


def test_skills_prompt_lists_only_the_catalog_and_points_at_skill_read(tmp_path: Path) -> None:
    """system 前缀里只有目录:正文按需经 skill_read 进上下文,装再多 Skill 也不撑前缀。"""

    _write_skill_with_body(tmp_path, "alpha", body="# Alpha\n第一步:绝密操作流程")
    prompt = build_skills_prompt(load_enabled_skills(skills_root=tmp_path))

    assert "- alpha: alpha skill" in prompt
    assert "skill_read(name)" in prompt
    assert "绝密操作流程" not in prompt  # 正文绝不进 system 前缀


def test_read_skill_returns_body_without_frontmatter_and_lists_bundled_files(tmp_path: Path) -> None:
    _write_skill_with_body(
        tmp_path,
        "report",
        body="# Report\n1. 先跑 scripts/build.py\n2. 再写结论",
        extra={"scripts/build.py": "print('hi')\n", "reference/style.md": "# style\n"},
    )

    result = read_skill(skills_root=tmp_path, name="report")

    assert result["skill"] == "report"
    assert result["content"].startswith("# Report")
    assert "name: report" not in result["content"]  # frontmatter 已剥掉
    assert result["truncated"] is False
    assert {entry["path"] for entry in result["files"]} == {"scripts/build.py", "reference/style.md"}


def test_read_skill_reads_a_bundled_file_and_refuses_to_escape_the_skill_dir(tmp_path: Path) -> None:
    _write_skill_with_body(tmp_path, "report", body="body", extra={"reference/style.md": "# style guide\n"})
    (tmp_path / "secret.txt").write_text("do not leak", encoding="utf-8")

    inside = read_skill(skills_root=tmp_path, name="report", path="reference/style.md")
    assert inside["content"].strip() == "# style guide"

    escaped = read_skill(skills_root=tmp_path, name="report", path="../secret.txt")
    assert escaped["error"] == "path_outside_skill_dir"
    assert "do not leak" not in str(escaped)


def test_read_skill_truncates_long_bodies_with_a_resume_offset(tmp_path: Path) -> None:
    _write_skill_with_body(tmp_path, "long", body="x" * 500)

    first = read_skill(skills_root=tmp_path, name="long", max_chars=100)

    assert first["truncated"] is True
    assert len(first["content"]) == 100
    rest = read_skill(skills_root=tmp_path, name="long", offset=first["next_offset"], max_chars=1000)
    assert rest["truncated"] is False
    assert first["content"] + rest["content"] == "x" * 500 + "\n"


def test_read_skill_reports_an_unknown_name_with_candidates(tmp_path: Path) -> None:
    _write_skill_with_body(tmp_path, "alpha", body="body")

    result = read_skill(skills_root=tmp_path, name="alpah")

    assert result["error"] == "skill_not_found"
    assert result["available_skills"] == ["alpha"]


def test_skill_read_tool_records_the_skill_in_the_compaction_ledger(tmp_path: Path) -> None:
    """读过的 Skill 要进 used_skills 台账:压缩会折叠正文,台账让模型知道该重读谁。"""

    _write_skill_with_body(tmp_path, "alpha", body="# Alpha\nstep one")
    tool = next(
        item
        for item in build_customization_tools(
            skills_root=tmp_path,
            skills_config_store=None,
            mcp_config_store=None,
        )
        if item.name == "skill_read"
    )

    command = tool.invoke({"name": "alpha", "id": "call-1", "type": "tool_call", "args": {"name": "alpha"}})

    assert command.update["used_skills"] == ["alpha"]
    message = command.update["messages"][0]
    assert message.status == "success"
    assert "step one" in message.content


def test_skill_read_tool_does_not_record_a_failed_read(tmp_path: Path) -> None:
    tool = next(
        item
        for item in build_customization_tools(
            skills_root=tmp_path,
            skills_config_store=None,
            mcp_config_store=None,
        )
        if item.name == "skill_read"
    )

    command = tool.invoke({"name": "missing", "id": "call-2", "type": "tool_call", "args": {"name": "missing"}})

    assert "used_skills" not in command.update
    assert command.update["messages"][0].status == "error"
