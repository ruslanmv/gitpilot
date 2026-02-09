"""Tests for gitpilot.mcp_client module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.mcp_client import (
    MCPClient,
    MCPConnection,
    MCPServerConfig,
    MCPTool,
    TransportType,
)


# ---------------------------------------------------------------------------
# MCPServerConfig.from_dict
# ---------------------------------------------------------------------------


class TestMCPServerConfigFromDict:
    def test_minimal_stdio_config(self):
        data = {"name": "test-server", "type": "stdio", "command": "node", "args": ["server.js"]}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.name == "test-server"
        assert cfg.transport == TransportType.STDIO
        assert cfg.command == "node"
        assert cfg.args == ["server.js"]

    def test_http_config_with_url(self):
        data = {"name": "api-server", "transport": "http", "url": "https://example.com/mcp"}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.transport == TransportType.HTTP
        assert cfg.url == "https://example.com/mcp"

    def test_sse_transport(self):
        data = {"name": "sse-server", "type": "sse", "url": "https://example.com/sse"}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.transport == TransportType.SSE

    def test_defaults_to_stdio_when_no_type(self):
        data = {"name": "default-server", "command": "/usr/bin/myserver"}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.transport == TransportType.STDIO

    def test_env_expansion(self):
        data = {"name": "env-srv", "type": "stdio", "command": "srv", "env": {"KEY": "static"}}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.env["KEY"] == "static"

    def test_auth_token_present(self):
        data = {"name": "auth-srv", "type": "http", "url": "http://localhost", "auth_token": "tok123"}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.auth_token == "tok123"

    def test_auth_token_absent(self):
        data = {"name": "no-auth", "type": "http", "url": "http://localhost"}
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.auth_token is None

    def test_headers_present(self):
        data = {
            "name": "hdr-srv", "type": "http",
            "url": "http://localhost",
            "headers": {"X-Custom": "value"},
        }
        cfg = MCPServerConfig.from_dict(data)
        assert cfg.headers == {"X-Custom": "value"}


# ---------------------------------------------------------------------------
# MCPClient — configuration management
# ---------------------------------------------------------------------------


class TestMCPClientConfig:
    def test_load_config_from_json_array(self, tmp_path):
        gitpilot_dir = tmp_path / ".gitpilot"
        gitpilot_dir.mkdir()
        config = [
            {"name": "srv1", "type": "stdio", "command": "cmd1"},
            {"name": "srv2", "type": "http", "url": "http://host"},
        ]
        (gitpilot_dir / "mcp.json").write_text(json.dumps(config))

        client = MCPClient()
        count = client.load_config(gitpilot_dir)
        assert count == 2
        assert set(client.list_servers()) == {"srv1", "srv2"}

    def test_load_config_from_servers_key(self, tmp_path):
        gitpilot_dir = tmp_path / ".gitpilot"
        gitpilot_dir.mkdir()
        config = {"servers": [{"name": "only", "type": "stdio", "command": "x"}]}
        (gitpilot_dir / "mcp.json").write_text(json.dumps(config))

        client = MCPClient()
        count = client.load_config(gitpilot_dir)
        assert count == 1
        assert client.list_servers() == ["only"]

    def test_load_config_missing_file_returns_zero(self, tmp_path):
        client = MCPClient()
        count = client.load_config(tmp_path / "nonexistent")
        assert count == 0

    def test_load_config_invalid_json_returns_zero(self, tmp_path):
        gitpilot_dir = tmp_path / ".gitpilot"
        gitpilot_dir.mkdir()
        (gitpilot_dir / "mcp.json").write_text("not valid json{{{")

        client = MCPClient()
        count = client.load_config(gitpilot_dir)
        assert count == 0

    def test_add_server(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="manual", transport=TransportType.HTTP, url="http://x")
        client.add_server(cfg)
        assert "manual" in client.list_servers()

    def test_list_servers_empty(self):
        client = MCPClient()
        assert client.list_servers() == []


# ---------------------------------------------------------------------------
# MCPClient — connect / disconnect
# ---------------------------------------------------------------------------


class TestMCPClientConnect:
    async def test_connect_unknown_server_raises(self):
        client = MCPClient()
        with pytest.raises(ValueError, match="Unknown MCP server"):
            await client.connect("nonexistent")

    async def test_connect_stdio_launches_process(self):
        client = MCPClient()
        cfg = MCPServerConfig(
            name="stdio-srv", transport=TransportType.STDIO,
            command="node", args=["server.js"],
        )
        client.add_server(cfg)

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()

        # Mock initialize and tools/list responses
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n"
        tools_resp = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "query", "description": "Run SQL"},
        ]}}) + "\n"
        mock_proc.stdout.readline = AsyncMock(side_effect=[
            init_resp.encode(),
            tools_resp.encode(),
        ])
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            conn = await client.connect("stdio-srv")

        assert len(conn.tools) == 1
        assert conn.tools[0].name == "query"
        assert conn.tools[0].server_name == "stdio-srv"

    async def test_connect_http_discovers_tools(self):
        client = MCPClient()
        cfg = MCPServerConfig(
            name="http-srv", transport=TransportType.HTTP,
            url="https://example.com/mcp",
        )
        client.add_server(cfg)

        init_response = MagicMock()
        init_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {}}
        init_response.raise_for_status = MagicMock()

        tools_response = MagicMock()
        tools_response.json.return_value = {
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": [
                {"name": "fetch", "description": "Fetch URL", "inputSchema": {"type": "object"}},
            ]},
        }
        tools_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[init_response, tools_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            conn = await client.connect("http-srv")

        assert len(conn.tools) == 1
        assert conn.tools[0].name == "fetch"
        assert conn.tools[0].input_schema == {"type": "object"}

    async def test_disconnect_terminates_stdio_process(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="dc-srv", transport=TransportType.STDIO, command="node")
        client.add_server(cfg)

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()

        conn = MCPConnection(config=cfg, _process=mock_proc)
        client._connections["dc-srv"] = conn

        await client.disconnect("dc-srv")

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_awaited_once()
        assert "dc-srv" not in client._connections

    async def test_disconnect_nonexistent_is_noop(self):
        client = MCPClient()
        await client.disconnect("ghost")  # should not raise

    async def test_disconnect_all(self):
        client = MCPClient()
        cfg1 = MCPServerConfig(name="s1", transport=TransportType.HTTP, url="http://a")
        cfg2 = MCPServerConfig(name="s2", transport=TransportType.HTTP, url="http://b")
        client._connections["s1"] = MCPConnection(config=cfg1)
        client._connections["s2"] = MCPConnection(config=cfg2)

        await client.disconnect_all()
        assert len(client._connections) == 0


# ---------------------------------------------------------------------------
# MCPClient — call_tool
# ---------------------------------------------------------------------------


class TestMCPClientCallTool:
    async def test_call_tool_returns_text(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="tool-srv", transport=TransportType.HTTP, url="http://x")
        conn = MCPConnection(config=cfg)

        tool_response = MagicMock()
        tool_response.json.return_value = {
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [
                {"type": "text", "text": "Result line 1"},
                {"type": "text", "text": "Result line 2"},
            ]},
        }
        tool_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=tool_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            result = await client.call_tool(conn, "my_tool", {"key": "val"})

        assert result == "Result line 1\nResult line 2"

    async def test_call_tool_returns_raw_when_no_text(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="raw-srv", transport=TransportType.HTTP, url="http://x")
        conn = MCPConnection(config=cfg)

        tool_response = MagicMock()
        tool_response.json.return_value = {
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "image", "data": "abc"}]},
        }
        tool_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=tool_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            result = await client.call_tool(conn, "img_tool")

        # Should return the raw result dict since no text content found
        assert "content" in result


# ---------------------------------------------------------------------------
# MCPConnection
# ---------------------------------------------------------------------------


class TestMCPConnection:
    def test_is_alive_stdio_with_running_process(self):
        cfg = MCPServerConfig(name="alive", transport=TransportType.STDIO, command="x")
        proc = MagicMock()
        proc.returncode = None
        conn = MCPConnection(config=cfg, _process=proc)
        assert conn.is_alive is True

    def test_is_alive_stdio_no_process(self):
        cfg = MCPServerConfig(name="dead", transport=TransportType.STDIO, command="x")
        conn = MCPConnection(config=cfg)
        assert conn.is_alive is False

    def test_is_alive_stdio_exited_process(self):
        cfg = MCPServerConfig(name="exited", transport=TransportType.STDIO, command="x")
        proc = MagicMock()
        proc.returncode = 1
        conn = MCPConnection(config=cfg, _process=proc)
        assert conn.is_alive is False

    def test_is_alive_http_always_true(self):
        cfg = MCPServerConfig(name="http", transport=TransportType.HTTP, url="http://x")
        conn = MCPConnection(config=cfg)
        assert conn.is_alive is True

    def test_next_id_increments(self):
        cfg = MCPServerConfig(name="ids", transport=TransportType.HTTP, url="http://x")
        conn = MCPConnection(config=cfg)
        assert conn.next_id() == 1
        assert conn.next_id() == 2
        assert conn.next_id() == 3


# ---------------------------------------------------------------------------
# MCPClient — to_dict serialisation
# ---------------------------------------------------------------------------


class TestMCPClientToDict:
    def test_to_dict_no_connections(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="my-srv", transport=TransportType.HTTP, url="http://x")
        client.add_server(cfg)
        result = client.to_dict()
        assert len(result["servers"]) == 1
        srv = result["servers"][0]
        assert srv["name"] == "my-srv"
        assert srv["transport"] == "http"
        assert srv["connected"] is False
        assert srv["tools_count"] == 0

    def test_to_dict_with_active_connection(self):
        client = MCPClient()
        cfg = MCPServerConfig(name="active", transport=TransportType.STDIO, command="cmd")
        client.add_server(cfg)
        conn = MCPConnection(
            config=cfg,
            tools=[MCPTool(name="t1", description="desc", server_name="active")],
        )
        client._connections["active"] = conn
        result = client.to_dict()
        srv = result["servers"][0]
        assert srv["connected"] is True
        assert srv["tools_count"] == 1
