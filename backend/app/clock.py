"""Shared UTC clock helper.

集中一处生成当前 UTC 时间，让所有记录的时间来源一致；测试需要固定时间时也只改这里。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
