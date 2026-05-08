"""Transport bridge for the GitPilot MCP server.

This module is loaded *only* when ``GITPILOT_EXPOSE_MCP_SERVER=true``.
It exposes a single :func:`mount` function that wires the public tool
catalog onto the existing FastAPI app at the configured mount path.

Two routes are added:

* ``GET  {mount_path}/healthz`` — unauthenticated liveness probe.
* ``POST {mount_path}`` — JSON envelope that emulates the
  ``tools/list`` and ``tools/call`` MCP methods. We do *not* attempt to
  re-implement the full MCP streamable-HTTP framing here; the JSON
  envelope is intentionally simple so the HomePilot wizard can drive it
  directly during the connection test, and a richer FastMCP transport
  can be layered on later (Phase 6 in the plan).

Keeping it additive
-------------------

* No existing route is modified.
* No existing model is renamed.
* All routes are scoped under the configurable ``mount_path``
  (default ``/mcp-server/mcp``); collision with existing routes is
  impossible in practice.
* The entire module is importable on demand only — operators who never
  set ``GITPILOT_EXPOSE_MCP_SERVER=true`` never load FastMCP, never
  construct the tool registry, and pay zero startup cost.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request

from .mcp_server import (
    AuthError,
    GitPilotMCPServer,
    MCPServerConfig,
    MCPServerError,
    RecursionGuardError,
    ScopeError,
)
from .mcp_server_tools import bind

logger = logging.getLogger(__name__)


def _build_router(server: GitPilotMCPServer, *, mount_path: str) -> APIRouter:
    router = APIRouter()

    @router.get(f"{mount_path}/healthz")
    async def healthz() -> dict[str, Any]:  # noqa: D401
        """Liveness probe used by the HomePilot wizard's step 1."""
        return {
            "status": "ok",
            "server": server.config.server_name,
            "tool_count": len(server.list_tools()),
        }

    @router.post(mount_path)
    async def mcp_endpoint(
        request: Request,
        authorization: str | None = Header(default=None),
        x_gitpilot_origin: str | None = Header(default=None, alias="x-gitpilot-origin"),
    ) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}")

        method = payload.get("method") or "tools/call"
        if method == "tools/list":
            # tools/list does not need auth: the server name + tool catalog
            # is public so the HomePilot wizard can show it during step 3.
            return {"tools": server.list_tools()}

        if method == "tools/call":
            tool_name = payload.get("tool") or payload.get("name")
            arguments = payload.get("arguments") or {}
            if not isinstance(tool_name, str) or not tool_name:
                raise HTTPException(status_code=422, detail="missing 'tool'")
            if not isinstance(arguments, dict):
                raise HTTPException(
                    status_code=422, detail="'arguments' must be an object"
                )
            try:
                result = await server.call_tool(
                    tool_name,
                    arguments,
                    bearer=authorization,
                    origin_header=x_gitpilot_origin,
                )
            except RecursionGuardError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc))
            except ScopeError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except MCPServerError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return {"success": True, **result}

        raise HTTPException(status_code=400, detail=f"unknown method: {method!r}")

    return router


def mount(app: FastAPI, config: MCPServerConfig) -> GitPilotMCPServer:
    """Mount the GitPilot MCP server onto an existing FastAPI app.

    Returns the configured :class:`GitPilotMCPServer` so callers can
    introspect the catalog (used in tests).
    """
    server = bind(GitPilotMCPServer(config))
    router = _build_router(server, mount_path=config.mount_path)
    app.include_router(router)
    logger.info(
        "gitpilot mcp server: %d tools mounted at %s",
        len(server.list_tools()),
        config.mount_path,
    )
    return server
