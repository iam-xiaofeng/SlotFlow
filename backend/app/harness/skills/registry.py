"""SlotFlow 只读 skills registry。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.harness.skills.parser import SKILL_FILE_NAME, parse_skill_file
from app.harness.skills.types import Skill


def load_enabled_skills(
    *,
    skills_root: Path | None,
    enabled_names: set[str] | None = None,
) -> list[Skill]:
    """扫描本地 skills 目录并返回 enabled skills。

    第一版没有 gateway 配置系统；如果 `enabled_names` 为 `None`，显式传入的 skills root 下
    所有有效 skill 都视为 enabled。后续接配置后再改成真正的开关状态。
    """

    if skills_root is None or not skills_root.exists():
        return []

    skills = [
        skill
        for skill in iter_skill_files(skills_root)
        if skill is not None and (enabled_names is None or skill.name in enabled_names)
    ]
    return sorted(skills, key=lambda skill: skill.name)


def iter_skill_files(skills_root: Path) -> Iterable[Skill | None]:
    """遍历 skills root 下的 `SKILL.md` 文件。"""

    for skill_file in sorted(skills_root.rglob(SKILL_FILE_NAME)):
        if any(part.startswith(".") for part in skill_file.relative_to(skills_root).parts):
            continue
        yield parse_skill_file(skill_file)


def build_skills_prompt(skills: list[Skill]) -> str:
    """把 enabled skills 转成 system prompt 片段。"""

    if not skills:
        return ""

    lines = [
        "<slotflow-skills>",
        "Enabled skills for this run:",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
        if skill.allowed_tools is None:
            lines.append("  allowed_tools: inherit")
        elif not skill.allowed_tools:
            lines.append("  allowed_tools: none")
        else:
            lines.append(f"  allowed_tools: {', '.join(skill.allowed_tools)}")
    lines.append("</slotflow-skills>")
    return "\n".join(lines)
