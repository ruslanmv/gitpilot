# gitpilot-mcp-server (self-registration)

GitPilot can run *as* an MCP server — letting other agents (notably a
HomePilot Coder persona) drive it through the same MCP Context Forge
gateway GitPilot uses to call **out** to other servers.

## Off by default

Mounted only when:

```bash
GITPILOT_EXPOSE_MCP_SERVER=true
GITPILOT_MCP_SERVER_TOKEN=<random-token>
# Optional, for write tools:
GITPILOT_MCP_SERVER_ALLOW_MUTATION=true
GITPILOT_MCP_SERVER_MUTATION_TOKEN=<separate-token>
```

The server lives on the same FastAPI app as GitPilot, mounted at
`/mcp-server/mcp` (overridable via `GITPILOT_MCP_SERVER_MOUNT_PATH`).

## Tool catalog

| Tool | Scope |
|------|-------|
| `gitpilot.healthz` | read |
| `gitpilot.list_repos` | read |
| `gitpilot.list_branches` | read |
| `gitpilot.describe_repo` | read |
| `gitpilot.list_skills` | read |
| `gitpilot.classify_topology` | read |
| `gitpilot.plan` | plan |
| `gitpilot.execute` | plan |
| `gitpilot.run_skill` | mutation |
| `gitpilot.create_pr` | mutation |

`read` and `plan` tools are non-destructive. `mutation` tools require
the separate mutation token *and* `ALLOW_MUTATION=true`.

## Recursion guard

If GitPilot's own agents ever reach back into this server (loop), the
incoming `X-Gitpilot-Origin: self` header is rejected with HTTP 409.

## HomePilot wizard

The `wizard_compatible: true` flag in `register.json` is the contract
HomePilot's wizard looks for. The wizard expects, in order:

1. `gitpilot.healthz` for the prerequisites step.
2. `gitpilot.list_repos` + `gitpilot.list_branches` for workspace pick.
3. `tool_policy` + `scopes` to render the permissions toggles.

See `gitpilot.mcp_server` for the data contract.
