"""API models for MCP server management."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


McpServerSource = Literal["environment", "user"]


class McpServerRecord(BaseModel):
    name: str
    enabled: bool
    transport: str | None = None
    url: str | None = None
    source: McpServerSource
    protected: bool = False


class McpServerUpdateRequest(BaseModel):
    enabled: bool


class McpHttpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_must_be_portable(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
            raise ValueError("name must use letters, numbers, dots, underscores, or hyphens")
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value
