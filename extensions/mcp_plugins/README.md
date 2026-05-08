# MCP Context Forge plugins (mirrored from server repos)

This folder is the GitPilot-side mirror of the three MCP servers
GitPilot is wired to call through the MCP Context Forge gateway.

| Plugin | Upstream repo | Branch under development |
|--------|---------------|--------------------------|
| `postgre/` | https://github.com/ruslanmv/mcp-postgre-server | `claude/add-mcp-context-tools-xt3It` |
| `milvus/` | https://github.com/ruslanmv/milvus-admin-ui (`mcp-server/` subdir) | `claude/add-mcp-context-tools-xt3It` |
| `inspector/` | https://github.com/ruslanmv/mcp-inspector-server | `claude/add-mcp-context-tools-xt3It` |

Each subfolder contains:

- `register.json` — the same Context Forge registration manifest the
  upstream repo ships, copied here so GitPilot can validate it locally
  against `gitpilot.mcp_plugin.registry.KNOWN_SERVERS`.
- `README.md` — short summary of the tools the plugin exposes.

The actual server source code stays in the upstream repos. This mirror
is metadata-only so the GitPilot release artefact ships a self-describing
list of attachable plugins without bundling three separate Python/Node
codebases.

## Wiring it up

1. Bring up Context Forge with the three servers attached
   (see each upstream repo's `docker-compose.yml`).
2. Set the GitPilot env vars:

   ```bash
   GITPILOT_MCP_ENABLED=true
   GITPILOT_MCP_GATEWAY_URL=http://mcp-context-forge:4444/mcp
   GITPILOT_MCP_AUTH_TOKEN=<your token>
   GITPILOT_MCP_ALLOWED_SERVERS=mcp-postgre-server,mcp-milvus-server,mcp-inspector-server
   ```

3. Use the helpers in `gitpilot.mcp_plugin.agent_hooks` from your agents,
   e.g. `coder_describe_table`, `test_runner_fixtures`,
   `reviewer_validate_migration`, `reviewer_batch_validate`.
