# mcp-postgre-server (mirror)

Read-only PostgreSQL MCP server. Source: <https://github.com/ruslanmv/mcp-postgre-server>.

## Tools GitPilot calls

| Tool | Used by |
|------|---------|
| `postgres.list_databases` | Explorer |
| `postgres.list_schemas` | Explorer |
| `postgres.list_tables` | Explorer |
| `postgres.describe_table` | Coder, Test Runner, Reviewer |
| `postgres.safe_select` | Coder (sample data) |
| `postgres.explain_query` | Reviewer |
| `postgres.validate_migration` | Reviewer |
| `postgres.generate_test_fixtures` | Test Runner |
| `postgres.generate_repository_context` | Coder (this branch's upgrade) |

## Helpers in `gitpilot.mcp_plugin.agent_hooks`

- `coder_describe_table(client, table, schema=...)`
- `coder_generate_repository_context(client, table, language=...)`
- `test_runner_fixtures(client, table, num_rows=..., seed=...)`
- `reviewer_validate_migration(client, migration_sql, dry_run=...)`
