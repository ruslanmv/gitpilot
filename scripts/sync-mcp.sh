#!/usr/bin/env bash
# scripts/sync-mcp.sh
#
# Trigger /api/mcp/sync against a running GitPilot. Useful from CI, cron,
# or as a one-shot CLI ("make sync-mcp"). The endpoint itself is the
# canonical implementation; this script is a thin wrapper.

set -euo pipefail

GITPILOT_URL="${GITPILOT_URL:-http://127.0.0.1:8000}"

if ! curl -fsS "${GITPILOT_URL}/api/ping" >/dev/null 2>&1; then
  echo "❌ GitPilot is not running at ${GITPILOT_URL}. Run 'make run' first." >&2
  exit 1
fi

echo "🔁 Syncing MCP Context Forge registry into GitPilot..."
HTTP_CODE=$(curl -sS -o /tmp/sync-mcp.json -w "%{http_code}" \
  -X POST "${GITPILOT_URL}/api/mcp/sync" \
  -H "Content-Type: application/json" \
  -d '{}')

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "❌ Sync failed (HTTP ${HTTP_CODE})" >&2
  cat /tmp/sync-mcp.json >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  jq -r '"✅ Sync complete · added: \(.added|length) · refreshed: \(.kept|length) · orphaned: \(.orphaned|length)"' /tmp/sync-mcp.json
  jq . /tmp/sync-mcp.json
else
  cat /tmp/sync-mcp.json
fi
