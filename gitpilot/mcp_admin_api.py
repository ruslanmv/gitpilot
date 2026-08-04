"""MCP Context Forge admin API.

Powers the Settings → "MCP Servers" tab. Lets the user list, install,
uninstall, enable/disable and test attached MCP servers, and toggle
individual tools on a per-server basis.

State is persisted to ``~/.gitpilot/mcp_servers.json`` so it survives
restarts. The shape is intentionally small:

.. code-block:: json

    {
      "version": 1,
      "gateway_url": "http://localhost:4444/mcp",
      "servers": {
        "mcp-postgre-server": {
          "installed": true,
          "enabled": true,
          "endpoint": "http://mcp-postgre-server:8080/mcp",
          "auth_token_env": "MCP_POSTGRE_SERVER_TOKEN",
          "tags": ["postgresql", "database"],
          "tool_overrides": {"postgres.execute_write": false}
        }
      }
    }

Catalog data lives in
``gitpilot/extensions/mcp_plugins/<id>/register.json`` so a release
artefact ships with the curated set.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gitpilot.mcp_plugin import (
    KNOWN_SERVERS,
    ForgeClientError,
    MCPContextForgeClient,
    PolicyEngine,
    PolicyViolation,
    get_settings,
)

logger = logging.getLogger(__name__)

STATE_VERSION = 1
STATE_FILE_ENV = "GITPILOT_MCP_STATE_FILE"
DEFAULT_STATE_FILE = Path.home() / ".gitpilot" / "mcp_servers.json"

# Per-tool risk classification used by the UI to colour the badges.
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"

DESTRUCTIVE_KEYWORDS = ("drop", "delete", "truncate", "remove", "destroy")
MUTATION_KEYWORDS = ("insert", "update", "upsert", "create", "alter", "execute_write")

# Which agents call which tool, used by the UI's "Used by" chip.
AGENT_USES_TOOL: dict[str, tuple[str, ...]] = {
    "postgres.list_databases": ("explorer",),
    "postgres.list_schemas": ("explorer",),
    "postgres.list_tables": ("explorer",),
    "postgres.describe_table": ("coder", "test_runner", "reviewer"),
    "postgres.safe_select": ("coder",),
    "postgres.explain_query": ("reviewer",),
    "postgres.validate_migration": ("reviewer",),
    "postgres.generate_test_fixtures": ("test_runner",),
    "postgres.generate_repository_context": ("coder",),
    "milvus.list_collections": ("explorer",),
    "milvus.describe_collection": ("coder", "test_runner"),
    "milvus.describe_index": ("reviewer",),
    "milvus.search": ("coder",),
    "milvus.hybrid_search": ("coder",),
    "milvus.validate_index_config": ("reviewer",),
    "milvus.generate_ingestion_code": ("coder",),
    "milvus.generate_rag_pipeline_context": ("coder",),
    "milvus.generate_test_vectors": ("test_runner",),
    "inspector.ping_server": ("reviewer",),
    "inspector.list_capabilities": ("explorer",),
    "inspector.validate_tool_schema": ("reviewer",),
    "inspector.run_contract_tests": ("reviewer",),
    "inspector.batch_validate": ("reviewer",),
    "inspector.generate_report": ("reviewer",),
    "inspector.list_logs": ("reviewer",),
}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    override = os.environ.get(STATE_FILE_ENV)
    return Path(override) if override else DEFAULT_STATE_FILE


@dataclass
class ServerState:
    id: str
    installed: bool = True
    enabled: bool = True
    endpoint: str = ""
    auth_token_env: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    tool_overrides: dict[str, bool] = field(default_factory=dict)
    # Sync metadata. Both fields are additive: pre-existing on-disk states
    # without these keys deserialise with the safe defaults.
    orphan: bool = False
    source: str = "manual"  # "manual" | "forge-sync" | "user" | "wizard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "auth_token_env": self.auth_token_env,
            "description": self.description,
            "tags": list(self.tags),
            "tool_overrides": dict(self.tool_overrides),
            "orphan": self.orphan,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, server_id: str, blob: dict[str, Any]) -> "ServerState":
        return cls(
            id=server_id,
            installed=bool(blob.get("installed", True)),
            enabled=bool(blob.get("enabled", True)),
            endpoint=str(blob.get("endpoint", "")),
            auth_token_env=str(blob.get("auth_token_env", "")),
            description=str(blob.get("description", "")),
            tags=list(blob.get("tags", []) or []),
            tool_overrides=dict(blob.get("tool_overrides", {}) or {}),
            orphan=bool(blob.get("orphan", False)),
            source=str(blob.get("source", "manual") or "manual"),
        )


@dataclass
class StoreSnapshot:
    gateway_url: str
    servers: dict[str, ServerState]


class MCPStore:
    """File-backed store with atomic writes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _state_path()

    def load(self) -> StoreSnapshot:
        if not self.path.exists():
            return self._seed_default()
        try:
            with self.path.open() as fh:
                blob = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("invalid MCP state file %s, reseeding (%s)", self.path, exc)
            return self._seed_default()

        servers = {
            sid: ServerState.from_dict(sid, payload)
            for sid, payload in (blob.get("servers") or {}).items()
        }
        return StoreSnapshot(
            gateway_url=str(
                blob.get("gateway_url") or get_settings().gateway_url
            ),
            servers=servers,
        )

    def save(self, snapshot: StoreSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": STATE_VERSION,
            "gateway_url": snapshot.gateway_url,
            "servers": {sid: s.to_dict() for sid, s in snapshot.servers.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w") as fh:
            json.dump(body, fh, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def _seed_default(self) -> StoreSnapshot:
        """Seed with the three known servers, all installed but disabled.

        We do not auto-enable on first boot: the user must opt in from the
        UI so they see the consent prompt and configure auth tokens before
        any external tool is callable.
        """
        servers: dict[str, ServerState] = {}
        for sid, entry in KNOWN_SERVERS.items():
            servers[sid] = ServerState(
                id=sid,
                installed=True,
                enabled=False,
                endpoint=_default_endpoint(sid),
                auth_token_env=_default_token_env(sid),
                description=entry.description,
                tags=list(entry.tags),
            )
        snapshot = StoreSnapshot(
            gateway_url=get_settings().gateway_url, servers=servers
        )
        self.save(snapshot)
        return snapshot


def _default_endpoint(server_id: str) -> str:
    port = {"mcp-postgre-server": 8080, "mcp-milvus-server": 8082, "mcp-inspector-server": 8081}
    return f"http://{server_id}:{port.get(server_id, 8080)}/mcp"


def _default_token_env(server_id: str) -> str:
    return server_id.upper().replace("-", "_") + "_TOKEN"


# ---------------------------------------------------------------------------
# Catalog (read-only, packaged with GitPilot)
# ---------------------------------------------------------------------------
def _catalog_dir() -> Path:
    # gitpilot/mcp_admin_api.py  ->  gitpilot/  ->  <repo root>
    here = Path(__file__).resolve().parent.parent
    return here / "extensions" / "mcp_plugins"


def load_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    base = _catalog_dir()
    if not base.exists():
        return items
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "register.json"
        if not manifest.exists():
            continue
        try:
            with manifest.open() as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": payload.get("name") or child.name,
                "slug": child.name,
                "description": payload.get("description", ""),
                "tags": payload.get("tags", []),
                "endpoint": payload.get("endpoint", ""),
                "auth": payload.get("auth", {}),
                "metadata": payload.get("metadata", {}),
                "tool_policy": payload.get("tool_policy", {}),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Risk + tool aggregation
# ---------------------------------------------------------------------------
def classify_risk(tool_name: str) -> str:
    lower = tool_name.lower()
    if any(kw in lower for kw in DESTRUCTIVE_KEYWORDS):
        return RISK_HIGH
    if any(kw in lower for kw in MUTATION_KEYWORDS):
        return RISK_MEDIUM
    return RISK_LOW


def _expected_tools(server_id: str) -> Iterable[str]:
    entry = KNOWN_SERVERS.get(server_id)
    return entry.expected_tools if entry else ()


def _tool_payload(server_id: str, tool_name: str, override: bool | None) -> dict[str, Any]:
    risk = classify_risk(tool_name)
    return {
        "name": tool_name,
        "risk": risk,
        "enabled_default": risk != RISK_HIGH,
        "enabled": override if override is not None else risk != RISK_HIGH,
        "used_by": list(AGENT_USES_TOOL.get(tool_name, ())),
        "destructive": risk == RISK_HIGH,
        "mutation": risk == RISK_MEDIUM,
    }


# ---------------------------------------------------------------------------
# Live status (tries to talk to the forge; degrades gracefully)
# ---------------------------------------------------------------------------
def gateway_base_url(gateway_url: str) -> str:
    """Return the gateway origin without the ``/mcp`` transport suffix.

    The settings string ``gateway_url`` is the canonical address for MCP
    JSON-RPC traffic (always ends in ``/mcp``), but admin endpoints --
    ``/health``, ``/gateways``, ``/admin/...`` -- live at the origin.
    Strip the suffix once, here, so callers always have a clean base.
    """
    cleaned = gateway_url.rstrip("/")
    if cleaned.endswith("/mcp"):
        cleaned = cleaned[: -len("/mcp")]
    return cleaned


async def gateway_status(gateway_url: str, auth_token: str) -> dict[str, Any]:
    base = gateway_base_url(gateway_url)
    try:
        async with httpx.AsyncClient(
            timeout=3.0,
            headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {},
        ) as http:
            resp = await http.get(f"{base}/health")
            if resp.status_code == 200:
                # Try to surface the gateway's reported version + tool count
                # if it returns JSON; fall back to the raw text otherwise.
                try:
                    return {"reachable": True, "detail": resp.json()}
                except ValueError:
                    return {"reachable": True, "detail": {"status": resp.text[:120]}}
            return {"reachable": False, "detail": f"status={resp.status_code}"}
    except httpx.HTTPError as exc:
        return {"reachable": False, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------
class InstallRequest(BaseModel):
    server_id: str = Field(..., description="catalog slug or full server id")
    endpoint: str | None = None
    auth_token_env: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class CustomInstallRequest(BaseModel):
    register_json: dict[str, Any] = Field(
        ..., description="A Context Forge register.json document."
    )


class ToolToggleRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/mcp", tags=["mcp-admin"])


def _store() -> MCPStore:
    return MCPStore()


@router.get("/status")
async def get_status() -> dict[str, Any]:
    store = _store()
    snapshot = store.load()
    settings = get_settings()
    gateway = await gateway_status(snapshot.gateway_url, settings.auth_token)

    installed = sum(1 for s in snapshot.servers.values() if s.installed)
    enabled = sum(1 for s in snapshot.servers.values() if s.installed and s.enabled)
    expected_tool_count = sum(
        len(list(_expected_tools(sid)))
        for sid, s in snapshot.servers.items()
        if s.installed and s.enabled
    )

    return {
        "gateway_url": snapshot.gateway_url,
        "gateway_reachable": gateway["reachable"],
        "gateway_detail": gateway["detail"],
        "plugin_enabled": settings.enabled,
        "servers_installed": installed,
        "servers_enabled": enabled,
        "tools_advertised": expected_tool_count,
    }


@router.get("/servers")
async def list_servers() -> dict[str, Any]:
    snapshot = _store().load()
    out: list[dict[str, Any]] = []
    for sid, s in snapshot.servers.items():
        tools = [
            _tool_payload(sid, name, s.tool_overrides.get(name))
            for name in _expected_tools(sid)
        ]
        out.append(
            {
                "id": sid,
                "installed": s.installed,
                "enabled": s.enabled,
                "endpoint": s.endpoint,
                "auth_token_env": s.auth_token_env,
                "description": s.description,
                "tags": s.tags,
                "tool_count": len(tools),
                "tools": tools,
                "is_known": sid in KNOWN_SERVERS,
                "orphan": s.orphan,
                "source": s.source,
            }
        )
    out.sort(key=lambda x: x["id"])
    return {"servers": out}


@router.get("/catalog")
async def list_catalog() -> dict[str, Any]:
    catalog = load_catalog()
    snapshot = _store().load()
    for item in catalog:
        item["installed"] = item["id"] in snapshot.servers and snapshot.servers[
            item["id"]
        ].installed
    return {"items": catalog}


@router.post("/servers/install")
async def install_server(req: InstallRequest) -> dict[str, Any]:
    catalog = {item["id"]: item for item in load_catalog()}
    base = catalog.get(req.server_id)
    if base is None and req.endpoint is None:
        raise HTTPException(
            status_code=404,
            detail=f"server {req.server_id!r} not in catalog (provide endpoint to install custom)",
        )

    snapshot = _store().load()
    server = ServerState(
        id=req.server_id,
        installed=True,
        enabled=False,
        endpoint=req.endpoint or (base["endpoint"] if base else ""),
        auth_token_env=req.auth_token_env
        or (base.get("auth", {}).get("env", "") if base else ""),
        description=req.description or (base["description"] if base else ""),
        tags=list(req.tags or (base["tags"] if base else [])),
    )
    snapshot.servers[req.server_id] = server
    _store().save(snapshot)
    return {"ok": True, "server": server.to_dict() | {"id": server.id}}


@router.post("/servers/install-custom")
async def install_custom(req: CustomInstallRequest) -> dict[str, Any]:
    rj = req.register_json
    server_id = str(rj.get("name") or "")
    endpoint = str(rj.get("endpoint") or "")
    if not server_id or not endpoint:
        raise HTTPException(
            status_code=422, detail="register_json must include 'name' and 'endpoint'"
        )

    snapshot = _store().load()
    snapshot.servers[server_id] = ServerState(
        id=server_id,
        installed=True,
        enabled=False,
        endpoint=endpoint,
        auth_token_env=str(rj.get("auth", {}).get("env", "")),
        description=str(rj.get("description", "")),
        tags=list(rj.get("tags", []) or []),
    )
    _store().save(snapshot)
    return {"ok": True, "server": snapshot.servers[server_id].to_dict() | {"id": server_id}}


@router.post("/servers/{server_id}/uninstall")
async def uninstall_server(server_id: str) -> dict[str, Any]:
    store = _store()
    snapshot = store.load()
    if server_id not in snapshot.servers:
        raise HTTPException(status_code=404, detail=f"server {server_id!r} not installed")
    snapshot.servers.pop(server_id)
    store.save(snapshot)
    return {"ok": True}


@router.post("/servers/{server_id}/enable")
async def enable_server(server_id: str) -> dict[str, Any]:
    return _toggle_server(server_id, enabled=True)


@router.post("/servers/{server_id}/disable")
async def disable_server(server_id: str) -> dict[str, Any]:
    return _toggle_server(server_id, enabled=False)


def _toggle_server(server_id: str, *, enabled: bool) -> dict[str, Any]:
    store = _store()
    snapshot = store.load()
    if server_id not in snapshot.servers:
        raise HTTPException(status_code=404, detail=f"server {server_id!r} not installed")
    snapshot.servers[server_id].enabled = enabled
    store.save(snapshot)
    return {"ok": True, "enabled": enabled}


@router.post("/servers/{server_id}/tools/{tool_name}/toggle")
async def toggle_tool(server_id: str, tool_name: str, req: ToolToggleRequest) -> dict[str, Any]:
    store = _store()
    snapshot = store.load()
    if server_id not in snapshot.servers:
        raise HTTPException(status_code=404, detail=f"server {server_id!r} not installed")

    # Block client-side policy violations *before* persisting an enable.
    if req.enabled and PolicyEngine().is_allowed(tool_name, {})[0] is False:
        raise HTTPException(
            status_code=409,
            detail=f"tool {tool_name!r} blocked by policy; cannot be enabled",
        )

    snapshot.servers[server_id].tool_overrides[tool_name] = req.enabled
    store.save(snapshot)
    return {"ok": True, "tool": tool_name, "enabled": req.enabled}


# ---------------------------------------------------------------------------
# Two-network-context endpoint model
# ---------------------------------------------------------------------------
# Forge stores server endpoints as docker-network URLs (e.g.
# "http://mcp-postgre-server:8080/mcp") because that is the URL FORGE
# uses to call back into the federated server. The GitPilot backend,
# however, runs on the WSL/macOS/Linux *host* — it cannot resolve docker
# service names. So the same canonical endpoint maps to a different
# *probe* URL depending on which side of the network seam you call from.
#
# Single source of truth for the host-side ports: docker-compose.mcp.yml
# publishes them via env vars in .mcp.env (MCP_FORGE_PORT etc.). We
# read those same env vars here so there is no place the two views can
# drift.
#
# This mirrors the k8s ClusterIP / NodePort split, Consul's
# `service.address` vs `service.taggedAddresses`, and Linkerd's
# in-cluster-vs-ingress URL distinction.

# Compose service-name → (env var with published host port, default port).
DOCKER_SERVICE_TO_HOST_PORT: dict[str, tuple[str, int]] = {
    "mcp-context-forge":    ("MCP_FORGE_PORT",     4444),
    "mcp-postgre-server":   ("MCP_POSTGRE_PORT",   8080),
    "mcp-milvus-server":    ("MCP_MILVUS_PORT",    8082),
    "mcp-inspector-server": ("MCP_INSPECTOR_PORT", 8081),
}


def to_host_url(in_network_url: str) -> str:
    """Translate a docker-network URL to its host-reachable form.

    ``http://mcp-postgre-server:8080/mcp`` → ``http://localhost:8080/mcp``

    Idempotent: a URL whose host is already ``localhost``, ``127.0.0.1``,
    or any name not in :data:`DOCKER_SERVICE_TO_HOST_PORT` is returned
    unchanged. This keeps user-added custom servers (which point at
    real DNS / IP) untouched.

    Defensive: a malformed URL falls through unchanged so probes can
    surface the original failure rather than a translation crash.
    """
    try:
        parsed = urlparse(in_network_url)
    except (TypeError, ValueError):
        return in_network_url
    host = parsed.hostname
    if host is None or host not in DOCKER_SERVICE_TO_HOST_PORT:
        return in_network_url
    env_var, default_port = DOCKER_SERVICE_TO_HOST_PORT[host]
    try:
        port = int(os.environ.get(env_var, str(default_port)))
    except (TypeError, ValueError):
        port = default_port
    new_netloc = f"localhost:{port}"
    return parsed._replace(netloc=new_netloc).geturl()


async def _direct_health_probe(endpoint: str, timeout: float = 3.0) -> dict[str, Any]:
    """Hit ``<endpoint base>/health`` directly, bypassing Forge.

    The MCP server endpoints stored locally end in ``/mcp`` (the JSON-RPC
    transport path). The container's liveness probe lives at ``/health``
    on the same host:port. We strip the trailing ``/mcp`` and probe.

    The endpoint is first translated through :func:`to_host_url` so that
    docker-network service names (which the GitPilot backend running on
    the host cannot resolve) become ``localhost:<published-port>``.

    Best practice: per-server diagnostics talk to the server itself,
    *not* through a federation gateway. Gateway proxying is a separate
    failure surface and a separate "Test Forge" concern.
    """
    # Translate docker-network URLs to host-reachable URLs.
    endpoint = to_host_url(endpoint)
    base = endpoint.rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    url = f"{base}/health"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.get(url)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code == 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = {"raw": resp.text[:120]}
            return {
                "ok": True,
                "status_code": 200,
                "response_time_ms": elapsed_ms,
                "detail": detail,
                "probed": url,
            }
        return {
            "ok": False,
            "status_code": resp.status_code,
            "response_time_ms": elapsed_ms,
            "reason": f"HTTP {resp.status_code}",
            "probed": url,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "response_time_ms": int((time.monotonic() - started) * 1000),
            "reason": str(exc),
            "probed": url,
        }


@router.post("/servers/{server_id}/test")
async def test_server(server_id: str) -> dict[str, Any]:
    """Direct health probe of the MCP server, bypassing Forge.

    Forge proxying is a separate failure mode (auth, federation
    config, network) that we don't want to conflate with "is this
    server alive". The card's Test button answers the simpler, more
    actionable question first; "is Forge proxying it" is surfaced by
    the gateway dot in the header and by the Sync flow.
    """
    snapshot = _store().load()
    server = snapshot.servers.get(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not installed")

    return await _direct_health_probe(server.endpoint)


# ---------------------------------------------------------------------------
# Sync (pull-from-Forge)
# ---------------------------------------------------------------------------
# Last successful SyncReport, kept in process memory so the UI can
# answer GET /api/mcp/sync/status without re-running the reconcile.
_LAST_SYNC: dict[str, Any] | None = None


class SyncRequest(BaseModel):
    """Optional overrides for one-off syncs (CI, ad-hoc CLI)."""

    forge_url: str | None = Field(
        default=None,
        description="Override the configured Forge URL for this call only.",
    )
    list_path_override: str = Field(
        default="",
        description="Override Forge's list endpoint (default: try a few common paths).",
    )


@router.post("/sync")
async def trigger_sync(req: SyncRequest | None = None) -> dict[str, Any]:
    """Pull the Forge registry and reconcile against the local store.

    Idempotent: re-running produces the same SyncReport for the same
    Forge state. Never raises; transport failures surface as
    ``forge_unreachable=true``.
    """
    global _LAST_SYNC
    # Local import to keep mcp_admin_api importable when mcp_forge_sync
    # would otherwise pull httpx in at startup.
    from .mcp_forge_sync import sync as run_sync

    body = req or SyncRequest()
    report = await run_sync(
        store=_store(),
        forge_url=body.forge_url,
        list_path_override=body.list_path_override,
    )
    _LAST_SYNC = report.to_dict()
    return _LAST_SYNC


@router.get("/agent_tools")
async def agent_tools(include_mutation: bool = False) -> dict[str, Any]:
    """Return the toolbox the GitPilot agents currently see.

    Reads from the same store that the MCP Servers tab writes to, so the
    UI's "what can I use right now" view stays in sync with what the
    planner / coder / reviewer get when they're invoked.
    """
    from .mcp_tools_bridge import describe_available_tools

    tools = describe_available_tools(include_mutation=include_mutation)
    return {"count": len(tools), "tools": tools}


@router.get("/sync/status")
async def sync_status() -> dict[str, Any]:
    """Return the last sync's report (or a placeholder if never synced)."""
    if _LAST_SYNC is None:
        return {
            "ran": False,
            "added": [],
            "kept": [],
            "orphaned": [],
            "forge_unreachable": False,
        }
    return {"ran": True, **_LAST_SYNC}


@router.post("/servers/{server_id}/forget")
async def forget_orphan(server_id: str) -> dict[str, Any]:
    """Remove a server that's been flagged orphan.

    The "Forget" UI action. Refuses to forget non-orphan servers so the
    user can never accidentally delete an actively-syncing entry.
    """
    store = _store()
    snapshot = store.load()
    server = snapshot.servers.get(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not installed")
    if not server.orphan:
        raise HTTPException(
            status_code=409,
            detail="server is not flagged orphan; use the Uninstall action instead",
        )
    snapshot.servers.pop(server_id)
    store.save(snapshot)
    return {"ok": True, "forgotten": server_id}


# ---------------------------------------------------------------------------
# Gateway connection (which Forge, and how we authenticate with it)
# ---------------------------------------------------------------------------
# Pointing GitPilot at a different Context Forge -- a colleague's, a staging
# one, or the one HomePilot already started -- used to mean editing environment
# variables and restarting. These three endpoints make it a setting, and are
# the single authority behind every surface: the desktop UI, the mobile UI and
# the VS Code extension all read and write here.
#
# Secrets are write-only by design: reads report whether a credential exists,
# never what it is.


class GatewayConfigBody(BaseModel):
    """A partial edit of the gateway connection.

    Every field is optional and ``None`` means "not submitted". A settings form
    always posts a blank password box, and that must never erase a stored
    password -- so clearing is an explicit flag rather than an empty string.
    """

    url: str | None = Field(default=None, description="Base URL of the Context Forge gateway.")
    authMode: str | None = Field(default=None, description="auto | none | token | login")
    email: str | None = Field(default=None, description="Admin email used to sign in.")
    password: str | None = Field(default=None, description="Admin password. Stored, never returned.")
    apiToken: str | None = Field(default=None, description="Forge API token. Stored, never returned.")
    label: str | None = Field(default=None, description="A name for this gateway, for humans.")
    clearPassword: bool = Field(default=False, description="Explicitly remove the stored password.")
    clearApiToken: bool = Field(default=False, description="Explicitly remove the stored API token.")


@router.get("/gateway")
async def get_gateway_config() -> dict[str, Any]:
    """The current connection, with every secret redacted to a boolean."""
    from .mcp_gateway_config import load_profile

    return load_profile().public_dict()


@router.put("/gateway")
async def put_gateway_config(body: GatewayConfigBody) -> dict[str, Any]:
    """Save the connection. Returns the same redacted view a read returns."""
    from .mcp_gateway_config import GatewayUpdate, apply_update, load_profile, save_profile

    try:
        updated = apply_update(
            load_profile(),
            GatewayUpdate(
                url=body.url,
                auth_mode=body.authMode,
                email=body.email,
                password=body.password,
                api_token=body.apiToken,
                label=body.label,
                clear_password=body.clearPassword,
                clear_api_token=body.clearApiToken,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_profile(updated)
    return updated.public_dict()


@router.post("/gateway/test")
async def test_gateway_config(body: GatewayConfigBody | None = None) -> dict[str, Any]:
    """Probe a gateway and report what it actually said.

    Accepts an unsaved draft, so "Test connection" works before committing --
    the credential is proved in the settings screen rather than discovered to
    be wrong in the middle of a coding run. Values omitted from the draft fall
    back to what is already stored.
    """
    from .mcp_gateway_config import GatewayUpdate, apply_update, load_profile, probe_gateway

    profile = load_profile()
    if body is not None:
        try:
            profile = apply_update(
                profile,
                GatewayUpdate(
                    url=body.url,
                    auth_mode=body.authMode,
                    email=body.email,
                    password=body.password,
                    api_token=body.apiToken,
                    label=body.label,
                    clear_password=body.clearPassword,
                    clear_api_token=body.clearApiToken,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await probe_gateway(profile)
    return result.as_dict()
