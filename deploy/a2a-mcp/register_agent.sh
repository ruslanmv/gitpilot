#!/usr/bin/env bash
set -euo pipefail

# deploy/a2a-mcp/register_agent.sh
#
# Registers GitPilot's A2A endpoint into ContextForge (Admin API),
# then creates a Virtual Server that exposes it to MCP clients.
#
# You need:
# - ContextForge running (setup.sh)
# - An admin JWT token for the gateway API
#
# Usage:
#   export CF_BASE_URL="http://localhost:8080"
#   export CF_ADMIN_BEARER="...jwt..."
#   export GITPILOT_A2A_URL="http://gitpilot-backend:8000/a2a/v1/invoke/"   # inside docker
#   export GITPILOT_A2A_SECRET="same as .env.stack"
#   ./register_agent.sh

CF_BASE_URL="${CF_BASE_URL:-http://localhost:8080}"
CF_ADMIN_BEARER="${CF_ADMIN_BEARER:-}"
GITPILOT_A2A_URL="${GITPILOT_A2A_URL:-http://gitpilot-backend:8000/a2a/v1/invoke/}"
GITPILOT_A2A_SECRET="${GITPILOT_A2A_SECRET:-}"

if [[ -z "$CF_ADMIN_BEARER" ]]; then
  echo "ERROR: CF_ADMIN_BEARER is required (admin JWT token)."
  exit 1
fi
if [[ -z "$GITPILOT_A2A_SECRET" ]]; then
  echo "ERROR: GITPILOT_A2A_SECRET is required."
  exit 1
fi

echo "Registering A2A agent in ContextForge..."
AGENT_JSON="$(curl -sS -X POST "$CF_BASE_URL/admin/a2a" \
  -H "Authorization: Bearer $CF_ADMIN_BEARER" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "gitpilot",
  "description": "GitPilot A2A agent (repo read/write + plan + execute)",
  "endpoint_url": "$GITPILOT_A2A_URL",
  "agent_type": "jsonrpc",
  "protocol_version": "1.0",
  "auth_type": "headers",
  "auth_headers": [
    { "X-A2A-Secret": "$GITPILOT_A2A_SECRET" }
  ],
  "tags": ["git", "codegen", "repo-edit"]
}
JSON
)"

echo "$AGENT_JSON" | python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))'

AGENT_ID="$(echo "$AGENT_JSON" | python -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id",""))')"
if [[ -z "$AGENT_ID" ]]; then
  echo "ERROR: Could not read agent id from response."
  exit 1
fi

echo ""
echo "Creating virtual server including this A2A agent..."
SERVER_JSON="$(curl -sS -X POST "$CF_BASE_URL/admin/servers" \
  -H "Authorization: Bearer $CF_ADMIN_BEARER" \
  -H "Content-Type: application/json" \
  -d @- <<JSON
{
  "name": "gitpilot_server",
  "description": "Virtual MCP server exposing GitPilot A2A tools",
  "associated_a2a_agents": ["$AGENT_ID"]
}
JSON
)"

echo "$SERVER_JSON" | python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))'

SERVER_ID="$(echo "$SERVER_JSON" | python -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id",""))')"
if [[ -z "$SERVER_ID" ]]; then
  echo "ERROR: Could not read server id from response."
  exit 1
fi

echo ""
echo "Done."
echo "MCP endpoint (Streamable HTTP) is usually:"
echo "  $CF_BASE_URL/servers/$SERVER_ID/mcp"
echo ""
echo "Tip: open admin UI:"
echo "  $CF_BASE_URL/admin"
