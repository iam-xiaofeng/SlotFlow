"""API models for user-managed skills."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class SkillRecord(BaseModel):
    name: str
    description: str
    path: str
    enabled: bool = True
    protected: bool = False
    source: str = "user"


class SkillUpdateRequest(BaseModel):
    enabled: bool


class SkillInstallRequest(BaseModel):
    package_url: str = Field(
        default="https://github.com/vercel-labs/skills",
        min_length=1,
        max_length=2048,
    )
    skill_name: str = Field(min_length=1, max_length=128)

    @field_validator("package_url")
    @classmethod
    def package_url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("package_url must start with http:// or https://")
        return value

    @field_validator("skill_name")
    @classmethod
    def skill_name_must_be_portable(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            raise ValueError("skill_name must use letters, numbers, dots, underscores, or hyphens")
        return value
