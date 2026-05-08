"""Tests for gitpilot.mcp_tools_bridge.

The bridge is what makes sync'd MCP tools usable by the agents. We
verify three properties end-to-end:

1. Selection: only enabled servers + enabled tools end up in the
   agent toolbox; mutation tools require ``include_mutation=True``;
   orphaned servers are excluded.
2. Invocation: a built tool callable issues the correct
   ``tools/call`` envelope and surfaces a string the agent can read.
3. Failure mode: HTTP errors come back as a sentence, never as an
   uncaught exception.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gitpilot import mcp_admin_api as admin
from gitpilot import mcp_tools_bridge as bridge


@pytest.fixture
def temp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "mcp_servers.json"
    monkeypatch.setenv(admin.STATE_FILE_ENV, str(state))
    return state


@pytest.fixture
def store(temp_state: Path) -> admin.MCPStore:
    s = admin.MCPStore()
    snap = s.load()
    pg = snap.servers["mcp-postgre-server"]
    pg.enabled = True
    pg.tool_overrides = {
        "postgres.list_databases": True,
        "postgres.describe_table": True,
    }
    s.save(snap)
    return s


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def test_describe_returns_only_enabled_read_tools(store: admin.MCPStore) -> None:
    out = bridge.describe_available_tools(store=store)
    names = {t["name"] for t in out}
    assert names == {"postgres.list_databases", "postgres.describe_table"}
    for t in out:
        assert t["scope"] == "read"
        assert t["risk"] == "low"


def test_disabled_server_yields_no_tools(store: admin.MCPStore) -> None:
    snap = store.load()
    snap.servers["mcp-postgre-server"].enabled = False
    store.save(snap)
    assert bridge.describe_available_tools(store=store) == []


def test_orphan_server_yields_no_tools(store: admin.MCPStore) -> None:
    snap = store.load()
    snap.servers["mcp-postgre-server"].orphan = True
    store.save(snap)
    assert bridge.describe_available_tools(store=store) == []


def test_mutation_tools_require_explicit_opt_in(
    store: admin.MCPStore,
) -> None:
    snap = store.load()
    snap.servers["mcp-postgre-server"].tool_overrides[
        "postgres.execute_write"
    ] = True
    store.save(snap)

    safe = bridge.describe_available_tools(store=store, include_mutation=False)
    assert all(t["scope"] != "mutation" for t in safe)

    full = bridge.describe_available_tools(store=store, include_mutation=True)
    mutation = [t for t in full if t["scope"] == "mutation"]
    assert mutation and mutation[0]["name"] == "postgres.execute_write"


def test_explicit_false_override_disables_a_tool(
    store: admin.MCPStore,
) -> None:
    snap = store.load()
    snap.servers["mcp-postgre-server"].tool_overrides[
        "postgres.list_databases"
    ] = False
    store.save(snap)
    names = {
        t["name"] for t in bridge.describe_available_tools(store=store)
    }
    assert "postgres.list_databases" not in names
    assert "postgres.describe_table" in names


# ---------------------------------------------------------------------------
# Build (raw callables — crewai is not always installed in test env)
# ---------------------------------------------------------------------------
def test_build_returns_one_callable_per_descriptor(
    store: admin.MCPStore,
) -> None:
    tools = bridge.build_mcp_agent_tools(store=store)
    assert len(tools) == 2
    for t in tools:
        # Either a CrewAI Tool object (has .run / .func) or a raw
        # callable -- both are valid runtime shapes for the agent.
        assert callable(t) or hasattr(t, "func") or hasattr(t, "run")


def test_max_tools_cap_is_respected(store: admin.MCPStore) -> None:
    out = bridge.build_mcp_agent_tools(store=store, max_tools=1)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Invocation: HTTP round-trip with MockTransport
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoke_remote_tool_success() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, json={"success": True, "result": {"databases": ["x"]}}
        )

    descriptor = bridge.MCPToolDescriptor(
        name="postgres.list_databases",
        description="list dbs",
        server_id="mcp-postgre-server",
        server_endpoint="http://forge-test/mcp-server/mcp",
        auth_token="abc",
        scope="read",
        risk="low",
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await bridge.invoke_remote_tool(descriptor, {}, http_client=client)
    await client.aclose()

    assert out["ok"] is True
    assert out["result"]["result"]["databases"] == ["x"]
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["tool"] == "postgres.list_databases"
    assert captured["headers"].get("authorization") == "Bearer abc"
    assert captured["headers"].get("x-gitpilot-origin") == "agent"


@pytest.mark.asyncio
async def test_invoke_remote_tool_returns_string_on_http_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    descriptor = bridge.MCPToolDescriptor(
        name="postgres.list_databases",
        description="list dbs",
        server_id="mcp-postgre-server",
        server_endpoint="http://forge-test/mcp",
        auth_token="wrong",
        scope="read",
        risk="low",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await bridge.invoke_remote_tool(descriptor, {}, http_client=client)
    await client.aclose()
    assert out["ok"] is False
    assert "401" in out["error"]


@pytest.mark.asyncio
async def test_invoke_remote_tool_returns_string_on_transport_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    descriptor = bridge.MCPToolDescriptor(
        name="postgres.list_databases",
        description="list",
        server_id="x",
        server_endpoint="http://x/mcp",
        auth_token="t",
        scope="read",
        risk="low",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await bridge.invoke_remote_tool(descriptor, {}, http_client=client)
    await client.aclose()
    assert out["ok"] is False
    assert "nope" in out["error"]
