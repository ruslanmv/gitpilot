"""Policy gates for MCP tool calls.

The Context Forge gateway enforces its own server-side policies, but we
also enforce them client-side: failing fast keeps a destructive tool
call from ever leaving the GitPilot process, and surfaces the policy
decision in the agent transcript.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from .config import MCPPluginSettings, get_settings

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Decides whether a given tool call is allowed.

    Three gates, in order:

    1. Server allowlist (``allowed_servers``).
    2. Hard-coded destructive keyword denylist (``drop``, ``delete``…).
    3. Mutation tools listed in :data:`MUTATION_TOOLS` need explicit
       approval when ``require_approval_for_mutations`` is set.
    """

    DESTRUCTIVE_KEYWORDS: tuple[str, ...] = (
        "drop",
        "delete",
        "truncate",
        "remove",
        "destroy",
        "clear",
        "purge",
    )

    # Tool-prefix → server-id mapping. Kept explicit because the server
    # name "mcp-postgre-server" does not match the tool prefix "postgres".
    PREFIX_TO_SERVER: ClassVar[dict[str, str]] = {
        "postgres": "mcp-postgre-server",
        "milvus": "mcp-milvus-server",
        "inspector": "mcp-inspector-server",
    }

    MUTATION_TOOLS: frozenset[str] = frozenset(
        {
            "postgres.execute_write",
            "postgres.unsafe_execute",
            "milvus.insert",
            "milvus.upsert",
            "milvus.create_collection",
            "milvus.drop_collection",
            "milvus.create_index",
            "milvus.drop_index",
        }
    )

    def __init__(self, settings: MCPPluginSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def is_allowed(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        prefix = tool_name.split(".", 1)[0] if "." in tool_name else ""
        server_id = self.PREFIX_TO_SERVER.get(prefix, "")
        if (
            self.settings.allowed_servers
            and server_id
            and server_id not in self.settings.allowed_servers
        ):
            return False, f"server {server_id!r} not in allowlist"

        lower = tool_name.lower()
        for kw in self.DESTRUCTIVE_KEYWORDS:
            if kw in lower:
                return False, f"destructive keyword {kw!r} in tool name"

        if (
            self.settings.require_approval_for_mutations
            and tool_name in self.MUTATION_TOOLS
        ):
            return False, "mutation tool requires explicit approval"

        return True, "ok"
