"""解析 SlotFlow `SKILL.md` 元数据。

第一版只支持本项目需要的极小 frontmatter 子集，避免为了只读 registry 立刻引入完整
YAML 依赖和安装/校验系统。
"""

from __future__ import annotations

from pathlib import Path

from app.harness.skills.types import Skill

SKILL_FILE_NAME = "SKILL.md"


def parse_skill_file(skill_file: Path) -> Skill | None:
    """从 `SKILL.md` 读取只读 skill 元数据。

    无效文件返回 `None`，这样 registry 扫描目录时可以跳过半成品 skill。
    """

    if skill_file.name != SKILL_FILE_NAME or not skill_file.is_file():
        return None

    content = skill_file.read_text(encoding="utf-8")
    frontmatter = extract_frontmatter(content)
    if frontmatter is None:
        return None

    fields = parse_frontmatter_fields(frontmatter)
    name = fields.get("name")
    description = fields.get("description")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None

    return Skill(
        name=name.strip(),
        description=description.strip(),
        skill_dir=skill_file.parent,
        skill_file=skill_file,
        enabled=True,
    )


def extract_frontmatter(content: str) -> str | None:
    """提取开头 `---` 包裹的 frontmatter。"""

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end_index is None:
        return None

    return "\n".join(lines[1:end_index])


def parse_frontmatter_fields(frontmatter: str) -> dict[str, object]:
    """解析当前支持的 frontmatter 字段。"""

    fields: dict[str, object] = {}
    lines = frontmatter.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()
        index += 1
        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()

        if raw_value:
            fields[key] = parse_scalar_or_inline_list(raw_value)
            continue

        items: list[str] = []
        while index < len(lines):
            child = lines[index]
            stripped = child.strip()
            if not stripped:
                index += 1
                continue
            if not child.startswith((" ", "\t")):
                break
            index += 1
            if stripped.startswith("- "):
                item = strip_quotes(stripped[2:].strip())
                if item:
                    items.append(item)
        fields[key] = items

    return fields


def parse_scalar_or_inline_list(value: str) -> str | list[str]:
    """解析简单标量或 `[]` / `[a, b]` 形式。"""

    value = strip_quotes(value)
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [strip_quotes(item.strip()) for item in body.split(",") if item.strip()]
    return value


def strip_quotes(value: str) -> str:
    """去掉一层简单引号。"""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
