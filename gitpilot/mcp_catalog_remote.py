"""Remote MCP server catalogues, so the local four are not the whole world.

GitPilot ships a small catalogue in ``extensions/mcp_plugins/`` — Postgres,
Milvus, Inspector, and GitPilot's own server. That covers the common cases and
nothing else. When a task needs a capability GitPilot has never heard of, the
user should be able to search a registry, pick a server, and have the agents
gain its tools for that piece of work.

This module is that search. It is deliberately **source-agnostic**: MatrixHub
is the configured default, but any registry that serves a catalogue document
works, because the normaliser accepts the handful of shapes registries
actually use rather than one vendor's schema.

Three rules it is built around:

1. **A registry is untrusted input.** Everything is normalised and bounded
   before it reaches the UI or the store; a malformed entry is dropped, not
   propagated.
2. **Discovery never installs.** Search returns descriptions. Installing is a
   separate, explicit act through the existing install endpoints, so browsing
   a registry cannot change what the agents can do.
3. **Unreachable is a message, not an exception.** A registry that is down
   returns an empty list and a reason, exactly like local model discovery.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

#: Where GitPilot looks for MCP servers when no other source is configured.
DEFAULT_REGISTRY_URL = "https://api.matrixhub.io"

ENV_REGISTRY_URL = "GITPILOT_MATRIXHUB_URL"
ENV_REGISTRY_TOKEN = "GITPILOT_MATRIXHUB_TOKEN"  # noqa: S105 - env var name

#: Paths tried in order. Registries disagree about where search lives, and
#: trying a couple of well-known ones beats making every user configure it.
SEARCH_PATHS = (
    "/catalog/search",
    "/api/v1/catalog/search",
    "/v1/catalog/search",
)

#: Belt and braces against a registry returning something enormous.
MAX_RESULTS = 100
REQUEST_TIMEOUT = 10.0


@dataclass
class RemoteCatalogEntry:
    """One MCP server offered by a registry, in GitPilot's own shape.

    Mirrors the local catalogue entry so the install path and the UI do not
    need to know where a server was discovered.
    """

    id: str
    description: str = ""
    endpoint: str = ""
    transport: str = "streamable-http"
    tags: list[str] = field(default_factory=list)
    auth: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    homepage: str = ""
    version: str = ""
    #: Which registry this came from, so the UI can say so.
    source: str = "remote"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.id,
            "description": self.description,
            "endpoint": self.endpoint,
            "transport": self.transport,
            "tags": list(self.tags),
            "auth": dict(self.auth),
            "metadata": dict(self.metadata),
            "homepage": self.homepage,
            "version": self.version,
            "source": self.source,
            # A remote entry is never pre-installed; the caller marks it.
            "installed": False,
        }


def registry_url() -> str:
    """The configured registry, without a trailing slash.

    A blank or whitespace-only override means "unset", not "no registry" —
    an empty env var is far more often an accident than an intention.
    """
    configured = (os.environ.get(ENV_REGISTRY_URL) or "").strip()
    return (configured or DEFAULT_REGISTRY_URL).rstrip("/")


def registry_token() -> str:
    return os.environ.get(ENV_REGISTRY_TOKEN, "")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _first_str(payload: dict[str, Any], *keys: str) -> str:
    """First non-empty string among *keys*, so vendor naming does not matter."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return [str(t).strip() for t in value if str(t).strip()]
    return []


def _looks_like_mcp_server(payload: dict[str, Any]) -> bool:
    """Filter out catalogue entries that are not MCP servers.

    Registries carry more than MCP servers — agents, bundles, datasets. An
    entry with no endpoint and no MCP marker is not something GitPilot can
    install, and offering it would only produce a failure later.
    """
    kind = _first_str(payload, "type", "kind", "category", "entity_type").lower()
    if kind and "mcp" in kind:
        return True
    if kind and kind not in ("", "server", "tool", "service"):
        return False
    return bool(_first_str(payload, "endpoint", "url", "base_url", "server_url"))


def normalise_entry(payload: Any, source: str = "remote") -> RemoteCatalogEntry | None:
    """Turn one registry record into a catalogue entry, or ``None`` if unusable."""
    if not isinstance(payload, dict):
        return None
    if not _looks_like_mcp_server(payload):
        return None

    identifier = _first_str(payload, "id", "name", "slug", "identifier")
    if not identifier:
        return None

    endpoint = _first_str(payload, "endpoint", "url", "base_url", "server_url")

    auth = payload.get("auth")
    if not isinstance(auth, dict):
        auth = {}

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return RemoteCatalogEntry(
        id=identifier,
        description=_first_str(payload, "description", "summary", "about"),
        endpoint=endpoint,
        transport=_first_str(payload, "transport", "protocol") or "streamable-http",
        tags=_as_tags(payload.get("tags") or payload.get("topics")),
        auth=auth,
        metadata=metadata,
        homepage=_first_str(payload, "homepage", "repository", "upstream", "html_url"),
        version=_first_str(payload, "version", "latest_version"),
        source=source,
    )


def extract_entries(body: Any, source: str = "remote") -> list[RemoteCatalogEntry]:
    """Pull entries out of whatever envelope a registry wrapped them in."""
    records: list[Any] = []

    if isinstance(body, list):
        records = body
    elif isinstance(body, dict):
        for key in ("items", "results", "servers", "data", "entries", "catalog"):
            value = body.get(key)
            if isinstance(value, list):
                records = value
                break
        else:
            # A bare single record is still a result.
            records = [body]

    entries: list[RemoteCatalogEntry] = []
    seen: set[str] = set()
    for record in records[:MAX_RESULTS]:
        entry = normalise_entry(record, source=source)
        if entry and entry.id not in seen:
            seen.add(entry.id)
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _source_label(url: str) -> str:
    try:
        return urlparse(url).hostname or "remote"
    except ValueError:
        return "remote"


async def search_registry(
    query: str = "",
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search a registry for MCP servers.

    Returns ``(entries, error)``. A registry that is unreachable, slow, or
    serving something unrecognisable yields ``([], reason)`` — never an
    exception, because a browsable catalogue failing must not break the
    settings page it is rendered in.
    """
    base = ((base_url or "").strip() or registry_url()).strip().rstrip("/")
    if not base:
        return [], "No MCP registry configured"

    source = _source_label(base)
    auth_token = token if token is not None else registry_token()
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    params: dict[str, str] = {"type": "mcp_server"}
    if query.strip():
        params["q"] = query.strip()

    last_error = "No search endpoint responded"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for path in SEARCH_PATHS:
                url = f"{base}{path}"
                try:
                    resp = await client.get(url, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = f"Cannot reach {base}: {exc}"
                    continue

                if resp.status_code == 404:
                    # Wrong path for this registry — try the next one.
                    last_error = f"{base} has no catalogue at {path}"
                    continue
                if resp.status_code == 401 or resp.status_code == 403:
                    return [], (
                        f"{base} rejected the request. Set {ENV_REGISTRY_TOKEN} "
                        f"if the registry requires a token."
                    )
                if resp.status_code >= 400:
                    last_error = f"{base} returned HTTP {resp.status_code}"
                    continue

                try:
                    body = resp.json()
                except ValueError:
                    last_error = f"{base} did not return JSON"
                    continue

                entries = extract_entries(body, source=source)
                if entries:
                    return [e.to_dict() for e in entries], None
                # A valid, empty answer is a result, not a failure.
                return [], None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("MCP registry search failed: %s", exc)
        return [], f"Registry search failed: {exc}"

    return [], last_error


async def fetch_manifest(
    entry_id: str,
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one server's full record, for the install step.

    Search results are summaries. Installing needs the endpoint and auth
    details, which some registries only include on the detail record.
    """
    base = ((base_url or "").strip() or registry_url()).strip().rstrip("/")
    if not base or not entry_id:
        return None, "No registry or server id"

    source = _source_label(base)
    auth_token = token if token is not None else registry_token()
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    paths = (
        f"/catalog/entities/{entry_id}",
        f"/api/v1/catalog/entities/{entry_id}",
        f"/catalog/{entry_id}",
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for path in paths:
                try:
                    resp = await client.get(f"{base}{path}", headers=headers)
                except httpx.HTTPError:
                    continue
                if resp.status_code >= 400:
                    continue
                try:
                    body = resp.json()
                except ValueError:
                    continue
                entry = normalise_entry(body, source=source)
                if entry:
                    return entry.to_dict(), None
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"Registry lookup failed: {exc}"

    return None, f"{entry_id} not found in {base}"
