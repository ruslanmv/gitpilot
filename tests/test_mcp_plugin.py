"""Tests for gitpilot.mcp_plugin.

These tests do not need a running Context Forge gateway; they exercise
the policy engine, the registry, and the forge client wired against
``httpx.MockTransport``.
"""
from __future__ import annotations

import json

import httpx
import pytest

from gitpilot.mcp_plugin import (
    KNOWN_SERVERS,
    MCPContextForgeClient,
    MCPPluginSettings,
    PolicyEngine,
    PolicyViolation,
    ToolRegistry,
    coder_describe_table,
    reviewer_validate_migration,
)


def _settings(**overrides) -> MCPPluginSettings:
    base = dict(
        enabled=True,
        gateway_url="http://forge.test/mcp",
        auth_token="t0ken",
        allowed_servers=("mcp-postgre-server", "mcp-milvus-server", "mcp-inspector-server"),
        enable_during_code_generation=True,
        enable_during_test_generation=True,
        require_approval_for_mutations=True,
        max_calls_per_request=20,
        timeout_seconds=10,
        log_all_calls=False,
    )
    base.update(overrides)
    return MCPPluginSettings(**base)


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------
def test_policy_allows_safe_postgres_tools() -> None:
    p = PolicyEngine(_settings())
    ok, _ = p.is_allowed("postgres.describe_table", {"table": "users"})
    assert ok


@pytest.mark.parametrize(
    "tool",
    [
        "postgres.drop_table",
        "milvus.delete_collection",
        "postgres.truncate_users",
    ],
)
def test_policy_blocks_destructive_keywords(tool: str) -> None:
    p = PolicyEngine(_settings())
    ok, reason = p.is_allowed(tool, {})
    assert not ok
    assert "destructive" in reason or "blocked" in reason or "mutation" in reason


def test_policy_blocks_mutations_when_required() -> None:
    p = PolicyEngine(_settings(require_approval_for_mutations=True))
    ok, reason = p.is_allowed("postgres.execute_write", {"sql": "INSERT ..."})
    assert not ok
    assert "mutation" in reason


def test_policy_allows_mutations_when_approved() -> None:
    p = PolicyEngine(_settings(require_approval_for_mutations=False))
    ok, _ = p.is_allowed("milvus.insert", {})
    assert ok


def test_policy_blocks_non_allowlisted_servers() -> None:
    p = PolicyEngine(_settings(allowed_servers=("mcp-postgre-server",)))
    ok, reason = p.is_allowed("milvus.list_collections", {})
    assert not ok
    assert "allowlist" in reason


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_replace_and_lookup() -> None:
    reg = ToolRegistry()
    reg.replace(
        [
            {"name": "postgres.describe_table"},
            {"name": "milvus.search"},
            {"name": "inspector.batch_validate"},
        ]
    )
    assert reg.has("postgres.describe_table")
    assert not reg.has("postgres.unknown")
    assert len(reg.by_server("mcp-postgre-server")) == 1
    assert len(reg.by_server("mcp-milvus-server")) == 1


def test_known_servers_listing() -> None:
    assert "mcp-postgre-server" in KNOWN_SERVERS
    assert "postgres.generate_repository_context" in KNOWN_SERVERS[
        "mcp-postgre-server"
    ].expected_tools
    assert (
        "inspector.batch_validate"
        in KNOWN_SERVERS["mcp-inspector-server"].expected_tools
    )


# ---------------------------------------------------------------------------
# Forge client (via MockTransport)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forge_client_invokes_tool_and_returns_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tools/list"):
            return httpx.Response(200, json={"tools": [{"name": "postgres.describe_table"}]})
        if request.url.path.endswith("/tools/invoke"):
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "content": {"schema": "public", "table": "users", "columns": []},
                    "execution_time_ms": 12,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="http://forge.test/mcp",
        headers={"Authorization": "Bearer t0ken"},
    )
    client = MCPContextForgeClient(_settings(), http_client=http)
    await client.initialize()

    result = await client.call_tool(
        "postgres.describe_table",
        {"table": "users", "schema": "public"},
        agent_name="coder",
    )
    assert result.success
    assert result.content["table"] == "users"
    assert captured["body"]["tool"] == "postgres.describe_table"
    assert captured["body"]["context"]["agent"] == "coder"

    await client.close()


@pytest.mark.asyncio
async def test_forge_client_blocks_destructive_tool_locally() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"tools": []})
    )
    http = httpx.AsyncClient(transport=transport, base_url="http://forge.test/mcp")
    client = MCPContextForgeClient(_settings(), http_client=http)
    await client.initialize()
    with pytest.raises(PolicyViolation):
        await client.call_tool(
            "postgres.drop_table", {"table": "users"}, agent_name="coder"
        )
    await client.close()


@pytest.mark.asyncio
async def test_call_count_cap() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=(
                {"tools": []}
                if request.url.path.endswith("/tools/list")
                else {"success": True, "content": {}}
            ),
        )
    )
    http = httpx.AsyncClient(transport=transport, base_url="http://forge.test/mcp")
    client = MCPContextForgeClient(
        _settings(max_calls_per_request=2), http_client=http
    )
    await client.initialize()
    await client.call_tool("postgres.describe_table", {"table": "u"}, agent_name="coder")
    await client.call_tool("postgres.describe_table", {"table": "u"}, agent_name="coder")
    from gitpilot.mcp_plugin import ForgeClientError

    with pytest.raises(ForgeClientError):
        await client.call_tool(
            "postgres.describe_table", {"table": "u"}, agent_name="coder"
        )
    await client.close()


# ---------------------------------------------------------------------------
# Agent hooks (transport errors → None, never raise)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_hooks_swallow_transport_errors() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"error": "boom"})
    )
    http = httpx.AsyncClient(transport=transport, base_url="http://forge.test/mcp")
    client = MCPContextForgeClient(_settings(), http_client=http)
    await client.initialize()

    # describe_table returns None on transport failure
    result = await coder_describe_table(client, "users")
    assert result is None

    # validate_migration also returns None
    val = await reviewer_validate_migration(client, "ALTER TABLE users ADD c int")
    assert val is None
    await client.close()
