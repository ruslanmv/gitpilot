"""MCP Context Forge plugin for GitPilot.

This package wires GitPilot's agents to external MCP servers (PostgreSQL,
Milvus, Inspector) through a single MCP Context Forge gateway. The
existing :mod:`gitpilot.mcp_client` keeps providing low-level transport;
this package adds:

- a per-server registry that the agents consult by capability tag,
- a policy layer that blocks destructive tool calls before they reach
  Context Forge,
- thin agent hooks (``coder_describe_table``, ``test_runner_fixtures``,
  ``reviewer_validate_migration``) so agents don't need to know the
  exact tool names.

The plugin degrades gracefully: if ``GITPILOT_MCP_ENABLED`` is false or
the gateway is unreachable, calls return ``None`` and the agents fall
back to their previous behaviour.
"""

from .agent_hooks import (
    AgentMCPHooks,
    coder_describe_collection,
    coder_describe_table,
    coder_generate_repository_context,
    reviewer_batch_validate,
    reviewer_validate_migration,
    test_runner_fixtures,
    test_runner_test_vectors,
)
from .config import MCPPluginSettings, get_settings
from .forge_client import (
    ForgeClientError,
    MCPContextForgeClient,
    PolicyViolation,
    ToolResult,
)
from .policies import PolicyEngine
from .registry import KNOWN_SERVERS, ServerEntry, ToolRegistry

__all__ = [
    "AgentMCPHooks",
    "ForgeClientError",
    "KNOWN_SERVERS",
    "MCPContextForgeClient",
    "MCPPluginSettings",
    "PolicyEngine",
    "PolicyViolation",
    "ServerEntry",
    "ToolRegistry",
    "ToolResult",
    "coder_describe_collection",
    "coder_describe_table",
    "coder_generate_repository_context",
    "get_settings",
    "reviewer_batch_validate",
    "reviewer_validate_migration",
    "test_runner_fixtures",
    "test_runner_test_vectors",
]
