# gitpilot/mcp_client.py
"""Model Context Protocol (MCP) client for GitPilot.

Connects to MCP servers (databases, Slack, Figma, Sentry, etc.) and
exposes their tools to GitPilot agents.  Supports three transport types:

- **stdio**  — launch a local subprocess and communicate via stdin/stdout
- **http**   — send JSON-RPC requests over HTTP
- **sse**    — Server-Sent Events streaming connection

Configuration lives in ``.gitpilot/mcp.json``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MCP_CONFIG_FILE = "mcp.json"
MCP_JSONRPC_VERSION = "2.0"


class TransportType(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: TransportType
    # stdio
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    # http / sse
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    # auth
    auth_token: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        transport = TransportType(data.get("type", data.get("transport", "stdio")))
        env = {}
        for k, v in data.get("env", {}).items():
            # Expand $ENV_VAR references
            env[k] = os.path.expandvars(v) if isinstance(v, str) else v
        return cls(
            name=data["name"],
            transport=transport,
            command=data.get("command"),
            args=[os.path.expandvars(a) for a in data.get("args", [])],
            env=env,
            url=data.get("url"),
            headers=data.get("headers", {}),
            auth_token=os.path.expandvars(data["auth_token"]) if data.get("auth_token") else None,
        )


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPConnection:
    """An active connection to an MCP server."""

    config: MCPServerConfig
    tools: List[MCPTool] = field(default_factory=list)
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _request_id: int = field(default=0, repr=False)

    @property
    def is_alive(self) -> bool:
        if self.config.transport == TransportType.STDIO:
            return self._process is not None and self._process.returncode is None
        return True  # HTTP/SSE are stateless per-request

    def next_id(self) -> int:
        self._request_id += 1
        return self._request_id


class MCPClient:
    """Connect to MCP servers and call their tools.

    Usage::

        client = MCPClient()
        client.load_config(workspace / ".gitpilot")
        conn = await client.connect("postgres")
        tools = await client.list_tools(conn)
        result = await client.call_tool(conn, "query", {"sql": "SELECT 1"})
    """

    def __init__(self) -> None:
        self._configs: Dict[str, MCPServerConfig] = {}
        self._connections: Dict[str, MCPConnection] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def load_config(self, gitpilot_dir: Path) -> int:
        """Load MCP server configs from ``.gitpilot/mcp.json``.  Returns count."""
        config_path = gitpilot_dir / MCP_CONFIG_FILE
        if not config_path.exists():
            return 0
        try:
            data = json.loads(config_path.read_text())
            servers = data if isinstance(data, list) else data.get("servers", [])
            for entry in servers:
                cfg = MCPServerConfig.from_dict(entry)
                self._configs[cfg.name] = cfg
            logger.info("Loaded %d MCP server configs", len(servers))
            return len(servers)
        except Exception as e:
            logger.warning("Failed to load MCP config: %s", e)
            return 0

    def add_server(self, config: MCPServerConfig) -> None:
        self._configs[config.name] = config

    def list_servers(self) -> List[str]:
        return list(self._configs.keys())

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self, server_name: str) -> MCPConnection:
        """Connect to a named MCP server and discover its tools."""
        if server_name in self._connections and self._connections[server_name].is_alive:
            return self._connections[server_name]

        config = self._configs.get(server_name)
        if not config:
            raise ValueError(f"Unknown MCP server: {server_name}")

        conn = MCPConnection(config=config)

        if config.transport == TransportType.STDIO:
            await self._connect_stdio(conn)

        # Discover tools via initialize + tools/list
        await self._initialize(conn)
        conn.tools = await self.list_tools(conn)

        self._connections[server_name] = conn
        logger.info("Connected to MCP server '%s' — %d tools", server_name, len(conn.tools))
        return conn

    async def disconnect(self, server_name: str) -> None:
        conn = self._connections.pop(server_name, None)
        if conn and conn._process:
            conn._process.terminate()
            await conn._process.wait()

    async def disconnect_all(self) -> None:
        for name in list(self._connections):
            await self.disconnect(name)

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    async def list_tools(self, conn: MCPConnection) -> List[MCPTool]:
        """List tools available on the connected server."""
        result = await self._send_request(conn, "tools/list", {})
        tools = []
        for t in result.get("tools", []):
            tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=conn.config.name,
            ))
        return tools

    async def call_tool(
        self,
        conn: MCPConnection,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Call a tool on the connected server."""
        result = await self._send_request(conn, "tools/call", {
            "name": tool_name,
            "arguments": params or {},
        })
        # MCP returns content array; flatten text content
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else result

    def to_crewai_tools(self, conn: MCPConnection) -> list:
        """Wrap MCP tools as CrewAI-compatible tool functions.

        Returns a list of callables decorated with ``@tool``.
        """
        from crewai.tools import tool as crewai_tool

        wrapped = []
        for mcp_tool in conn.tools:
            # Capture in closure
            _conn = conn
            _name = mcp_tool.name
            _desc = mcp_tool.description or f"MCP tool: {_name}"

            @crewai_tool(_name)
            def _wrapper(params: str = "{}") -> str:
                __doc__ = _desc  # noqa: F841
                import asyncio as _aio
                loop = _aio.new_event_loop()
                try:
                    parsed = json.loads(params) if isinstance(params, str) else params
                    return str(loop.run_until_complete(
                        MCPClient.call_tool(self, _conn, _name, parsed)
                    ))
                finally:
                    loop.close()

            _wrapper.__doc__ = _desc
            wrapped.append(_wrapper)
        return wrapped

    # ------------------------------------------------------------------
    # Transport internals
    # ------------------------------------------------------------------

    async def _connect_stdio(self, conn: MCPConnection) -> None:
        config = conn.config
        if not config.command:
            raise ValueError(f"stdio server '{config.name}' requires a command")
        env = {**os.environ, **config.env}
        conn._process = await asyncio.create_subprocess_exec(
            config.command, *config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

    async def _initialize(self, conn: MCPConnection) -> Dict[str, Any]:
        return await self._send_request(conn, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "gitpilot", "version": "1.0"},
        })

    async def _send_request(
        self,
        conn: MCPConnection,
        method: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via the appropriate transport."""
        msg = {
            "jsonrpc": MCP_JSONRPC_VERSION,
            "id": conn.next_id(),
            "method": method,
            "params": params,
        }

        if conn.config.transport == TransportType.STDIO:
            return await self._send_stdio(conn, msg)
        else:
            return await self._send_http(conn, msg)

    async def _send_stdio(
        self, conn: MCPConnection, msg: dict,
    ) -> Dict[str, Any]:
        proc = conn._process
        if not proc or not proc.stdin or not proc.stdout:
            raise RuntimeError(f"stdio process not running for '{conn.config.name}'")
        payload = json.dumps(msg) + "\n"
        proc.stdin.write(payload.encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError(f"No response from MCP server '{conn.config.name}'")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})

    async def _send_http(
        self, conn: MCPConnection, msg: dict,
    ) -> Dict[str, Any]:
        url = conn.config.url
        if not url:
            raise ValueError(f"HTTP server '{conn.config.name}' requires a url")
        headers = {**conn.config.headers, "Content-Type": "application/json"}
        if conn.config.auth_token:
            headers["Authorization"] = f"Bearer {conn.config.auth_token}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=msg, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result", {})

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "servers": [
                {
                    "name": c.name,
                    "transport": c.transport.value,
                    "command": c.command,
                    "url": c.url,
                    "tools_count": len(self._connections[c.name].tools)
                    if c.name in self._connections else 0,
                    "connected": c.name in self._connections,
                }
                for c in self._configs.values()
            ]
        }
