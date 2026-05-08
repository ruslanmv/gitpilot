"""Static catalog of MCP servers GitPilot ships with.

The actual tool list per server is discovered at runtime from Context
Forge (``ToolRegistry.discover``); this module just keeps the list of
servers we *expect* to attach so policy decisions and health checks can
reason about them without a network round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class ServerEntry:
    name: str
    description: str
    tags: tuple[str, ...]
    expected_tools: tuple[str, ...]


KNOWN_SERVERS: dict[str, ServerEntry] = {
    "mcp-postgre-server": ServerEntry(
        name="mcp-postgre-server",
        description="PostgreSQL schema discovery, safe SELECT, migration validation, fixtures.",
        tags=("postgresql", "database", "code-generation", "testing"),
        expected_tools=(
            "postgres.list_databases",
            "postgres.list_schemas",
            "postgres.list_tables",
            "postgres.describe_table",
            "postgres.safe_select",
            "postgres.validate_migration",
            "postgres.generate_test_fixtures",
            "postgres.generate_repository_context",
        ),
    ),
    "mcp-milvus-server": ServerEntry(
        name="mcp-milvus-server",
        description="Milvus collection discovery, vector search, RAG codegen, test vectors.",
        tags=("milvus", "vector-database", "rag", "code-generation"),
        expected_tools=(
            "milvus.list_collections",
            "milvus.describe_collection",
            "milvus.search",
            "milvus.validate_index_config",
            "milvus.generate_rag_pipeline_context",
            "milvus.generate_test_vectors",
        ),
    ),
    "mcp-inspector-server": ServerEntry(
        name="mcp-inspector-server",
        description="Validate, ping and contract-test other MCP servers.",
        tags=("mcp", "diagnostics"),
        expected_tools=(
            "inspector.ping_server",
            "inspector.list_capabilities",
            "inspector.validate_tool_schema",
            "inspector.run_contract_tests",
            "inspector.batch_validate",
            "inspector.generate_report",
        ),
    ),
}


@dataclass
class ToolRegistry:
    """Runtime cache of tools advertised by Context Forge."""

    tools: dict[str, dict] = field(default_factory=dict)

    def replace(self, tools: list[dict]) -> None:
        self.tools = {t["name"]: t for t in tools if "name" in t}

    def has(self, name: str) -> bool:
        return name in self.tools

    # Server-id → tool-prefix mapping (mirrors PolicyEngine.PREFIX_TO_SERVER).
    SERVER_TO_PREFIX: ClassVar[dict[str, str]] = {
        "mcp-postgre-server": "postgres",
        "mcp-milvus-server": "milvus",
        "mcp-inspector-server": "inspector",
    }

    def by_server(self, server_name: str) -> list[dict]:
        prefix = self.SERVER_TO_PREFIX.get(
            server_name,
            server_name.removeprefix("mcp-").removesuffix("-server"),
        )
        return [t for n, t in self.tools.items() if n.startswith(f"{prefix}.")]

    def by_tag(self, tag: str) -> list[dict]:
        return [
            t for t in self.tools.values() if tag in (t.get("tags") or [])
        ]
