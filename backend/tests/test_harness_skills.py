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
from app.harness.skills import build_skills_prompt, load_enabled_skills, parse_skill_file


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

    def fake_create_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder_module, "_create_agent_graph", fake_create_agent_graph)

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
        runtime_module,
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
