"""Remote MCP registry search: normalisation, filtering, and failure modes.

The registry is untrusted input arriving over the network, so the shape of
what comes back is not guaranteed. These tests pin the two properties that
matter: anything unusable is dropped rather than propagated, and a registry
that is down produces a message rather than an exception.
"""
from __future__ import annotations

import httpx
import pytest

from gitpilot.mcp_catalog_remote import (
    DEFAULT_REGISTRY_URL,
    ENV_REGISTRY_URL,
    RemoteCatalogEntry,
    extract_entries,
    normalise_entry,
    registry_url,
    search_registry,
)


# ── Configuration ─────────────────────────────────────────────────────────


def test_registry_defaults_to_matrixhub(monkeypatch):
    monkeypatch.delenv(ENV_REGISTRY_URL, raising=False)
    assert registry_url() == DEFAULT_REGISTRY_URL


def test_registry_url_is_overridable(monkeypatch):
    """A team running its own registry must not have to patch GitPilot."""
    monkeypatch.setenv(ENV_REGISTRY_URL, "https://registry.example.com/")
    assert registry_url() == "https://registry.example.com"


# ── Normalisation ─────────────────────────────────────────────────────────


def test_normalise_a_typical_entry():
    entry = normalise_entry(
        {
            "id": "mcp-postgre-server",
            "description": "PostgreSQL schema discovery and safe queries",
            "endpoint": "http://localhost:8080/mcp",
            "transport": "streamable-http",
            "tags": ["database", "postgresql"],
            "auth": {"type": "bearer", "env": "MCP_POSTGRE_SERVER_TOKEN"},
            "version": "1.2.0",
            "homepage": "https://example.com/mcp-postgre-server",
        },
        source="matrixhub.io",
    )
    assert entry is not None
    assert entry.id == "mcp-postgre-server"
    assert entry.endpoint == "http://localhost:8080/mcp"
    assert entry.tags == ["database", "postgresql"]
    assert entry.auth["env"] == "MCP_POSTGRE_SERVER_TOKEN"
    assert entry.source == "matrixhub.io"


@pytest.mark.parametrize(
    "payload, expected_id, expected_endpoint",
    [
        # Registries disagree about field names; all of these mean the same.
        ({"name": "srv", "url": "http://a/mcp"}, "srv", "http://a/mcp"),
        ({"slug": "srv", "base_url": "http://a/mcp"}, "srv", "http://a/mcp"),
        ({"identifier": "srv", "server_url": "http://a/mcp"}, "srv", "http://a/mcp"),
    ],
)
def test_field_aliases_are_accepted(payload, expected_id, expected_endpoint):
    entry = normalise_entry(payload)
    assert entry is not None
    assert entry.id == expected_id
    assert entry.endpoint == expected_endpoint


def test_summary_and_about_serve_as_description():
    assert normalise_entry({"id": "a", "url": "http://x/mcp", "summary": "S"}).description == "S"
    assert normalise_entry({"id": "a", "url": "http://x/mcp", "about": "A"}).description == "A"


def test_comma_separated_tags_are_split():
    entry = normalise_entry({"id": "a", "url": "http://x/mcp", "tags": "db, sql ,"})
    assert entry.tags == ["db", "sql"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "a string",
        123,
        {},
        # No endpoint and no MCP marker — nothing GitPilot could install.
        {"id": "srv"},
        # An id is required; an anonymous entry cannot be installed or shown.
        {"url": "http://a/mcp"},
    ],
)
def test_unusable_entries_are_dropped(payload):
    assert normalise_entry(payload) is None


def test_non_mcp_catalogue_entries_are_filtered_out():
    """A registry carries more than MCP servers — bundles, agents, datasets."""
    assert normalise_entry({"id": "b", "type": "bundle", "url": "http://a"}) is None
    assert normalise_entry({"id": "d", "kind": "dataset", "url": "http://a"}) is None
    # An explicit MCP marker is enough even without an endpoint.
    assert normalise_entry({"id": "m", "type": "mcp_server"}) is not None


def test_malformed_auth_and_metadata_do_not_leak_through():
    entry = normalise_entry({"id": "a", "url": "http://x/mcp", "auth": "bearer", "metadata": []})
    assert entry.auth == {}
    assert entry.metadata == {}


# ── Envelopes ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["items", "results", "servers", "data", "entries", "catalog"])
def test_every_common_envelope_is_unwrapped(key):
    body = {key: [{"id": "a", "url": "http://x/mcp"}, {"id": "b", "url": "http://y/mcp"}]}
    assert [e.id for e in extract_entries(body)] == ["a", "b"]


def test_a_bare_list_is_accepted():
    body = [{"id": "a", "url": "http://x/mcp"}]
    assert [e.id for e in extract_entries(body)] == ["a"]


def test_a_single_record_is_a_result():
    assert [e.id for e in extract_entries({"id": "a", "url": "http://x/mcp"})] == ["a"]


def test_duplicates_collapse():
    body = {"items": [{"id": "a", "url": "http://x/mcp"}, {"id": "a", "url": "http://y/mcp"}]}
    assert len(extract_entries(body)) == 1


def test_results_are_bounded():
    """A registry returning an enormous page must not become an enormous page."""
    body = {"items": [{"id": f"s{i}", "url": "http://x/mcp"} for i in range(500)]}
    assert len(extract_entries(body)) <= 100


# ── Search ────────────────────────────────────────────────────────────────


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def patch_client(monkeypatch):
    """Route httpx.AsyncClient through a mock transport."""

    def apply(handler):
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = _mock_transport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return apply


@pytest.mark.asyncio
async def test_search_returns_normalised_entries(patch_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "mcp_server"
        assert request.url.params["q"] == "postgres"
        return httpx.Response(
            200,
            json={"items": [{"id": "mcp-postgre-server", "url": "http://a/mcp"}]},
        )

    patch_client(handler)
    items, error = await search_registry("postgres", base_url="https://reg.example.com")

    assert error is None
    assert [i["id"] for i in items] == ["mcp-postgre-server"]
    # Discovery never marks anything installed — that is the store's job.
    assert items[0]["installed"] is False
    assert items[0]["source"] == "reg.example.com"


@pytest.mark.asyncio
async def test_search_tries_the_next_path_on_404(patch_client):
    """Registries disagree about where search lives."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/catalog/search":
            return httpx.Response(404)
        return httpx.Response(200, json={"items": [{"id": "a", "url": "http://x/mcp"}]})

    patch_client(handler)
    items, error = await search_registry(base_url="https://reg.example.com")

    assert error is None
    assert [i["id"] for i in items] == ["a"]
    assert seen[0] == "/catalog/search"
    assert len(seen) > 1


@pytest.mark.asyncio
async def test_an_empty_result_is_not_an_error(patch_client):
    patch_client(lambda request: httpx.Response(200, json={"items": []}))
    items, error = await search_registry(base_url="https://reg.example.com")
    assert items == []
    assert error is None


@pytest.mark.asyncio
async def test_an_unreachable_registry_reports_rather_than_raises(patch_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    patch_client(handler)
    items, error = await search_registry(base_url="https://reg.example.com")

    assert items == []
    assert error and "Cannot reach" in error


@pytest.mark.asyncio
async def test_an_unauthorised_registry_says_which_token_to_set(patch_client):
    patch_client(lambda request: httpx.Response(401))
    items, error = await search_registry(base_url="https://reg.example.com")

    assert items == []
    assert error and "GITPILOT_MATRIXHUB_TOKEN" in error


@pytest.mark.asyncio
async def test_non_json_is_reported_not_raised(patch_client):
    patch_client(lambda request: httpx.Response(200, text="<html>nope</html>"))
    items, error = await search_registry(base_url="https://reg.example.com")

    assert items == []
    assert error is not None


@pytest.mark.asyncio
async def test_a_token_is_sent_when_configured(patch_client):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"items": []})

    patch_client(handler)
    await search_registry(base_url="https://reg.example.com", token="tok-123")
    assert captured["auth"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_a_blank_base_url_falls_back_to_the_configured_registry(patch_client, monkeypatch):
    """Blank means "unset", so the configured default is used.

    An empty override is far more often an accident than a request to
    disable search, and silently searching nothing would look like a
    registry with no results.
    """
    monkeypatch.setenv(ENV_REGISTRY_URL, "https://configured.example.com")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        return httpx.Response(200, json={"items": []})

    patch_client(handler)
    items, error = await search_registry(base_url="   ")

    assert error is None
    assert items == []
    assert seen and seen[0] == "configured.example.com"


def test_a_blank_env_override_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(ENV_REGISTRY_URL, "   ")
    assert registry_url() == DEFAULT_REGISTRY_URL


def test_entry_serialises_for_the_ui():
    payload = RemoteCatalogEntry(id="a", endpoint="http://x/mcp").to_dict()
    for key in ("id", "slug", "description", "endpoint", "tags", "source", "installed"):
        assert key in payload


# ── The bundled catalogue ─────────────────────────────────────────────────


def test_bundled_catalogue_ships_inside_the_package():
    """A ``pip install`` must not land with an empty catalogue.

    ``extensions/mcp_plugins/`` is not packaged — it is the Docker build
    context and exists only in a checkout — so the manifests are duplicated
    into ``gitpilot/mcp_catalog/``. Without that copy every pip user saw
    zero servers on offer.
    """
    import json
    from pathlib import Path

    import gitpilot

    packaged = Path(gitpilot.__file__).resolve().parent / "mcp_catalog"
    assert packaged.is_dir(), "the packaged catalogue directory is missing"

    manifests = sorted(packaged.glob("*/register.json"))
    assert manifests, "the packaged catalogue is empty"
    for manifest in manifests:
        payload = json.loads(manifest.read_text())
        assert payload.get("name"), f"{manifest} has no name"
        assert payload.get("endpoint"), f"{manifest} has no endpoint"


def test_packaged_catalogue_matches_the_repo_source():
    """The two copies must agree, or one of them is lying.

    Editing ``extensions/mcp_plugins/`` without copying the change across
    would ship a stale catalogue to every pip user, which is exactly the
    class of bug this duplication risks.
    """
    import json
    from pathlib import Path

    import gitpilot

    packaged = Path(gitpilot.__file__).resolve().parent / "mcp_catalog"
    repo = Path(gitpilot.__file__).resolve().parent.parent / "extensions" / "mcp_plugins"
    if not repo.is_dir():
        pytest.skip("not a repo checkout")

    for manifest in sorted(repo.glob("*/register.json")):
        twin = packaged / manifest.parent.name / "register.json"
        assert twin.exists(), (
            f"{manifest.parent.name} is missing from gitpilot/mcp_catalog/ — "
            f"copy it across so packaged installs see it"
        )
        assert json.loads(twin.read_text()) == json.loads(manifest.read_text()), (
            f"{manifest.parent.name} differs between the repo and the package"
        )


def test_load_catalog_reads_the_bundled_servers():
    from gitpilot.mcp_admin_api import load_catalog

    ids = {item["id"] for item in load_catalog()}
    assert "mcp-postgre-server" in ids, "the PostgreSQL server should be on offer"
    assert all(item["source"] == "bundled" for item in load_catalog())
