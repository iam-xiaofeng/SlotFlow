"""FastAPI routes for user-managed MCP servers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from app.chat.runtime import SlotFlowRuntimeConfig, refresh_runtime_mcp_config
from app.harness.mcp import (
    McpServerNotFoundError,
    ProtectedMcpServerError,
    SlotFlowMcpConfigStore,
    SlotFlowMcpServerConfig,
)
from app.mcp.models import McpHttpServerCreateRequest, McpServerRecord, McpServerUpdateRequest


router = APIRouter(prefix="/api/mcp/servers", tags=["MCP"])


@router.get("", response_model=list[McpServerRecord])
async def list_mcp_servers(request: Request) -> list[McpServerRecord]:
    """List environment and user-managed MCP servers."""

    runtime_config = get_runtime_config(request)
    refresh_runtime_mcp_config(runtime_config)
    store = get_mcp_config_store(request)
    user_names = {server.name for server in store.load_user_servers()} if store else set()
    return [
        mcp_server_to_record(server, source="user" if server.name in user_names else "environment")
        for server in runtime_config.mcp_config.servers
    ]


@router.post("", response_model=McpServerRecord)
async def create_http_mcp_server(
    body: McpHttpServerCreateRequest,
    request: Request,
) -> McpServerRecord:
    """Create or replace a user-managed streamable HTTP MCP server."""

    store = get_mcp_config_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="mcp config store is not configured")

    server = store.upsert_http_server(
        name=body.name,
        url=body.url,
        enabled=body.enabled,
    )
    refresh_runtime_mcp_config(get_runtime_config(request))
    return mcp_server_to_record(server, source="user")


@router.patch("/{server_name}", response_model=McpServerRecord)
async def update_mcp_server(
    server_name: str,
    body: McpServerUpdateRequest,
    request: Request,
) -> McpServerRecord:
    """Enable or disable one MCP server."""

    store = get_mcp_config_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="mcp config store is not configured")

    try:
        server = store.set_server_enabled(server_name, body.enabled)
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="mcp server not found") from exc

    refresh_runtime_mcp_config(get_runtime_config(request))
    source = "user" if any(item.name == server.name for item in store.load_user_servers()) else "environment"
    return mcp_server_to_record(server, source=source)


@router.delete("/{server_name}", status_code=204)
async def delete_mcp_server(server_name: str, request: Request) -> Response:
    """Delete a user-managed MCP server."""

    store = get_mcp_config_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="mcp config store is not configured")

    try:
        store.delete_user_server(server_name)
    except ProtectedMcpServerError as exc:
        raise HTTPException(status_code=403, detail="environment MCP server cannot be deleted") from exc
    except McpServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="mcp server not found") from exc

    refresh_runtime_mcp_config(get_runtime_config(request))
    return Response(status_code=204)


def get_runtime_config(request: Request) -> SlotFlowRuntimeConfig:
    runtime_config = getattr(request.app.state, "runtime_config", None)
    if runtime_config is None:
        raise HTTPException(status_code=503, detail="runtime config is not configured")
    return runtime_config


def get_mcp_config_store(request: Request) -> SlotFlowMcpConfigStore | None:
    runtime_config = get_runtime_config(request)
    return runtime_config.mcp_config_store


def mcp_server_to_record(
    server: SlotFlowMcpServerConfig,
    *,
    source: str,
) -> McpServerRecord:
    config = server.config or {}
    return McpServerRecord(
        name=server.name,
        enabled=server.enabled,
        transport=string_or_none(config.get("transport")),
        url=string_or_none(config.get("url")),
        source="user" if source == "user" else "environment",
        protected=source != "user",
    )


def string_or_none(value) -> str | None:
    return value if isinstance(value, str) else None
