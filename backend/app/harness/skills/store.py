"""Persistent Skill configuration and registry installer helpers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.harness.skills.parser import SKILL_FILE_NAME, parse_skill_file

DEFAULT_FIND_SKILLS_NAME = "find-skills"
DEFAULT_FIND_SKILLS_PACKAGE = "https://github.com/vercel-labs/skills"
_UNSET = object()


class SkillNotFoundError(KeyError):
    """Raised when a configured skill is missing."""


class ProtectedSkillError(ValueError):
    """Raised when a protected skill would be mutated destructively."""


@dataclass(frozen=True, slots=True)
class SkillConfig:
    enabled: bool = True
    protected: bool = False
    source: str = "user"
    package_url: str | None = None
    order: int = 0
    pinned: bool = False
    parent: str | None = None


class SlotFlowSkillsConfigStore:
    """Persist user-visible skill state separately from the skill folder tree."""

    def __init__(self, path: str | Path, *, skills_root: str | Path) -> None:
        self.path = Path(path)
        self.skills_root = Path(skills_root)

    def ensure_default_find_skills(self) -> None:
        """Ensure the built-in protected find-skills entry exists locally."""

        skill_dir = self.skills_root / DEFAULT_FIND_SKILLS_NAME
        skill_file = skill_dir / SKILL_FILE_NAME
        if not skill_file.is_file():
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(default_find_skills_content(), encoding="utf-8")

        self.mark_skill(
            DEFAULT_FIND_SKILLS_NAME,
            enabled=True,
            protected=True,
            source="skills.sh",
            package_url=DEFAULT_FIND_SKILLS_PACKAGE,
            order=0,
            pinned=True,
            parent=None,
        )

    def configs(self) -> dict[str, SkillConfig]:
        data = self._read_data()
        raw_skills = data.get("skills", {})
        if not isinstance(raw_skills, dict):
            return {}

        configs: dict[str, SkillConfig] = {}
        for name, raw_config in raw_skills.items():
            if not isinstance(name, str) or not isinstance(raw_config, dict):
                continue
            enabled = raw_config.get("enabled", True)
            protected = raw_config.get("protected", False)
            source = raw_config.get("source", "user")
            package_url = raw_config.get("package_url")
            order = raw_config.get("order", 0)
            pinned = raw_config.get("pinned", False)
            parent = raw_config.get("parent")
            configs[name] = SkillConfig(
                enabled=enabled if isinstance(enabled, bool) else True,
                protected=protected if isinstance(protected, bool) else False,
                source=source if isinstance(source, str) else "user",
                package_url=package_url if isinstance(package_url, str) else None,
                order=order if isinstance(order, int) else 0,
                pinned=pinned if isinstance(pinned, bool) else False,
                parent=parent if isinstance(parent, str) and parent.strip() else None,
            )
        return configs

    def get_config(self, name: str) -> SkillConfig:
        return self.configs().get(name, SkillConfig())

    def is_enabled(self, name: str) -> bool:
        return self.get_config(name).enabled

    def is_protected(self, name: str) -> bool:
        return self.get_config(name).protected

    def enabled_skill_names(self, discovered_names: set[str]) -> set[str]:
        return {
            name
            for name in discovered_names
            if self.get_config(name).enabled
        }

    def infer_missing_dependency_parents(self) -> None:
        """Group legacy skills from the same registry install under their first skill.

        New installs record dependency parents when the CLI returns extra skills. Older
        local configs may only have the shared package URL, so the UI would show every
        dependency as a top-level skill. This migration only fills blank parents and
        leaves protected or explicitly grouped skills unchanged.
        """

        configs = self.configs()
        groups: dict[str, list[tuple[str, SkillConfig]]] = {}
        for name, config in configs.items():
            if (
                config.protected
                or not config.package_url
                or not config.source.startswith("skills.sh")
            ):
                continue
            groups.setdefault(config.package_url, []).append((name, config))

        changed = False
        for group in groups.values():
            if len(group) <= 1:
                continue
            ordered = sorted(group, key=skill_config_sort_key)
            root_name = next(
                (
                    name
                    for name, _ in ordered
                    if any(child_config.parent == name for _, child_config in ordered)
                ),
                ordered[0][0],
            )
            for name, config in ordered:
                if name == root_name or config.parent is not None:
                    continue
                configs[name] = replace_skill_config(config, parent=root_name)
                changed = True

        if changed:
            self._write_configs(configs)

    def set_enabled(self, name: str, enabled: bool) -> SkillConfig:
        configs = self.configs()
        current = configs.get(name, SkillConfig())
        configs[name] = replace_skill_config(current, enabled=enabled)
        self._write_configs(configs)
        return configs[name]

    def set_pinned(self, name: str, pinned: bool) -> SkillConfig:
        configs = self.configs()
        current = configs.get(name, SkillConfig())
        configs[name] = replace_skill_config(current, pinned=pinned)
        self._write_configs(configs)
        return configs[name]

    def reorder_skills(self, ordered_names: list[str]) -> dict[str, SkillConfig]:
        configs = self.configs()
        for index, name in enumerate(dict.fromkeys(ordered_names)):
            current = configs.get(name, SkillConfig())
            configs[name] = replace_skill_config(current, order=index)
        self._write_configs(configs)
        return configs

    def mark_skill(
        self,
        name: str,
        *,
        enabled: bool = True,
        protected: bool = False,
        source: str = "user",
        package_url: str | None = None,
        order: int | None = None,
        pinned: bool | None = None,
        parent: Any = _UNSET,
    ) -> SkillConfig:
        configs = self.configs()
        current = configs.get(name)
        next_parent = current.parent if parent is _UNSET and current else None
        if parent is not _UNSET:
            next_parent = parent
        configs[name] = SkillConfig(
            enabled=enabled if current is None else current.enabled,
            protected=protected,
            source=source,
            package_url=package_url,
            order=order if order is not None else (current.order if current else next_skill_order(configs)),
            pinned=pinned if pinned is not None else (current.pinned if current else False),
            parent=next_parent,
        )
        self._write_configs(configs)
        return configs[name]

    def remove_skill_config(self, name: str) -> None:
        configs = self.configs()
        if configs.get(name, SkillConfig()).protected:
            raise ProtectedSkillError(name)
        configs.pop(name, None)
        self._write_configs(configs)

    def remove_skill_tree_config(self, name: str) -> None:
        configs = self.configs()
        if configs.get(name, SkillConfig()).protected:
            raise ProtectedSkillError(name)
        names_to_remove = skill_config_tree_names(configs, name)
        for skill_name in names_to_remove:
            config = configs.get(skill_name, SkillConfig())
            if skill_name in configs:
                if config.protected:
                    raise ProtectedSkillError(skill_name)
                configs.pop(skill_name, None)
        self._write_configs(configs)

    def install_skill_from_registry(
        self,
        *,
        package_url: str,
        skill_name: str,
        timeout_seconds: int = 120,
    ) -> Path:
        """Install one skill through the public skills CLI into skills_root.

        同包的多个 skills 是平级的,安装时不做主/子推断;需要收拢时由模型或用户创建
        索引 skill(``create_skill_group``),分组关系由磁盘结构(dependencies/ 嵌套)承载。
        """

        if self.is_protected(skill_name):
            raise ProtectedSkillError(skill_name)
        validate_install_request(package_url=package_url, skill_name=skill_name)

        with tempfile.TemporaryDirectory(prefix="slotflow-skills-") as temp_dir:
            temp_path = Path(temp_dir)
            completed = subprocess.run(
                [
                    "npx",
                    "-y",
                    "skills",
                    "add",
                    package_url,
                    "--skill",
                    skill_name,
                    "--agent",
                    "codex",
                    "--copy",
                    "-y",
                ],
                cwd=temp_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout).strip() or "skills install failed")

            source_root = temp_path / ".agents" / "skills"
            source_dir = source_root / skill_name
            skill_file = source_dir / SKILL_FILE_NAME
            if parse_skill_file(skill_file) is None:
                raise RuntimeError("installed skill is missing a valid SKILL.md")

            target_dir = self.skills_root / skill_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, target_dir)

            dependency_records: list[tuple[str, Path]] = []
            for child_dir in sorted(source_root.iterdir() if source_root.is_dir() else []):
                if child_dir == source_dir or not child_dir.is_dir():
                    continue
                child_skill = parse_skill_file(child_dir / SKILL_FILE_NAME)
                if child_skill is None:
                    continue
                child_target = target_dir / "dependencies" / child_dir.name
                if child_target.exists():
                    shutil.rmtree(child_target)
                child_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(child_dir, child_target)
                dependency_records.append((child_skill.name, child_target))

        self.mark_skill(
            skill_name,
            enabled=True,
            protected=False,
            source="skills.sh",
            package_url=package_url,
            parent=None,
        )
        for child_name, _ in dependency_records:
            self.mark_skill(
                child_name,
                enabled=True,
                protected=False,
                source="skills.sh dependency",
                package_url=package_url,
                parent=skill_name,
            )
        return self.skills_root / skill_name

    def create_skill_group(
        self,
        *,
        name: str,
        description: str,
        content: str,
        member_dirs: dict[str, Path],
    ) -> Path:
        """Create an index skill that groups existing top-level skills.

        没有天然的主/子 skill:一批平行 skills(比如一个论文流水线包的十几个技能)会把
        system prompt 撑爆,所以由模型或用户写一个索引 skill(名称/描述/正文自定),成员
        整体移动到 ``<索引>/dependencies/`` 下。分组由磁盘结构承载;prompt 只列顶层
        skill,成员内容由模型按索引 skill 的指引按需读取。
        """

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("group name must use letters, numbers, dots, underscores, or hyphens")
        if not description.strip():
            raise ValueError("group description must not be empty")
        if not member_dirs:
            raise ValueError("group needs at least one member skill")
        if self.is_protected(name):
            raise ProtectedSkillError(name)

        group_dir = self.skills_root / name
        if group_dir.exists():
            raise ValueError(f"skill directory already exists: {name}")

        resolved_root = self.skills_root.resolve()
        for member_name, member_dir in member_dirs.items():
            resolved = member_dir.resolve()
            if self.is_protected(member_name):
                raise ProtectedSkillError(member_name)
            if resolved.parent != resolved_root:
                raise ValueError(
                    f"member skill is not a top-level skill directory: {member_name}"
                )

        dependencies_dir = group_dir / "dependencies"
        dependencies_dir.mkdir(parents=True)
        (group_dir / SKILL_FILE_NAME).write_text(
            build_skill_group_markdown(
                name=name,
                description=description,
                content=content,
                member_dirs=member_dirs,
            ),
            encoding="utf-8",
        )
        for member_dir in member_dirs.values():
            shutil.move(str(member_dir), str(dependencies_dir / member_dir.name))

        configs = self.configs()
        configs[name] = SkillConfig(
            enabled=True,
            protected=False,
            source="group",
            order=next_skill_order(configs),
        )
        for member_name in member_dirs:
            current = configs.get(member_name, SkillConfig())
            configs[member_name] = replace_skill_config(current, parent=name)
        self._write_configs(configs)
        return group_dir

    def _read_data(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"skills": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"skills": {}}
        return data if isinstance(data, dict) else {"skills": {}}

    def _write_configs(self, configs: dict[str, SkillConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": {
                name: {
                    "enabled": config.enabled,
                    "protected": config.protected,
                    "source": config.source,
                    "order": config.order,
                    "pinned": config.pinned,
                    **({"package_url": config.package_url} if config.package_url else {}),
                    **({"parent": config.parent} if config.parent else {}),
                }
                for name, config in sorted(configs.items(), key=skill_config_sort_key)
            }
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def validate_install_request(*, package_url: str, skill_name: str) -> None:
    if not package_url.startswith(("https://", "http://")):
        raise ValueError("package_url must start with http:// or https://")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", skill_name):
        raise ValueError("skill_name must use letters, numbers, dots, underscores, or hyphens")


def build_skill_group_markdown(
    *,
    name: str,
    description: str,
    content: str,
    member_dirs: dict[str, Path],
) -> str:
    """Render the index skill's SKILL.md.

    正文由创建者(模型或用户)提供;成员清单(名字/相对目录/描述)自动追加,保证无论正文
    写得如何,模型都能从索引 skill 定位到每个成员的 SKILL.md。
    """

    member_lines = []
    for member_name in sorted(member_dirs):
        member_skill = parse_skill_file(
            member_dirs[member_name] / SKILL_FILE_NAME
        )
        summary = member_skill.description.strip() if member_skill else ""
        first_sentence = summary.split("\n")[0][:200] if summary else ""
        member_lines.append(
            f"- `{member_name}` — read `dependencies/{member_dirs[member_name].name}/SKILL.md`"
            + (f" — {first_sentence}" if first_sentence else "")
        )

    body = content.strip() or f"# {name}\n\nIndex skill grouping related member skills."
    # 用单行标量描述:parser 只认极简 frontmatter(不支持 YAML block scalar),否则索引
    # skill 自身的 description 会被解析成空、整条 skill 被丢弃。
    inline_description = " ".join(description.strip().split())
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_inline_quote(inline_description)}\n"
        "---\n\n"
        f"{body}\n\n"
        "## Member skills\n\n"
        "Each member below is a full skill. When this index skill matches the task,\n"
        "read the relevant member's SKILL.md (path relative to this directory) and\n"
        "follow it.\n\n" + "\n".join(member_lines) + "\n"
    )


def _yaml_inline_quote(value: str) -> str:
    """Quote a one-line description so the minimal frontmatter parser reads it back whole."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def replace_skill_config(config: SkillConfig, **updates: Any) -> SkillConfig:
    return replace(config, **updates)


def next_skill_order(configs: dict[str, SkillConfig]) -> int:
    if not configs:
        return 0
    return max(config.order for config in configs.values()) + 1


def skill_config_tree_names(configs: dict[str, SkillConfig], root_name: str) -> set[str]:
    """Return the root skill and all configured descendants by parent link."""

    names = {root_name}
    changed = True
    while changed:
        changed = False
        for name, config in configs.items():
            if name not in names and config.parent in names:
                names.add(name)
                changed = True
    return names


def skill_config_sort_key(item: tuple[str, SkillConfig]) -> tuple[bool, int, str]:
    name, config = item
    return (not config.pinned, config.order, name)


def default_find_skills_content() -> str:
    return """---
name: find-skills
description: Find and recommend installable Skills from https://www.skills.sh.
---

# Find Skills

Use this skill when the user wants to discover Skills from https://www.skills.sh
or install a known Skill package. Prefer concrete recommendations and include the
exact install command when helpful, for example:

```bash
npx skills add https://github.com/vercel-labs/skills --skill find-skills
```
"""
