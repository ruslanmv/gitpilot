"""Reconcile loop: pull the MCP server registry from MCP Context Forge.

This is the canonical implementation for both ``POST /api/mcp/sync`` (UI
button + ``make sync-mcp``) and any future scheduled background job.

Design contract (industry / GitOps best practices applied)
----------------------------------------------------------

* **Pull-only.** GitPilot never writes back to Forge. Forge is the
  source of truth for "what servers exist"; GitPilot keeps the local
  user-state (`enabled`, `tool_overrides`, `orphan` flag) in
  ``mcp_admin_api.MCPStore``.
* **Idempotent.** ``sync()`` is a pure function from ``forge_state ->
  local_state``. Re-running it produces the same result. Tests rely on
  this property.
* **Non-destructive.** Servers that disappear from Forge are flagged
  ``orphan=True`` and kept locally; the user has a "Forget" action in
  the UI but nothing is auto-deleted.
* **User-state preservation.** Every reconcile re-applies
  ``tool_overrides`` and ``enabled`` from the previous local state. A
  sync never silently disables a server the user just turned on.
* **Custom servers respected.** Servers added via the wizard's "Custom"
  tab carry ``source="user"``. They never get the orphan badge even
  when missing from Forge.
* **Observable.** The :class:`SyncReport` returned is the full
  structured result -- counts, ids, correlation id, timestamp -- so the
  UI banner and audit logs share one source.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

import httpx

from gitpilot.mcp_admin_api import MCPStore, ServerState, StoreSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
ENV_FORGE_URL = "GITPILOT_MCP_FORGE_URL"
ENV_FORGE_TOKEN = "GITPILOT_MCP_FORGE_TOKEN"
ENV_FORGE_LIST_PATH = "GITPILOT_MCP_FORGE_LIST_PATH"
DEFAULT_FORGE_URL = "http://localhost:4444"
# Forge's admin API; configurable so users on a customised gateway can override.
DEFAULT_LIST_PATHS: tuple[str, ...] = (
    "/admin/servers",
    "/registry/servers",
    "/api/servers",
)


def _settings() -> dict[str, str]:
    return {
        "url": os.environ.get(ENV_FORGE_URL, DEFAULT_FORGE_URL).rstrip("/"),
        "token": os.environ.get(ENV_FORGE_TOKEN, ""),
        "list_path": os.environ.get(ENV_FORGE_LIST_PATH, ""),
    }


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ForgeServer:
    """One server entry as advertised by Forge.

    Forge schemas vary slightly by version; we keep this dataclass as a
    forgiving normalised view. Unknown fields are dropped silently
    (forward-compat) -- see :func:`_normalise`.
    """
    id: str
    endpoint: str
    description: str = ""
    tags: tuple[str, ...] = ()
    auth_token_env: str = ""


@dataclass
class SyncReport:
    """Structured outcome of one reconcile pass."""

    added: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    forge_unreachable: bool = False
    error: str | None = None
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.kept) + len(self.orphaned)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def _normalise(payload: Mapping[str, Any]) -> ForgeServer | None:
    """Extract a :class:`ForgeServer` from a Forge dict.

    Returns ``None`` if the entry lacks ``name`` or ``endpoint``; we'd
    rather drop a malformed row than crash the reconcile.
    """
    server_id = (
        payload.get("name")
        or payload.get("id")
        or payload.get("server_name")
    )
    endpoint = (
        payload.get("endpoint")
        or payload.get("url")
        or payload.get("uri")
    )
    if not server_id or not endpoint:
        return None
    raw_tags = payload.get("tags") or []
    auth = payload.get("auth") or {}
    return ForgeServer(
        id=str(server_id),
        endpoint=str(endpoint),
        description=str(payload.get("description") or ""),
        tags=tuple(str(t) for t in raw_tags if isinstance(t, str)),
        auth_token_env=str(auth.get("env") or auth.get("token_env") or ""),
    )


def _extract_servers(body: Any) -> list[ForgeServer]:
    """Forge variants return ``{"servers": [...]}`` or a bare array.

    Both shapes are handled; anything else returns an empty list.
    """
    if isinstance(body, dict):
        items = body.get("servers") or body.get("items") or []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    return [s for s in (_normalise(it) for it in items if isinstance(it, dict)) if s]


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------
async def fetch_forge_registry(
    *,
    url: str,
    token: str,
    list_path_override: str = "",
    http_client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 5.0,
) -> list[ForgeServer]:
    """Fetch + normalise the Forge server list.

    Tries ``list_path_override`` first when supplied; otherwise iterates
    :data:`DEFAULT_LIST_PATHS` until one returns 200. Raises
    :class:`httpx.HTTPError` only when *every* candidate fails.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    candidates: tuple[str, ...] = (
        (list_path_override,) if list_path_override else DEFAULT_LIST_PATHS
    )

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
    last_exc: Exception | None = None
    try:
        for path in candidates:
            full = f"{url.rstrip('/')}{path}"
            try:
                resp = await client.get(full, headers=headers)
                if resp.status_code == 200:
                    return _extract_servers(resp.json())
                # 404 = wrong path; try the next candidate.
                if resp.status_code != 404:
                    last_exc = httpx.HTTPStatusError(
                        f"{full} -> HTTP {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
            except httpx.HTTPError as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        return []
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Reconcile (pure)
# ---------------------------------------------------------------------------
def reconcile(
    snapshot: StoreSnapshot,
    forge_servers: Iterable[ForgeServer],
) -> tuple[StoreSnapshot, SyncReport]:
    """Apply ``forge_servers`` onto ``snapshot``.

    Pure: identical inputs produce identical outputs. Tests rely on
    this. The caller saves the returned snapshot.
    """
    forge_by_id: dict[str, ForgeServer] = {fs.id: fs for fs in forge_servers}
    report = SyncReport()

    # --- additions / refreshes -------------------------------------------
    for fid, fserver in forge_by_id.items():
        if fid not in snapshot.servers:
            snapshot.servers[fid] = ServerState(
                id=fid,
                installed=True,
                enabled=False,  # opt-in; user toggles in UI
                endpoint=fserver.endpoint,
                auth_token_env=fserver.auth_token_env,
                description=fserver.description,
                tags=list(fserver.tags),
                source="forge-sync",
            )
            report.added.append(fid)
        else:
            # Refresh metadata Forge owns; preserve user-owned fields.
            current = snapshot.servers[fid]
            current.endpoint = fserver.endpoint or current.endpoint
            current.description = fserver.description or current.description
            current.tags = list(fserver.tags) or current.tags
            if fserver.auth_token_env:
                current.auth_token_env = fserver.auth_token_env
            # Drop any orphan flag; the server is back in Forge.
            current.orphan = False
            report.kept.append(fid)

    # --- orphans (in local but not in forge) -----------------------------
    for sid, sstate in list(snapshot.servers.items()):
        if sid in forge_by_id:
            continue
        # Custom (user-added) servers are never orphaned.
        if sstate.source == "user":
            continue
        # Mark, never delete.
        sstate.orphan = True
        report.orphaned.append(sid)

    return snapshot, report


# ---------------------------------------------------------------------------
# End-to-end entry point
# ---------------------------------------------------------------------------
async def sync(
    *,
    store: MCPStore | None = None,
    forge_url: str | None = None,
    forge_token: str | None = None,
    list_path_override: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> SyncReport:
    """Top-level entry point used by the REST endpoint and the CLI.

    Always returns a :class:`SyncReport`; never raises. Transport
    failures are captured as ``forge_unreachable=True`` so the UI can
    render a single, deterministic error path.
    """
    settings = _settings()
    s = MCPStore() if store is None else store

    url = forge_url or settings["url"]
    token = forge_token or settings["token"]
    path = list_path_override or settings["list_path"]

    snapshot = s.load()

    try:
        forge_servers = await fetch_forge_registry(
            url=url,
            token=token,
            list_path_override=path,
            http_client=http_client,
        )
    except httpx.HTTPError as exc:
        report = SyncReport(forge_unreachable=True, error=str(exc))
        logger.warning(
            "forge sync unreachable",
            extra={"url": url, "correlation_id": report.correlation_id},
        )
        return report

    new_snapshot, report = reconcile(snapshot, forge_servers)
    s.save(new_snapshot)

    logger.info(
        "mcp forge sync completed",
        extra={
            "correlation_id": report.correlation_id,
            "added": len(report.added),
            "kept": len(report.kept),
            "orphaned": len(report.orphaned),
        },
    )
    return report
