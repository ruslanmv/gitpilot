"""Tests for the GitPilot MCP server (gitpilot.mcp_server + bridge).

We exercise three layers:

1. **Auth + scope gate** in :mod:`gitpilot.mcp_server` — pure-function,
   no FastAPI involved.
2. **Tool dispatch** through :class:`GitPilotMCPServer.call_tool` with a
   stub handler, so we don't depend on GitPilot internals.
3. **HTTP bridge** in :mod:`gitpilot.mcp_server_bridge` via
   :class:`fastapi.testclient.TestClient`, including the
   ``X-Gitpilot-Origin: self`` recursion guard.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import mcp_server, mcp_server_bridge, mcp_server_tools


# ---------------------------------------------------------------------------
# Config + tool catalog basics
# ---------------------------------------------------------------------------
def test_default_config_disabled() -> None:
    cfg = mcp_server.MCPServerConfig()
    assert cfg.enabled is False
    assert cfg.allow_mutation is False
    assert cfg.mount_path == "/mcp-server/mcp"


def test_env_config_picks_up_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_EXPOSE_MCP_SERVER", "true")
    monkeypatch.setenv("GITPILOT_MCP_SERVER_TOKEN", "abc")
    monkeypatch.setenv("GITPILOT_MCP_SERVER_ALLOW_MUTATION", "true")
    monkeypatch.setenv("GITPILOT_MCP_SERVER_MUTATION_TOKEN", "abc")
    cfg = mcp_server.MCPServerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.auth_token == "abc"
    assert cfg.allow_mutation is True


def test_tool_catalog_is_stable() -> None:
    """Each tool name appears in exactly one scope and has an input schema."""
    names = [t.name for t in mcp_server.TOOL_CATALOG]
    assert len(names) == len(set(names)), "duplicate tool names"
    for tool in mcp_server.TOOL_CATALOG:
        assert tool.scope in {
            mcp_server.SCOPE_READ,
            mcp_server.SCOPE_PLAN,
            mcp_server.SCOPE_MUTATION,
        }
        assert tool.input_schema.get("type") == "object"


def test_handler_table_matches_catalog() -> None:
    catalog_names = {t.name for t in mcp_server.TOOL_CATALOG}
    assert set(mcp_server_tools.HANDLER_TABLE) == catalog_names


# ---------------------------------------------------------------------------
# authorize() pure function
# ---------------------------------------------------------------------------
def _cfg(**kwargs: Any) -> mcp_server.MCPServerConfig:
    base = dict(
        enabled=True,
        auth_token="secret",
        mutation_token="secret-mut",
        allow_mutation=False,
    )
    base.update(kwargs)
    return mcp_server.MCPServerConfig(**base)


def test_authorize_allows_read_with_valid_bearer() -> None:
    mcp_server.authorize(
        _cfg(),
        bearer="Bearer secret",
        origin_header=None,
        scope=mcp_server.SCOPE_READ,
    )


def test_authorize_rejects_self_origin() -> None:
    with pytest.raises(mcp_server.RecursionGuardError):
        mcp_server.authorize(
            _cfg(),
            bearer="Bearer secret",
            origin_header="self",
            scope=mcp_server.SCOPE_READ,
        )


def test_authorize_rejects_missing_bearer() -> None:
    with pytest.raises(mcp_server.AuthError):
        mcp_server.authorize(
            _cfg(),
            bearer=None,
            origin_header=None,
            scope=mcp_server.SCOPE_READ,
        )


def test_authorize_rejects_wrong_token() -> None:
    with pytest.raises(mcp_server.AuthError):
        mcp_server.authorize(
            _cfg(),
            bearer="Bearer wrong",
            origin_header=None,
            scope=mcp_server.SCOPE_READ,
        )


def test_authorize_blocks_mutation_when_disabled() -> None:
    with pytest.raises(mcp_server.ScopeError):
        mcp_server.authorize(
            _cfg(allow_mutation=False),
            bearer="Bearer secret",
            origin_header=None,
            scope=mcp_server.SCOPE_MUTATION,
        )


def test_authorize_blocks_mutation_with_wrong_token() -> None:
    with pytest.raises(mcp_server.ScopeError):
        mcp_server.authorize(
            _cfg(allow_mutation=True, mutation_token="other"),
            bearer="Bearer secret",
            origin_header=None,
            scope=mcp_server.SCOPE_MUTATION,
        )


def test_authorize_allows_mutation_when_token_matches() -> None:
    mcp_server.authorize(
        _cfg(allow_mutation=True, mutation_token="secret"),
        bearer="Bearer secret",
        origin_header=None,
        scope=mcp_server.SCOPE_MUTATION,
    )


# ---------------------------------------------------------------------------
# call_tool dispatch with a stub handler
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_call_tool_dispatches_to_handler() -> None:
    cfg = _cfg()
    server = mcp_server.GitPilotMCPServer(cfg)

    captured: dict[str, Any] = {}

    async def fake_healthz(args: dict[str, Any], ctx: mcp_server.CallContext):
        captured["called"] = True
        return {"status": "ok", "version": "test"}

    server.register_handler("gitpilot.healthz", fake_healthz)

    out = await server.call_tool(
        "gitpilot.healthz",
        {},
        bearer="Bearer secret",
        origin_header=None,
    )
    assert captured["called"] is True
    assert out["tool"] == "gitpilot.healthz"
    assert out["scope"] == "read"
    assert out["result"]["status"] == "ok"
    assert "call_id" in out
    assert isinstance(out["elapsed_ms"], int)


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_raises() -> None:
    server = mcp_server.GitPilotMCPServer(_cfg())
    with pytest.raises(mcp_server.MCPServerError):
        await server.call_tool(
            "gitpilot.does_not_exist",
            {},
            bearer="Bearer secret",
            origin_header=None,
        )


@pytest.mark.asyncio
async def test_call_tool_missing_handler_raises() -> None:
    server = mcp_server.GitPilotMCPServer(_cfg())
    # Don't bind any handlers; healthz exists in catalog but has no impl.
    with pytest.raises(mcp_server.MCPServerError, match="No handler registered"):
        await server.call_tool(
            "gitpilot.healthz",
            {},
            bearer="Bearer secret",
            origin_header=None,
        )


# ---------------------------------------------------------------------------
# HTTP bridge
# ---------------------------------------------------------------------------
@pytest.fixture
def http_app() -> tuple[FastAPI, mcp_server.MCPServerConfig]:
    app = FastAPI()
    cfg = _cfg()
    mcp_server_bridge.mount(app, cfg)
    return app, cfg


def test_healthz_unauthenticated(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.get("/mcp-server/mcp/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tool_count"] == len(mcp_server.TOOL_CATALOG)


def test_tools_list_no_auth_required(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post("/mcp-server/mcp", json={"method": "tools/list"})
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert any(t["name"] == "gitpilot.healthz" for t in tools)


def test_call_tool_requires_bearer(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        json={"method": "tools/call", "tool": "gitpilot.healthz", "arguments": {}},
    )
    assert r.status_code == 401


def test_call_tool_rejects_self_origin(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        headers={"authorization": "Bearer secret", "x-gitpilot-origin": "self"},
        json={"method": "tools/call", "tool": "gitpilot.healthz", "arguments": {}},
    )
    assert r.status_code == 409


def test_call_tool_returns_envelope(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        headers={"authorization": "Bearer secret"},
        json={"method": "tools/call", "tool": "gitpilot.healthz", "arguments": {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["tool"] == "gitpilot.healthz"
    assert body["scope"] == "read"
    assert body["result"]["status"] == "ok"


def test_mutation_call_blocked_when_allow_mutation_false() -> None:
    app = FastAPI()
    cfg = mcp_server.MCPServerConfig(
        enabled=True,
        auth_token="secret",
        allow_mutation=False,
    )
    mcp_server_bridge.mount(app, cfg)
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        headers={"authorization": "Bearer secret"},
        json={
            "method": "tools/call",
            "tool": "gitpilot.create_pr",
            "arguments": {
                "owner": "x",
                "repo": "y",
                "head": "feat",
                "title": "wip",
            },
        },
    )
    assert r.status_code == 403


def test_unknown_method_400(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        headers={"authorization": "Bearer secret"},
        json={"method": "tools/teleport"},
    )
    assert r.status_code == 400


def test_missing_tool_argument_422(http_app) -> None:
    app, _ = http_app
    client = TestClient(app)
    r = client.post(
        "/mcp-server/mcp",
        headers={"authorization": "Bearer secret"},
        json={"method": "tools/call", "arguments": {}},
    )
    assert r.status_code == 422
