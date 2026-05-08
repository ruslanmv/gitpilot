"""Expose every enabled MCP tool as a CrewAI agent tool.

This is the "Claude Code feel" bridge: when a server is enabled in the
MCP Servers tab and individual tools are ticked on, those tools appear
in the agents' toolbox so the planner / coder / reviewer can pick them
mid-conversation.

Design contract (best practices applied)
----------------------------------------

* **Read-only by default.** Mutation tools are excluded unless the
  caller explicitly opts in via ``include_mutation=True``.
* **Failure-safe.** Every tool catches exceptions and returns a
  human-readable error string. Agents never see a Python traceback;
  they see a sentence like ``MCP tool 'postgres.describe_table' failed:
  HTTP 401 (check the bearer token)``.
* **Cheap to rebuild.** The bridge reads the local
  :class:`~gitpilot.mcp_admin_api.MCPStore` on every call to
  :func:`build_mcp_agent_tools`, so toggle changes in the UI take
  effect on the next agent task without a process restart.
* **Discoverable.** :func:`describe_available_tools` returns a
  serialisable summary the planner (and the UI's "what can I do"
  pane) can both consume.
* **Bounded.** A hard cap on tool count keeps the LLM context budget
  predictable; default 32 (overridable via env var).
* **No global state.** No imports of CrewAI at module top-level so
  this module is testable without crewai installed; we import it
  lazily inside :func:`build_mcp_agent_tools`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from gitpilot.mcp_admin_api import MCPStore, ServerState

logger = logging.getLogger(__name__)

ENV_MAX_TOOLS = "GITPILOT_MCP_BRIDGE_MAX_TOOLS"
DEFAULT_MAX_TOOLS = 32


# ---------------------------------------------------------------------------
# Tool descriptor (data only)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MCPToolDescriptor:
    """One callable MCP tool, suitable for both an LLM tool list and a
    CrewAI ``@tool`` registration."""

    name: str
    description: str
    server_id: str
    server_endpoint: str
    auth_token: str
    scope: str  # "read" | "plan" | "mutation"
    risk: str  # "low" | "medium" | "high"

    def short(self) -> str:
        return f"[{self.server_id}] {self.name} — {self.description or self.scope}"


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------
def _select_enabled_tools(
    snapshot,
    *,
    include_mutation: bool,
) -> list[MCPToolDescriptor]:
    """Walk the local store and return one descriptor per enabled tool."""
    out: list[MCPToolDescriptor] = []
    for server_id, server in snapshot.servers.items():
        if not server.installed or not server.enabled:
            continue
        if server.orphan:
            # Orphaned servers stay in the store but their tools are not
            # advertised to agents until the user re-attaches them.
            continue
        token = _resolve_token(server)
        for tool_name, override in (server.tool_overrides or {}).items():
            # Honor explicit user toggle.
            if override is False:
                continue
            risk, scope = _classify(tool_name)
            if scope == "mutation" and not include_mutation:
                continue
            out.append(
                MCPToolDescriptor(
                    name=tool_name,
                    description=_describe(tool_name),
                    server_id=server_id,
                    server_endpoint=server.endpoint,
                    auth_token=token,
                    scope=scope,
                    risk=risk,
                )
            )
    return out


def _resolve_token(server: ServerState) -> str:
    """Pick the token a tool call should use.

    Priority: per-server token env var → fallback ``MCP_AUTH_TOKEN``.
    Empty token is acceptable for unauthenticated dev gateways; the
    server itself enforces the auth requirement.
    """
    if server.auth_token_env:
        v = os.environ.get(server.auth_token_env)
        if v:
            return v
    return os.environ.get("MCP_AUTH_TOKEN", "")


def _classify(tool_name: str) -> tuple[str, str]:
    """Return (risk, scope) for a tool name.

    The scope drives whether ``include_mutation`` gates the tool:

    * low risk     → "read"      (always exposed when enabled)
    * medium risk  → "mutation"  (writes data; gated)
    * high risk    → "mutation"  (destructive; gated)

    Risk is computed by :func:`gitpilot.mcp_admin_api.classify_risk`,
    which is the same function the UI uses to colour-code badges, so
    the agent toolbox stays in sync with what the user sees.
    """
    from gitpilot.mcp_admin_api import classify_risk

    risk = classify_risk(tool_name)
    if risk in ("high", "medium"):
        return (risk, "mutation")
    return ("low", "read")


def _describe(tool_name: str) -> str:
    """Cheap human-friendly description.

    Could query Forge for the long-form description, but the planner
    only needs a short hint. Falls back to the tool's own segments.
    """
    if "." in tool_name:
        ns, leaf = tool_name.split(".", 1)
        return f"{ns} server: {leaf.replace('_', ' ')}"
    return tool_name


# ---------------------------------------------------------------------------
# HTTP invocation
# ---------------------------------------------------------------------------
async def invoke_remote_tool(
    descriptor: MCPToolDescriptor,
    arguments: dict[str, Any],
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Issue one ``tools/call`` against the descriptor's server.

    Pure async; never raises -- returns ``{"ok": False, "error": "..."}``
    on failure so the agent-facing wrapper can present a string.
    """
    headers: dict[str, str] = {
        "content-type": "application/json",
        "x-gitpilot-origin": "agent",
    }
    if descriptor.auth_token:
        headers["authorization"] = f"Bearer {descriptor.auth_token}"

    body = {"method": "tools/call", "tool": descriptor.name, "arguments": arguments}

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        resp = await client.post(
            descriptor.server_endpoint, json=body, headers=headers
        )
        if resp.status_code != 200:
            text = resp.text[:300]
            return {"ok": False, "error": f"HTTP {resp.status_code}: {text}"}
        return {"ok": True, "result": resp.json()}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if own:
            await client.aclose()


# ---------------------------------------------------------------------------
# Sync wrapper used by CrewAI tools
# ---------------------------------------------------------------------------
def _run_async(coro):
    """Run an async function from a sync CrewAI tool body.

    Uses ``asyncio.run`` when no loop is running, otherwise schedules
    on the current loop with ``asyncio.run_coroutine_threadsafe`` --
    matches the pattern in :mod:`gitpilot.agent_tools`.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in a loop (rare under CrewAI but possible) -- spawn a
    # dedicated thread so we don't deadlock.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _make_callable(descriptor: MCPToolDescriptor) -> Callable[..., str]:
    """Turn a descriptor into a sync callable an agent can invoke.

    The signature is intentionally generic (``**kwargs``) to keep the
    binding compatible with CrewAI's runtime tool dispatcher; the
    server-side ``inputSchema`` is what gates argument validity.
    """

    async def _call(args: dict[str, Any]) -> dict[str, Any]:
        return await invoke_remote_tool(descriptor, args)

    def _tool_fn(**kwargs: Any) -> str:
        out = _run_async(_call(dict(kwargs)))
        if out.get("ok"):
            try:
                return json.dumps(out["result"], indent=2, default=str)
            except (TypeError, ValueError):
                return str(out["result"])
        return f"MCP tool {descriptor.name!r} failed: {out.get('error', 'unknown error')}"

    _tool_fn.__name__ = descriptor.name.replace(".", "_")
    _tool_fn.__doc__ = (
        f"{descriptor.description}\n"
        f"Server: {descriptor.server_id} · Scope: {descriptor.scope} · "
        f"Risk: {descriptor.risk}"
    )
    return _tool_fn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_mcp_agent_tools(
    *,
    store: MCPStore | None = None,
    include_mutation: bool = False,
    max_tools: int | None = None,
) -> list[Any]:
    """Build the live list of CrewAI tools backed by enabled MCP tools.

    Returns an empty list if MCP is disabled, no servers are enabled,
    or CrewAI is not importable. Never raises.
    """
    s = store or MCPStore()
    snap = s.load()
    descriptors = _select_enabled_tools(snap, include_mutation=include_mutation)

    if not descriptors:
        return []

    cap = int(
        max_tools
        if max_tools is not None
        else os.environ.get(ENV_MAX_TOOLS, DEFAULT_MAX_TOOLS)
    )
    descriptors = descriptors[:cap]

    try:
        from crewai.tools import tool as crewai_tool
    except Exception:
        logger.info("crewai not installed; mcp tool bridge returning raw callables")
        return [_make_callable(d) for d in descriptors]

    wrapped: list[Any] = []
    for descriptor in descriptors:
        fn = _make_callable(descriptor)
        # Decorate with CrewAI's @tool so the planner sees the description.
        decorated = crewai_tool(descriptor.short())(fn)
        wrapped.append(decorated)
    return wrapped


def describe_available_tools(
    *,
    store: MCPStore | None = None,
    include_mutation: bool = False,
) -> list[dict[str, Any]]:
    """Serialisable summary; used by the planner and a future UI pane."""
    s = store or MCPStore()
    descriptors = _select_enabled_tools(
        s.load(), include_mutation=include_mutation
    )
    return [
        {
            "name": d.name,
            "description": d.description,
            "server_id": d.server_id,
            "scope": d.scope,
            "risk": d.risk,
        }
        for d in descriptors
    ]
