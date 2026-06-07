"""SlotFlow skill 数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Skill:
    """一个只读 skill 的元数据。"""

    name: str
    description: str
    skill_dir: Path
    skill_file: Path
    enabled: bool = True
