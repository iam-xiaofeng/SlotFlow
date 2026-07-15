"""SlotFlow 只读 skills registry。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from app.harness.skills.parser import SKILL_FILE_NAME, parse_skill_file
from app.harness.skills.types import Skill

# Cache the disk scan (rglob + read every SKILL.md) so the prepare-node skills preflight
# does not re-read all skill files on every turn. The scan is the main contributor to
# first-token latency (cold ~2-3s with 25 skills). Invalidated by the skills_root mtime
# (install/remove changes the dir mtime) and a TTL safety net.
_SKILL_SCAN_CACHE: dict[tuple[str, int, float], tuple[float, list[Skill]]] = {}
_SKILL_SCAN_TTL = 60.0


def _scan_all_skills(skills_root: Path) -> list[Skill]:
    now = time.monotonic()
    try:
        root_mtime = skills_root.stat().st_mtime_ns
    except OSError:
        return []
    key = (str(skills_root), root_mtime, 0.0)
    cached = _SKILL_SCAN_CACHE.get(key)
    if cached is not None and now - cached[0] < _SKILL_SCAN_TTL:
        return cached[1]
    skills = [skill for skill in iter_skill_files(skills_root) if skill is not None]
    skills.sort(key=lambda skill: skill.name)
    _SKILL_SCAN_CACHE[key] = (now, skills)
    return skills


def invalidate_skill_scan_cache() -> None:
    """Drop the disk-scan cache (call after installing/removing a Skill)."""

    _SKILL_SCAN_CACHE.clear()


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

    skills = _scan_all_skills(skills_root)
    if enabled_names is not None:
        skills = [skill for skill in skills if skill.name in enabled_names]
    return skills


def iter_skill_files(skills_root: Path) -> Iterable[Skill | None]:
    """遍历 skills root 下的 `SKILL.md` 文件。"""

    for skill_file in sorted(skills_root.rglob(SKILL_FILE_NAME)):
        if any(part.startswith(".") for part in skill_file.relative_to(skills_root).parts):
            continue
        yield parse_skill_file(skill_file)


def build_skills_prompt(skills: list[Skill]) -> str:
    """把 enabled skills 转成 system prompt 片段。

    只列顶层 skill:分组(索引)skill 的成员物理上位于 ``<索引>/dependencies/`` 下,不单独
    进 prompt——否则一个十几技能的包会挤占模型注意力。成员内容由模型经索引 skill 的
    ``## Member skills`` 指引按需读取。
    """

    top_level = top_level_skills(skills)
    if not top_level:
        return ""

    lines = [
        "<slotflow-skills>",
        "Enabled skills for this run:",
    ]
    for skill in top_level:
        lines.append(f"- {skill.name}: {skill.description}")
    lines.append("</slotflow-skills>")
    return "\n".join(lines)


def top_level_skills(skills: list[Skill]) -> list[Skill]:
    """Drop skills that sit inside another skill's directory (grouped members)."""

    dirs = {skill.skill_dir.resolve() for skill in skills}
    result = []
    for skill in skills:
        skill_dir = skill.skill_dir.resolve()
        if any(other != skill_dir and other in skill_dir.parents for other in dirs):
            continue
        result.append(skill)
    return result
