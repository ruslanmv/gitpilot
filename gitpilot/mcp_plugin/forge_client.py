"""Async HTTP client for the MCP Context Forge gateway.

Built on :mod:`httpx`. The lower-level MCP transport (stdio/HTTP/SSE)
already lives in :mod:`gitpilot.mcp_client`; this client targets the
forge's REST surface (``/tools/list``, ``/tools/invoke``) which is what
Context Forge exposes for orchestrated multi-server access.

The client is intentionally narrow: discovery + invocation, with policy
enforcement and per-request call counting. Error handling is structured:
``ForgeClientError`` for transport/HTTP issues, ``PolicyViolation`` for
client-side policy denials.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .config import MCPPluginSettings, get_settings
from .policies import PolicyEngine
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


class ForgeClientError(RuntimeError):
    """Raised when the forge gateway returns an error or is unreachable."""


class PolicyViolation(ForgeClientError):
    """Raised when a tool call is blocked by the local policy engine."""


@dataclass
class ToolCall:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    agent_name: str
    repository_path: str | None
    timestamp: datetime


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    content: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    execution_time_ms: int = 0


class MCPContextForgeClient:
    """Talk to MCP Context Forge over HTTP."""

    def __init__(
        self,
        settings: MCPPluginSettings | None = None,
        *,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or ToolRegistry()
        self.policy = policy or PolicyEngine(self.settings)
        self._call_count = 0
        self._http: httpx.AsyncClient | None = http_client
        self._owns_http = http_client is None

    async def __aenter__(self) -> "MCPContextForgeClient":
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def initialize(self) -> None:
        """Open the HTTP client and discover the tool list (best-effort)."""
        if not self.settings.enabled:
            logger.info("mcp-plugin disabled via settings")
            return
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.settings.gateway_url,
                headers={
                    "Authorization": f"Bearer {self.settings.auth_token}",
                    "Content-Type": "application/json",
                    "User-Agent": "gitpilot-mcp-plugin/1.0",
                },
                timeout=self.settings.timeout_seconds,
            )
        try:
            await self.discover_tools()
        except Exception:  # noqa: BLE001
            logger.exception("tool discovery failed; plugin will degrade to no-op")

    async def close(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    async def discover_tools(self) -> list[dict]:
        if self._http is None:
            return []
        resp = await self._http.get("/tools/list")
        resp.raise_for_status()
        body = resp.json()
        tools = body.get("tools", body) if isinstance(body, dict) else []
        if not isinstance(tools, list):
            tools = []
        self.registry.replace(tools)
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        agent_name: str,
        repository_path: str | None = None,
    ) -> ToolResult:
        if not self.settings.enabled:
            raise ForgeClientError("mcp-plugin disabled")

        if self._call_count >= self.settings.max_calls_per_request:
            raise ForgeClientError(
                f"per-request call cap exceeded ({self.settings.max_calls_per_request})"
            )

        allowed, reason = self.policy.is_allowed(tool_name, arguments)
        if not allowed:
            raise PolicyViolation(f"tool {tool_name!r} blocked: {reason}")

        if self._http is None:
            raise ForgeClientError("client not initialised")

        call = ToolCall(
            id=str(uuid4()),
            tool_name=tool_name,
            arguments=arguments,
            agent_name=agent_name,
            repository_path=repository_path,
            timestamp=datetime.now(timezone.utc),
        )

        started = time.monotonic()
        try:
            resp = await self._http.post(
                "/tools/invoke",
                json={
                    "tool": tool_name,
                    "arguments": arguments,
                    "context": {
                        "agent": agent_name,
                        "repository": repository_path,
                        "call_id": call.id,
                    },
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning("forge invoke failed: %s", exc)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                content={},
                error=f"forge transport error: {exc}",
                execution_time_ms=elapsed_ms,
            )

        self._call_count += 1
        body = resp.json()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if self.settings.log_all_calls:
            logger.info(
                "mcp tool=%s agent=%s success=%s elapsed_ms=%d",
                tool_name,
                agent_name,
                body.get("success", True),
                elapsed_ms,
            )
        return ToolResult(
            tool_name=tool_name,
            success=bool(body.get("success", True)),
            content=body.get("content") or body,
            error=body.get("error"),
            execution_time_ms=int(body.get("execution_time_ms", elapsed_ms)),
        )

    @property
    def call_count(self) -> int:
        return self._call_count

    def reset_call_count(self) -> None:
        self._call_count = 0
