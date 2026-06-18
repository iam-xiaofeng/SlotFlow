"""Shared accessors for objects stored on `app.state`.

路由层统一从这里读取启动阶段放进 `app.state` 的核心依赖（chat 仓库、agent
adapter、上传存储、runtime 配置、长期记忆），避免每个路由模块各写一份 getter。

只用 `TYPE_CHECKING` 引入返回类型，函数体只做属性读取/校验，因此不会在导入期
拉起重型依赖，也不会和路由模块形成循环引用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from app.chat.agent_adapter import AgentAdapter
    from app.chat.repository import ChatRepository
    from app.chat.runtime import SlotFlowRuntimeConfig
    from app.harness.memory import SlotFlowMemoryStore
    from app.uploads.storage import SlotFlowUploadStore


def get_chat_repo(request: Request) -> ChatRepository:
    """从 app.state 取 chat 仓库。"""

    return request.app.state.chat_repo


def get_agent_adapter(request: Request) -> AgentAdapter:
    """从 app.state 取 agent adapter。"""

    return request.app.state.agent_adapter


def get_upload_store(request: Request) -> SlotFlowUploadStore:
    """从 app.state 取上传文件存储边界。"""

    return request.app.state.upload_store


def get_runtime_config(request: Request) -> SlotFlowRuntimeConfig:
    """取 runtime 配置；未装配时返回 503。"""

    runtime_config = getattr(request.app.state, "runtime_config", None)
    if runtime_config is None:
        raise HTTPException(status_code=503, detail="runtime config is not configured")
    return runtime_config


def get_memory_store(request: Request) -> SlotFlowMemoryStore:
    """取长期记忆存储；未启用时返回 503。"""

    store = getattr(request.app.state, "memory_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="memory store is not configured")
    return store
