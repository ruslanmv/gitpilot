"""Tests for gitpilot.mcp_forge_sync.

Pure-function reconcile is exercised against a stub Forge response, then
the REST endpoints (``/api/mcp/sync`` and ``/api/mcp/sync/status``) are
exercised end-to-end via FastAPI's TestClient. No real Forge needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import mcp_admin_api as admin
from gitpilot import mcp_forge_sync as sync_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "mcp_servers.json"
    monkeypatch.setenv(admin.STATE_FILE_ENV, str(state))
    return state


@pytest.fixture
def store(temp_state: Path) -> admin.MCPStore:
    return admin.MCPStore()


def _forge(payload: list[dict]) -> httpx.AsyncClient:
    """Mock httpx.AsyncClient that always returns ``payload``."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/admin/servers", "/registry/servers", "/api/servers"}:
            return httpx.Response(200, json={"servers": payload})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=2.0)


# ---------------------------------------------------------------------------
# _normalise / _extract_servers
# ---------------------------------------------------------------------------
def test_normalise_drops_malformed_rows() -> None:
    assert sync_mod._normalise({}) is None
    assert sync_mod._normalise({"name": "x"}) is None  # no endpoint
    assert sync_mod._normalise({"endpoint": "http://x"}) is None  # no id

    fs = sync_mod._normalise(
        {
            "name": "mcp-foo",
            "endpoint": "http://foo:9000/mcp",
            "description": "Foo",
            "tags": ["a", "b", 7],
            "auth": {"env": "FOO_TOKEN"},
        }
    )
    assert fs is not None
    assert fs.id == "mcp-foo"
    assert fs.tags == ("a", "b")  # the 7 is dropped silently
    assert fs.auth_token_env == "FOO_TOKEN"


def test_extract_servers_handles_both_shapes() -> None:
    payload = [{"name": "a", "endpoint": "http://a/mcp"}]
    assert len(sync_mod._extract_servers({"servers": payload})) == 1
    assert len(sync_mod._extract_servers(payload)) == 1
    assert sync_mod._extract_servers("garbage") == []


# ---------------------------------------------------------------------------
# Pure reconcile
# ---------------------------------------------------------------------------
def test_reconcile_adds_new_servers(store: admin.MCPStore) -> None:
    snap = store.load()
    # Wipe seeded servers so we start clean.
    snap.servers.clear()

    forge = [
        sync_mod.ForgeServer(id="mcp-postgre-server", endpoint="http://x:8080/mcp"),
        sync_mod.ForgeServer(id="mcp-new", endpoint="http://x:9999/mcp"),
    ]
    new_snap, report = sync_mod.reconcile(snap, forge)
    assert sorted(report.added) == ["mcp-new", "mcp-postgre-server"]
    assert report.kept == []
    assert report.orphaned == []
    # New servers land disabled (opt-in).
    assert all(not s.enabled for s in new_snap.servers.values())


def test_reconcile_kept_preserves_user_state(store: admin.MCPStore) -> None:
    snap = store.load()
    pg = snap.servers["mcp-postgre-server"]
    pg.enabled = True
    pg.tool_overrides["postgres.describe_table"] = True

    forge = [
        sync_mod.ForgeServer(
            id="mcp-postgre-server",
            endpoint="http://updated:8080/mcp",
            description="updated description",
        )
    ]
    new_snap, report = sync_mod.reconcile(snap, forge)
    assert "mcp-postgre-server" in report.kept
    refreshed = new_snap.servers["mcp-postgre-server"]
    assert refreshed.endpoint == "http://updated:8080/mcp"
    assert refreshed.description == "updated description"
    # Critically: user state is preserved.
    assert refreshed.enabled is True
    assert refreshed.tool_overrides["postgres.describe_table"] is True


def test_reconcile_marks_orphans_without_deleting(store: admin.MCPStore) -> None:
    snap = store.load()
    forge = [
        sync_mod.ForgeServer(id="mcp-postgre-server", endpoint="http://x/mcp"),
    ]  # mcp-milvus-server and mcp-inspector-server disappear
    new_snap, report = sync_mod.reconcile(snap, forge)
    assert "mcp-milvus-server" in report.orphaned
    assert "mcp-inspector-server" in report.orphaned
    # Still present locally; just flagged.
    assert "mcp-milvus-server" in new_snap.servers
    assert new_snap.servers["mcp-milvus-server"].orphan is True


def test_reconcile_does_not_orphan_user_added_servers(store: admin.MCPStore) -> None:
    snap = store.load()
    snap.servers["mcp-custom"] = admin.ServerState(
        id="mcp-custom",
        installed=True,
        enabled=True,
        endpoint="http://x/mcp",
        source="user",
    )

    forge: list[sync_mod.ForgeServer] = []  # nothing in forge
    _, report = sync_mod.reconcile(snap, forge)
    assert "mcp-custom" not in report.orphaned


def test_reconcile_clears_orphan_when_server_returns(store: admin.MCPStore) -> None:
    snap = store.load()
    snap.servers["mcp-postgre-server"].orphan = True
    forge = [sync_mod.ForgeServer(id="mcp-postgre-server", endpoint="http://x/mcp")]
    new_snap, _ = sync_mod.reconcile(snap, forge)
    assert new_snap.servers["mcp-postgre-server"].orphan is False


def test_reconcile_is_idempotent(store: admin.MCPStore) -> None:
    snap = store.load()
    forge = [sync_mod.ForgeServer(id="mcp-postgre-server", endpoint="http://x/mcp")]
    once, _ = sync_mod.reconcile(snap, forge)
    twice, report = sync_mod.reconcile(once, forge)
    # Same input twice → no further additions.
    assert report.added == []


# ---------------------------------------------------------------------------
# fetch_forge_registry: HTTP round-trip with MockTransport
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_forge_registry_tries_default_paths() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/registry/servers":
            return httpx.Response(
                200, json={"servers": [{"name": "x", "endpoint": "http://x/mcp"}]}
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await sync_mod.fetch_forge_registry(
        url="http://forge.test", token="t", http_client=client
    )
    await client.aclose()
    assert [s.id for s in out] == ["x"]
    # First default candidate /admin/servers tried before /registry/servers.
    assert seen[0] == "/admin/servers"


@pytest.mark.asyncio
async def test_fetch_forge_registry_raises_when_all_paths_fail() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPError):
        await sync_mod.fetch_forge_registry(
            url="http://forge.test", token="", http_client=client
        )
    await client.aclose()


# ---------------------------------------------------------------------------
# sync() top-level entry point
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_marks_unreachable_when_forge_down(
    store: admin.MCPStore,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    report = await sync_mod.sync(store=store, http_client=client)
    await client.aclose()
    assert report.forge_unreachable is True
    assert report.error is not None
    # The store is left untouched on failure.
    snap = store.load()
    assert "mcp-postgre-server" in snap.servers


@pytest.mark.asyncio
async def test_sync_writes_back_with_real_payload(
    store: admin.MCPStore,
) -> None:
    payload = [
        {"name": "mcp-postgre-server", "endpoint": "http://forge-pg:8080/mcp"},
        {"name": "mcp-fresh", "endpoint": "http://forge-new:9090/mcp"},
    ]
    client = _forge(payload)
    report = await sync_mod.sync(store=store, http_client=client)
    await client.aclose()
    assert "mcp-fresh" in report.added
    snap = store.load()
    assert snap.servers["mcp-postgre-server"].endpoint == "http://forge-pg:8080/mcp"
    assert "mcp-fresh" in snap.servers
    assert snap.servers["mcp-fresh"].enabled is False  # opt-in


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@pytest.fixture
def client(temp_state: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """FastAPI client with a stubbed sync engine.

    We patch ``mcp_forge_sync.sync`` so the REST layer can be tested
    without a live Forge.
    """
    app = FastAPI()
    app.include_router(admin.router)

    async def fake_sync(**kwargs):
        return sync_mod.SyncReport(
            added=["mcp-fresh"],
            kept=["mcp-postgre-server"],
            orphaned=[],
        )

    monkeypatch.setattr(sync_mod, "sync", fake_sync)
    # Reset the in-memory last report for clean state.
    monkeypatch.setattr(admin, "_LAST_SYNC", None, raising=False)
    return TestClient(app)


def test_status_endpoint_when_never_synced(client: TestClient) -> None:
    body = client.get("/api/mcp/sync/status").json()
    assert body["ran"] is False
    assert body["added"] == []


def test_sync_endpoint_returns_report(client: TestClient) -> None:
    r = client.post("/api/mcp/sync", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == ["mcp-fresh"]
    assert body["kept"] == ["mcp-postgre-server"]
    assert body["orphaned"] == []
    assert "correlation_id" in body
    assert "timestamp" in body


def test_status_after_sync_reflects_last_run(client: TestClient) -> None:
    client.post("/api/mcp/sync", json={})
    body = client.get("/api/mcp/sync/status").json()
    assert body["ran"] is True
    assert body["added"] == ["mcp-fresh"]


def test_forget_refuses_non_orphan(client: TestClient) -> None:
    r = client.post("/api/mcp/servers/mcp-postgre-server/forget")
    assert r.status_code == 409


def test_forget_orphan_removes_it(client: TestClient, temp_state: Path) -> None:
    # Mark inspector-server orphan in the store directly.
    store = admin.MCPStore()
    snap = store.load()
    snap.servers["mcp-inspector-server"].orphan = True
    store.save(snap)

    r = client.post("/api/mcp/servers/mcp-inspector-server/forget")
    assert r.status_code == 200
    assert r.json()["forgotten"] == "mcp-inspector-server"

    body = client.get("/api/mcp/servers").json()
    assert "mcp-inspector-server" not in {s["id"] for s in body["servers"]}


def test_list_servers_surfaces_orphan_field(client: TestClient) -> None:
    body = client.get("/api/mcp/servers").json()
    for s in body["servers"]:
        assert "orphan" in s
        assert "source" in s
