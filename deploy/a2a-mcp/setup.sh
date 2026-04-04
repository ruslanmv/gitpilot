#!/usr/bin/env bash
set -euo pipefail

# deploy/a2a-mcp/setup.sh
# Brings up a local/dev stack:
# - GitPilot backend (A2A enabled)
# - GitPilot frontend (optional)
# - MCP ContextForge gateway (minimal)
#
# Prereqs:
# - Docker + docker compose v2
# - Place ContextForge source at: deploy/a2a-mcp/mcp-context-forge
#   (e.g., unzip mcp-context-forge-main into that folder)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "./mcp-context-forge" ]]; then
  echo "ERROR: ./mcp-context-forge not found."
  echo "Place the ContextForge source in deploy/a2a-mcp/mcp-context-forge"
  exit 1
fi

if [[ ! -f "./.env.stack" ]]; then
  echo "Creating .env.stack from .env.stack.example"
  cp ./.env.stack.example ./.env.stack
  echo "IMPORTANT: Edit deploy/a2a-mcp/.env.stack and set strong secrets before production."
fi

echo "Starting stack..."
docker compose -f docker-compose.a2a-mcp.yml --env-file ./.env.stack up -d --build

echo ""
echo "Services:"
echo "- GitPilot backend:  http://localhost:8000   (A2A: /a2a/invoke and /a2a/v1/invoke)"
echo "- GitPilot frontend: http://localhost:5173"
echo "- ContextForge UI:   http://localhost:8080/admin"
echo "- ContextForge API:  http://localhost:8080  (behind nginx)"
echo ""
echo "Next: create an admin JWT token in the gateway, then run register_agent.sh"
