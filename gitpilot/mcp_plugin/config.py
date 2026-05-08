"""Settings for the Context Forge plugin.

All values are read from environment variables prefixed with
``GITPILOT_MCP_`` so they can be set without touching code, and so they
don't interact with the existing :mod:`gitpilot.mcp_client` settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MCPPluginSettings:
    enabled: bool = True
    gateway_url: str = "http://localhost:4444/mcp"
    auth_token: str = "change-me"
    allowed_servers: tuple[str, ...] = (
        "mcp-postgre-server",
        "mcp-milvus-server",
        "mcp-inspector-server",
    )
    enable_during_code_generation: bool = True
    enable_during_test_generation: bool = True
    require_approval_for_mutations: bool = True
    max_calls_per_request: int = 20
    timeout_seconds: int = 30
    log_all_calls: bool = True


@lru_cache
def get_settings() -> MCPPluginSettings:
    """Load settings once per process from the environment."""
    env = os.environ
    allowed = env.get("GITPILOT_MCP_ALLOWED_SERVERS", "")
    return MCPPluginSettings(
        enabled=_bool(env.get("GITPILOT_MCP_ENABLED", ""), True),
        gateway_url=env.get(
            "GITPILOT_MCP_GATEWAY_URL", "http://localhost:4444/mcp"
        ),
        auth_token=env.get("GITPILOT_MCP_AUTH_TOKEN", "change-me"),
        allowed_servers=(
            tuple(_csv(allowed))
            if allowed
            else MCPPluginSettings.__dataclass_fields__["allowed_servers"].default
        ),
        enable_during_code_generation=_bool(
            env.get("GITPILOT_MCP_ENABLE_DURING_CODE_GENERATION", ""), True
        ),
        enable_during_test_generation=_bool(
            env.get("GITPILOT_MCP_ENABLE_DURING_TEST_GENERATION", ""), True
        ),
        require_approval_for_mutations=_bool(
            env.get("GITPILOT_MCP_REQUIRE_APPROVAL_FOR_MUTATIONS", ""), True
        ),
        max_calls_per_request=int(env.get("GITPILOT_MCP_MAX_CALLS_PER_REQUEST", "20")),
        timeout_seconds=int(env.get("GITPILOT_MCP_TIMEOUT_SECONDS", "30")),
        log_all_calls=_bool(env.get("GITPILOT_MCP_LOG_ALL_CALLS", ""), True),
    )
