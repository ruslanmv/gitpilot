# mcp-inspector-server (mirror)

MCP diagnostics server. Source: <https://github.com/ruslanmv/mcp-inspector-server>.

## Tools GitPilot calls

| Tool | Used by |
|------|---------|
| `inspector.ping_server` | Reviewer (smoke test) |
| `inspector.list_capabilities` | Explorer |
| `inspector.validate_tool_schema` | Reviewer |
| `inspector.run_contract_tests` | Reviewer |
| `inspector.batch_validate` | Reviewer (this branch's upgrade) |
| `inspector.generate_report` | Reviewer (PR comment payload) |
| `inspector.list_logs` | Reviewer (post-mortem) |

## Helpers in `gitpilot.mcp_plugin.agent_hooks`

- `reviewer_batch_validate(client, targets, include_contract_tests=...)`
