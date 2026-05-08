"""Convenience helpers used directly by GitPilot's agents.

Each helper:

* swallows transport / policy errors and returns ``None`` so the agent
  can fall back to its previous behaviour;
* takes only the inputs the agent already has (table name, collection
  name, migration SQL);
* returns the parsed JSON content of the tool response, not the
  :class:`ToolResult` wrapper, so call sites stay short.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .forge_client import ForgeClientError, MCPContextForgeClient, ToolResult

logger = logging.getLogger(__name__)


def _unwrap(result: ToolResult) -> dict[str, Any] | None:
    if not result.success:
        logger.info("mcp tool %s failed: %s", result.tool_name, result.error)
        return None
    return result.content


@dataclass
class AgentMCPHooks:
    """Bind a forge client + agent name once, call helpers many times."""

    client: MCPContextForgeClient
    agent_name: str
    repository_path: str | None = None

    async def call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            result = await self.client.call_tool(
                tool_name,
                arguments,
                agent_name=self.agent_name,
                repository_path=self.repository_path,
            )
        except ForgeClientError as exc:
            logger.info("mcp call %s skipped: %s", tool_name, exc)
            return None
        return _unwrap(result)


# ---------------------------------------------------------------------------
# Coder agent
# ---------------------------------------------------------------------------
async def coder_describe_table(
    client: MCPContextForgeClient,
    table: str,
    schema: str = "public",
    *,
    agent_name: str = "coder",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "postgres.describe_table",
        {
            "schema": schema,
            "table": table,
            "include_indexes": True,
            "include_constraints": True,
        },
    )


async def coder_generate_repository_context(
    client: MCPContextForgeClient,
    table: str,
    schema: str = "public",
    language: str = "python",
    *,
    agent_name: str = "coder",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "postgres.generate_repository_context",
        {"schema": schema, "table": table, "language": language, "async_mode": True},
    )


async def coder_describe_collection(
    client: MCPContextForgeClient,
    collection: str,
    *,
    agent_name: str = "coder",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "milvus.describe_collection",
        {"collection": collection, "include_statistics": True},
    )


# ---------------------------------------------------------------------------
# Test runner agent
# ---------------------------------------------------------------------------
async def test_runner_fixtures(
    client: MCPContextForgeClient,
    table: str,
    schema: str = "public",
    num_rows: int = 10,
    seed: int = 42,
    *,
    agent_name: str = "test_runner",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "postgres.generate_test_fixtures",
        {
            "schema": schema,
            "table": table,
            "num_rows": num_rows,
            "format": "python_dict",
            "seed": seed,
        },
    )


async def test_runner_test_vectors(
    client: MCPContextForgeClient,
    dimension: int,
    num_vectors: int = 20,
    *,
    seed: int = 42,
    agent_name: str = "test_runner",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "milvus.generate_test_vectors",
        {
            "dimension": dimension,
            "num_vectors": num_vectors,
            "distribution": "normal",
            "seed": seed,
        },
    )


# ---------------------------------------------------------------------------
# Reviewer agent
# ---------------------------------------------------------------------------
async def reviewer_validate_migration(
    client: MCPContextForgeClient,
    migration_sql: str,
    *,
    dry_run: bool = False,
    agent_name: str = "reviewer",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "postgres.validate_migration",
        {
            "migration_sql": migration_sql,
            "check_syntax": True,
            "check_dependencies": True,
            "dry_run": dry_run,
        },
    )


async def reviewer_batch_validate(
    client: MCPContextForgeClient,
    targets: list[dict[str, Any]],
    *,
    include_contract_tests: bool = True,
    agent_name: str = "reviewer",
    repository_path: str | None = None,
) -> dict[str, Any] | None:
    """Ask the inspector to roll up health for every attached MCP server."""
    return await AgentMCPHooks(client, agent_name, repository_path).call(
        "inspector.batch_validate",
        {"targets": targets, "includeContractTests": include_contract_tests},
    )
