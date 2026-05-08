"""GitPilot MCP server — additive, env-gated, read-only by default.

This module turns GitPilot itself into an MCP server so that other agents
(notably HomePilot's Coder persona) can invoke a curated, scoped subset
of GitPilot's functionality through the Model Context Protocol.

Design contract
---------------

* **Off by default.** The Starlette app in :mod:`gitpilot.api` only mounts
  this server when ``GITPILOT_EXPOSE_MCP_SERVER=true``. Existing
  deployments are unaffected.
* **No edits to existing routes.** This is a *facade* over the same
  high-level functions the FastAPI routes already use; the routes
  themselves stay untouched.
* **Curated tool surface.** We expose ten tools, six read-only and four
  mutation. Mutation tools require a separate scope token
  (``GITPILOT_MCP_SERVER_MUTATION_TOKEN``) AND
  ``GITPILOT_MCP_SERVER_ALLOW_MUTATION=true``.
* **Recursion guard.** If a tool call carries the
  ``X-Gitpilot-Origin: self`` header, we reject it. This stops a runaway
  loop where GitPilot's own agents reach back into the GitPilot MCP
  server and trigger themselves.
* **Stateless.** Per-call session id is generated and forwarded to
  GitPilot's existing event channel; the server itself keeps no state.

The companion ``register.json`` in ``extensions/mcp_plugins/gitpilot/``
mirrors what the HomePilot wizard expects.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENV_EXPOSE = "GITPILOT_EXPOSE_MCP_SERVER"
ENV_AUTH_TOKEN = "GITPILOT_MCP_SERVER_TOKEN"
ENV_MUTATION_TOKEN = "GITPILOT_MCP_SERVER_MUTATION_TOKEN"
ENV_ALLOW_MUTATION = "GITPILOT_MCP_SERVER_ALLOW_MUTATION"
ENV_MOUNT_PATH = "GITPILOT_MCP_SERVER_MOUNT_PATH"
ENV_SERVER_NAME = "GITPILOT_MCP_SERVER_NAME"

DEFAULT_MOUNT_PATH = "/mcp-server/mcp"
DEFAULT_SERVER_NAME = "gitpilot-mcp-server"

ORIGIN_HEADER = "x-gitpilot-origin"
ORIGIN_SELF_VALUE = "self"


def _bool_env(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class MCPServerConfig:
    """Runtime configuration for the GitPilot MCP server."""

    enabled: bool = False
    server_name: str = DEFAULT_SERVER_NAME
    mount_path: str = DEFAULT_MOUNT_PATH
    auth_token: str = ""
    mutation_token: str = ""
    allow_mutation: bool = False

    @classmethod
    def from_env(cls) -> "MCPServerConfig":
        return cls(
            enabled=_bool_env(ENV_EXPOSE, default=False),
            server_name=os.environ.get(ENV_SERVER_NAME, DEFAULT_SERVER_NAME),
            mount_path=os.environ.get(ENV_MOUNT_PATH, DEFAULT_MOUNT_PATH),
            auth_token=os.environ.get(ENV_AUTH_TOKEN, ""),
            mutation_token=os.environ.get(ENV_MUTATION_TOKEN, ""),
            allow_mutation=_bool_env(ENV_ALLOW_MUTATION, default=False),
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MCPServerError(Exception):
    """Base class for GitPilot MCP server errors."""


class AuthError(MCPServerError):
    """Raised when the bearer token is missing/invalid for the requested scope."""


class ScopeError(MCPServerError):
    """Raised when a mutation tool is called without mutation scope."""


class RecursionGuardError(MCPServerError):
    """Raised when a tool call carries ``X-Gitpilot-Origin: self``."""


# ---------------------------------------------------------------------------
# Tool registry (data-only; the actual implementations live in
# mcp_server_tools.py to keep this module easy to read)
# ---------------------------------------------------------------------------
SCOPE_READ = "read"
SCOPE_PLAN = "plan"
SCOPE_MUTATION = "mutation"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    scope: str
    input_schema: dict[str, Any]
    handler_name: str  # attribute on mcp_server_tools.Tools


TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="gitpilot.healthz",
        description="Liveness probe; returns version + capabilities.",
        scope=SCOPE_READ,
        input_schema={"type": "object", "properties": {}},
        handler_name="healthz",
    ),
    ToolSpec(
        name="gitpilot.list_repos",
        description="List repositories visible to the authenticated GitPilot user.",
        scope=SCOPE_READ,
        input_schema={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "per_page": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 30,
                },
                "query": {"type": "string"},
            },
        },
        handler_name="list_repos",
    ),
    ToolSpec(
        name="gitpilot.list_branches",
        description="List branches in a repository.",
        scope=SCOPE_READ,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
        handler_name="list_branches",
    ),
    ToolSpec(
        name="gitpilot.describe_repo",
        description="Return high-level repo metadata: tree summary, default branch, recent commits.",
        scope=SCOPE_READ,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "max_files": {"type": "integer", "default": 50, "maximum": 500},
            },
            "required": ["owner", "repo"],
        },
        handler_name="describe_repo",
    ),
    ToolSpec(
        name="gitpilot.list_skills",
        description="List installed GitPilot skills with their slugs and descriptions.",
        scope=SCOPE_READ,
        input_schema={"type": "object", "properties": {}},
        handler_name="list_skills",
    ),
    ToolSpec(
        name="gitpilot.classify_topology",
        description="Classify a free-text request into a GitPilot topology id.",
        scope=SCOPE_READ,
        input_schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        handler_name="classify_topology",
    ),
    ToolSpec(
        name="gitpilot.plan",
        description=(
            "Produce a multi-step plan for a code-generation request. Read-only: "
            "no files are written and no PRs are opened."
        ),
        scope=SCOPE_PLAN,
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "workspace_root": {"type": "string"},
            },
            "required": ["prompt"],
        },
        handler_name="plan",
    ),
    ToolSpec(
        name="gitpilot.execute",
        description=(
            "Execute a previously-returned plan. Streaming. Honours the same scope "
            "checks as the equivalent /api/chat/execute route."
        ),
        scope=SCOPE_PLAN,
        input_schema={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "approval_token": {"type": "string"},
            },
            "required": ["plan_id"],
        },
        handler_name="execute",
    ),
    ToolSpec(
        name="gitpilot.run_skill",
        description="Invoke a named GitPilot skill. Mutation scope required.",
        scope=SCOPE_MUTATION,
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["slug"],
        },
        handler_name="run_skill",
    ),
    ToolSpec(
        name="gitpilot.create_pr",
        description=(
            "Open a pull request from a branch produced by a previous execute step. "
            "Mutation scope required."
        ),
        scope=SCOPE_MUTATION,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "head": {"type": "string"},
                "base": {"type": "string", "default": "main"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["owner", "repo", "head", "title"],
        },
        handler_name="create_pr",
    ),
)


# ---------------------------------------------------------------------------
# Auth + scope enforcement
# ---------------------------------------------------------------------------
def authorize(
    config: MCPServerConfig,
    *,
    bearer: str | None,
    origin_header: str | None,
    scope: str,
) -> None:
    """Validate the request before dispatching to a tool handler.

    Raises one of :class:`AuthError`, :class:`ScopeError`,
    :class:`RecursionGuardError`. Returning normally means the call may
    proceed.
    """
    if origin_header and origin_header.strip().lower() == ORIGIN_SELF_VALUE:
        raise RecursionGuardError(
            "Refusing self-call: X-Gitpilot-Origin: self detected"
        )

    if not config.auth_token:
        raise AuthError("Server has no auth token configured")
    if not bearer or not bearer.startswith("Bearer "):
        raise AuthError("Missing bearer token")
    presented = bearer.removeprefix("Bearer ").strip()
    if presented != config.auth_token:
        raise AuthError("Invalid bearer token")

    if scope == SCOPE_MUTATION:
        if not config.allow_mutation:
            raise ScopeError(
                "Mutation tools disabled (set GITPILOT_MCP_SERVER_ALLOW_MUTATION=true)"
            )
        if not config.mutation_token or presented != config.mutation_token:
            raise ScopeError(
                "Mutation tools require GITPILOT_MCP_SERVER_MUTATION_TOKEN"
            )


# ---------------------------------------------------------------------------
# Per-call envelope
# ---------------------------------------------------------------------------
@dataclass
class CallContext:
    call_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: float = field(default_factory=time.monotonic)
    tool_name: str = ""
    scope: str = SCOPE_READ

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


# ---------------------------------------------------------------------------
# Dispatch surface (sync + async friendly)
# ---------------------------------------------------------------------------
ToolHandler = Callable[[dict[str, Any], CallContext], Awaitable[dict[str, Any]]]


class GitPilotMCPServer:
    """Tool catalog + auth gate. Transport-agnostic.

    The actual MCP transport (FastMCP / streamable-http) is wired in
    :mod:`gitpilot.api` only when the server is enabled. This class is
    safe to import unconditionally: it does not touch the network or
    GitPilot internals at construction time.
    """

    def __init__(
        self,
        config: MCPServerConfig | None = None,
        *,
        handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        self.config = config or MCPServerConfig.from_env()
        self._handlers: dict[str, ToolHandler] = handlers or {}

    def register_handler(self, name: str, handler: ToolHandler) -> None:
        self._handlers[name] = handler

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the public tool catalog as MCP tool definitions."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "scope": t.scope,
                "inputSchema": t.input_schema,
            }
            for t in TOOL_CATALOG
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        bearer: str | None,
        origin_header: str | None,
    ) -> dict[str, Any]:
        spec = next((t for t in TOOL_CATALOG if t.name == name), None)
        if spec is None:
            raise MCPServerError(f"Unknown tool: {name!r}")

        authorize(
            self.config,
            bearer=bearer,
            origin_header=origin_header,
            scope=spec.scope,
        )

        handler = self._handlers.get(name)
        if handler is None:
            raise MCPServerError(
                f"No handler registered for {name!r}; did mcp_server_tools.bind run?"
            )

        ctx = CallContext(tool_name=name, scope=spec.scope)
        try:
            result = await asyncio.wait_for(handler(arguments, ctx), timeout=120)
        except asyncio.TimeoutError as exc:  # noqa: PERF203
            logger.warning("mcp tool %s timed out after 120s", name)
            raise MCPServerError("Tool call timed out") from exc

        return {
            "tool": name,
            "scope": spec.scope,
            "call_id": ctx.call_id,
            "elapsed_ms": ctx.elapsed_ms(),
            "result": result,
        }
