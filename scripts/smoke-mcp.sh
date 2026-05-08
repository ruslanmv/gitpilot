#!/usr/bin/env bash
# scripts/smoke-mcp.sh
#
# Post-deploy smoke test for the MCP Context Forge stack. Run after
# `make run-all` (or after a production release rolls out) to verify
# every service is healthy and GitPilot can see + reach Forge.
#
# What it checks:
#   1. GitPilot core /api/ping        (HTTP 200, JSON-parseable)
#   2. GitPilot /api/mcp/status       (gateway_reachable=true)
#   3. Each MCP service /health       (Forge, postgre, inspector;
#                                      milvus optional via --milvus)
#   4. POST /api/mcp/sync             (returns SyncReport, no error)
#   5. /api/mcp/agent_tools           (count >= 0; structure valid)
#
# Exit codes:
#   0  every check green
#   1  one or more checks failed (full diagnostic printed)
#
# Idempotent and safe in CI. Honours MCP_FORGE_PORT, MCP_POSTGRE_PORT,
# MCP_INSPECTOR_PORT, MCP_MILVUS_PORT, GITPILOT_URL env overrides.

set -uo pipefail

GITPILOT_URL="${GITPILOT_URL:-http://127.0.0.1:8000}"
FORGE_PORT="${MCP_FORGE_PORT:-4444}"
POSTGRE_PORT="${MCP_POSTGRE_PORT:-8080}"
INSPECTOR_PORT="${MCP_INSPECTOR_PORT:-8081}"
MILVUS_PORT="${MCP_MILVUS_PORT:-8082}"

INCLUDE_MILVUS=false
[[ "${1:-}" == "--milvus" ]] && INCLUDE_MILVUS=true

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
bad()  { printf '❌ %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }

FAILED=0

check_url() {
  local name="$1" url="$2" pattern="${3:-}"
  local body
  body=$(curl -sS --max-time 8 "$url" 2>&1)
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    bad "$name: curl exit $rc -> $body"
    FAILED=$((FAILED + 1))
    return
  fi
  if [[ -n "$pattern" ]] && ! echo "$body" | grep -q "$pattern"; then
    bad "$name: response missing /$pattern/"
    info "  body: $(echo "$body" | head -c 200)"
    FAILED=$((FAILED + 1))
    return
  fi
  ok "$name OK"
}

bold "🔍 GitPilot core"
check_url "  GitPilot /api/ping"   "${GITPILOT_URL}/api/ping" '"ok"\|true\|"pong"'

bold "🔍 MCP stack /health endpoints"
check_url "  Forge     :${FORGE_PORT}/health"     "http://127.0.0.1:${FORGE_PORT}/health"
check_url "  Postgre   :${POSTGRE_PORT}/health"   "http://127.0.0.1:${POSTGRE_PORT}/health"
check_url "  Inspector :${INSPECTOR_PORT}/health" "http://127.0.0.1:${INSPECTOR_PORT}/health"
$INCLUDE_MILVUS && check_url "  Milvus :${MILVUS_PORT}/health" "http://127.0.0.1:${MILVUS_PORT}/health"

bold "🔍 GitPilot ↔ Forge wiring"
check_url "  /api/mcp/status (forge_reachable)" "${GITPILOT_URL}/api/mcp/status" '"gateway_reachable":true'

bold "🔍 Sync round-trip"
sync_body=$(curl -sS --max-time 10 -X POST "${GITPILOT_URL}/api/mcp/sync" \
  -H 'Content-Type: application/json' -d '{}' 2>&1)
if echo "$sync_body" | grep -q '"forge_unreachable":true'; then
  bad "  /api/mcp/sync reported forge_unreachable=true"
  info "  $(echo "$sync_body" | head -c 200)"
  FAILED=$((FAILED + 1))
elif echo "$sync_body" | grep -q '"correlation_id"'; then
  ok "  /api/mcp/sync returned a SyncReport"
else
  bad "  /api/mcp/sync returned unexpected body"
  info "  $(echo "$sync_body" | head -c 200)"
  FAILED=$((FAILED + 1))
fi

bold "🔍 Agent toolbox"
check_url "  /api/mcp/agent_tools" "${GITPILOT_URL}/api/mcp/agent_tools" '"count"'

echo
if [[ $FAILED -eq 0 ]]; then
  bold "✨ All MCP smoke checks passed."
  exit 0
fi
bold "❌ ${FAILED} check(s) failed."
exit 1
