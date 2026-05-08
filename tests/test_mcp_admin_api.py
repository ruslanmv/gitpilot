"""Tests for the MCP admin REST API.

We test the store + the FastAPI router via TestClient. Network calls to
the forge gateway are stubbed out by overriding ``gateway_status`` so
the tests don't need a live forge.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import mcp_admin_api as admin


@pytest.fixture
def temp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_file = tmp_path / "mcp_servers.json"
    monkeypatch.setenv(admin.STATE_FILE_ENV, str(state_file))
    # Stub the gateway probe so we don't need the network.
    async def fake_status(url: str, token: str) -> dict:
        return {"reachable": True, "detail": {"status": "ok"}}

    monkeypatch.setattr(admin, "gateway_status", fake_status)
    return state_file


@pytest.fixture
def client(temp_state: Path) -> TestClient:
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def test_store_seeds_known_servers_disabled(temp_state: Path) -> None:
    snap = admin.MCPStore().load()
    assert "mcp-postgre-server" in snap.servers
    assert "mcp-milvus-server" in snap.servers
    assert "mcp-inspector-server" in snap.servers
    assert all(not s.enabled for s in snap.servers.values()), (
        "first-boot must keep servers disabled until the user opts in"
    )


def test_store_round_trip(temp_state: Path) -> None:
    store = admin.MCPStore()
    snap = store.load()
    snap.servers["mcp-postgre-server"].enabled = True
    snap.servers["mcp-postgre-server"].tool_overrides["postgres.describe_table"] = True
    store.save(snap)

    blob = json.loads(temp_state.read_text())
    assert blob["version"] == admin.STATE_VERSION
    assert blob["servers"]["mcp-postgre-server"]["enabled"] is True
    assert (
        blob["servers"]["mcp-postgre-server"]["tool_overrides"][
            "postgres.describe_table"
        ]
        is True
    )


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool,risk",
    [
        ("postgres.describe_table", admin.RISK_LOW),
        ("postgres.list_tables", admin.RISK_LOW),
        ("postgres.execute_write", admin.RISK_MEDIUM),
        ("milvus.insert", admin.RISK_MEDIUM),
        ("milvus.create_collection", admin.RISK_MEDIUM),
        ("postgres.drop_table", admin.RISK_HIGH),
        ("milvus.delete_collection", admin.RISK_HIGH),
        ("milvus.truncate_index", admin.RISK_HIGH),
    ],
)
def test_classify_risk(tool: str, risk: str) -> None:
    assert admin.classify_risk(tool) == risk


# ---------------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------------
def test_status_endpoint(client: TestClient) -> None:
    r = client.get("/api/mcp/status")
    assert r.status_code == 200
    body = r.json()
    assert body["servers_installed"] >= 3
    assert body["servers_enabled"] == 0
    assert body["gateway_reachable"] is True


def test_list_servers_includes_tools_with_risk(client: TestClient) -> None:
    body = client.get("/api/mcp/servers").json()
    by_id = {s["id"]: s for s in body["servers"]}
    pg = by_id["mcp-postgre-server"]
    assert pg["tool_count"] > 0
    risks = {t["name"]: t["risk"] for t in pg["tools"]}
    assert risks["postgres.describe_table"] == admin.RISK_LOW
    # used_by mapping is filled in for known tools.
    desc = next(t for t in pg["tools"] if t["name"] == "postgres.describe_table")
    assert "coder" in desc["used_by"]


def test_enable_disable_lifecycle(client: TestClient) -> None:
    r = client.post("/api/mcp/servers/mcp-postgre-server/enable")
    assert r.status_code == 200 and r.json()["enabled"] is True

    body = client.get("/api/mcp/servers").json()
    assert next(
        s for s in body["servers"] if s["id"] == "mcp-postgre-server"
    )["enabled"] is True

    r = client.post("/api/mcp/servers/mcp-postgre-server/disable")
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_tool_toggle_blocks_destructive(client: TestClient) -> None:
    # Try to enable a destructive tool the policy engine should deny.
    r = client.post(
        "/api/mcp/servers/mcp-postgre-server/tools/postgres.drop_table/toggle",
        json={"enabled": True},
    )
    assert r.status_code == 409
    assert "policy" in r.json()["detail"].lower()


def test_tool_toggle_allows_safe_tool(client: TestClient) -> None:
    r = client.post(
        "/api/mcp/servers/mcp-postgre-server/tools/postgres.describe_table/toggle",
        json={"enabled": False},
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_install_custom_requires_name_and_endpoint(client: TestClient) -> None:
    r = client.post("/api/mcp/servers/install-custom", json={"register_json": {}})
    assert r.status_code == 422


def test_install_custom_persists_server(client: TestClient) -> None:
    payload = {
        "register_json": {
            "name": "mcp-neo4j-server",
            "endpoint": "http://mcp-neo4j-server:8083/mcp",
            "description": "Neo4j MCP server",
            "tags": ["graph", "neo4j"],
            "auth": {"env": "MCP_NEO4J_SERVER_TOKEN"},
        }
    }
    r = client.post("/api/mcp/servers/install-custom", json=payload)
    assert r.status_code == 200
    out = r.json()["server"]
    assert out["id"] == "mcp-neo4j-server"
    assert out["installed"] is True
    assert out["enabled"] is False  # custom installs land disabled too
    assert out["tags"] == ["graph", "neo4j"]


def test_uninstall_unknown_server_404(client: TestClient) -> None:
    r = client.post("/api/mcp/servers/nope/uninstall")
    assert r.status_code == 404


def test_uninstall_known_server(client: TestClient) -> None:
    r = client.post("/api/mcp/servers/mcp-postgre-server/uninstall")
    assert r.status_code == 200
    body = client.get("/api/mcp/servers").json()
    assert "mcp-postgre-server" not in {s["id"] for s in body["servers"]}


# ---------------------------------------------------------------------------
# gateway_base_url + direct probe (Phase 1 fixes)
# ---------------------------------------------------------------------------
import httpx
import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:4444/mcp", "http://localhost:4444"),
        ("http://localhost:4444/mcp/", "http://localhost:4444"),
        ("http://localhost:4444", "http://localhost:4444"),
        ("https://forge.example.com/mcp", "https://forge.example.com"),
        # Already a base; idempotent.
        ("http://localhost:4444/", "http://localhost:4444"),
    ],
)
def test_gateway_base_url_strips_mcp_suffix(raw: str, expected: str) -> None:
    assert admin.gateway_base_url(raw) == expected


@pytest.mark.asyncio
async def test_direct_health_probe_success(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"status": "ok", "version": "1.0.0"})

    transport = httpx.MockTransport(handler)

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(admin.httpx, "AsyncClient", fake_async_client)

    out = await admin._direct_health_probe("http://mcp-postgre-server:8080/mcp")
    assert out["ok"] is True
    assert out["status_code"] == 200
    assert out["detail"]["status"] == "ok"
    # Probe targeted /health at the *origin*, not /mcp/health.
    assert captured["url"].endswith("/health")
    assert "/mcp/health" not in captured["url"]


@pytest.mark.asyncio
async def test_direct_health_probe_returns_string_on_failure(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        admin.httpx,
        "AsyncClient",
        lambda *a, **k: real_async_client(transport=transport, **k),
    )

    out = await admin._direct_health_probe("http://x:9000/mcp")
    assert out["ok"] is False
    assert "nope" in out["reason"]


# ---------------------------------------------------------------------------
# Two-network-context endpoint translator (Phase 2)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "in_network,host_url",
    [
        # Known docker service → translated to localhost:<published-port>.
        (
            "http://mcp-postgre-server:8080/mcp",
            "http://localhost:8080/mcp",
        ),
        (
            "http://mcp-milvus-server:8082/mcp",
            "http://localhost:8082/mcp",
        ),
        (
            "http://mcp-inspector-server:8081/mcp",
            "http://localhost:8081/mcp",
        ),
        (
            "http://mcp-context-forge:4444/mcp",
            "http://localhost:4444/mcp",
        ),
    ],
)
def test_to_host_url_translates_known_docker_services(
    in_network: str, host_url: str
) -> None:
    assert admin.to_host_url(in_network) == host_url


@pytest.mark.parametrize(
    "url",
    [
        # Already host-reachable.
        "http://localhost:8080/mcp",
        "http://127.0.0.1:8080/mcp",
        # User-added custom server (real DNS / IP).
        "http://my-mcp.example.com/mcp",
        "https://mcp.acme.internal:9000/mcp",
        # Unknown docker service name (a custom user install).
        "http://mcp-neo4j-server:8083/mcp",
    ],
)
def test_to_host_url_is_idempotent_for_non_known_hosts(url: str) -> None:
    assert admin.to_host_url(url) == url


def test_to_host_url_honours_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Operator publishes postgre on a non-default host port.
    monkeypatch.setenv("MCP_POSTGRE_PORT", "18080")
    assert (
        admin.to_host_url("http://mcp-postgre-server:8080/mcp")
        == "http://localhost:18080/mcp"
    )


def test_to_host_url_handles_garbage_input_safely() -> None:
    # Anything that doesn't parse as a URL falls through unchanged so a
    # probe can surface the *original* failure rather than a translation
    # crash.
    assert admin.to_host_url("not-a-url") == "not-a-url"
    assert admin.to_host_url("") == ""


@pytest.mark.asyncio
async def test_direct_probe_translates_docker_service_to_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe is sent to localhost:<published>, not to the docker service name."""
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["port"] = str(request.url.port)
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        admin.httpx,
        "AsyncClient",
        lambda *a, **k: real_async_client(transport=transport, **k),
    )

    out = await admin._direct_health_probe("http://mcp-postgre-server:8080/mcp")
    assert out["ok"] is True
    # The probe rewrote the docker service name to localhost.
    assert captured["host"] == "localhost"
    assert captured["port"] == "8080"
