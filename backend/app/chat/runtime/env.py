"""Small environment-variable loaders for the SlotFlow runtime config.

集中放置 runtime 配置用到的环境变量解析小工具，保持纯函数、无副作用，方便单测。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_bool_from_env(name: str, *, default: bool) -> bool:
    """Read a small boolean environment flag."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag, got {value!r}")


def load_optional_path_from_env(name: str) -> Path | None:
    """从环境变量读取可选路径。"""

    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def load_path_from_env(name: str, *, default: Path) -> Path:
    """从环境变量读取路径，不存在时返回默认值。"""

    value = os.environ.get(name)
    if not value or not value.strip():
        return default
    return Path(value.strip())


def load_optional_text_from_env(name: str) -> str | None:
    """从环境变量读取非空字符串。"""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def load_optional_csv_set_from_env(name: str) -> set[str] | None:
    """从环境变量读取逗号分隔名单。"""

    value = os.environ.get(name)
    if not value:
        return None
    names = {item.strip() for item in value.split(",") if item.strip()}
    return names or None


def load_optional_csv_list_from_env(name: str) -> list[str] | None:
    """Read a comma-separated list while preserving item order."""

    value = os.environ.get(name)
    if not value:
        return None
    names = [item.strip() for item in value.split(",") if item.strip()]
    return names or None


def load_positive_int_from_env(name: str, *, default: int) -> int:
    """Read a positive integer environment value."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return parsed
