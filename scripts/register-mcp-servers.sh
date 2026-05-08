#!/usr/bin/env bash
# scripts/register-mcp-servers.sh
#
# After 'make run-mcp' brings the compose stack up, the three MCP servers
# are running -- but Context Forge does not know they exist yet. This
# script federates them by POSTing to Forge's /gateways endpoint, so:
#
#   * the GitPilot UI's Sync button has something to pull,
#   * the inspector's batch_validate can route through Forge,
#   * 'forge transport error' on the per-server cards goes away.
#
# Strategy: run every probe + POST FROM INSIDE the docker network via a
# disposable `curlimages/curl` sidecar. This sidesteps every host-side
# port-mapping pitfall (Windows+WSL2 port-forward latency, IPv6 vs v4,
# `localhost` resolution quirks) and is the exact same pattern used by
# `compose run --no-deps` test harnesses in production stacks.
#
# Best practices applied:
#   * In-network probe          — bypass host port mapping entirely
#   * Wait-for-ready loop       — up to 120s (Forge first start can be slow)
#   * Idempotent                — 409 Conflict treated as success
#   * Independent per server    — one failure does not abort the others
#   * No secrets on argv        — token comes from .mcp.env via env vars
#   * Structured logs           — ✅ / ➖ / ⚠️ glyphs for at-a-glance status

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
step() { printf '🔹 %s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
skip() { printf '➖ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }

bold "🔗 Registering MCP servers with Context Forge"

if [[ ! -f .mcp.env ]]; then
  warn ".mcp.env not found. Run 'make install-mcp' first."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  warn "docker not found; cannot reach the gitpilot-mcp network."
  exit 0
fi

# shellcheck disable=SC1091
set -a
source .mcp.env
set +a

NETWORK="${MCP_NETWORK_NAME:-gitpilot-mcp}"
FORGE_HOST="${MCP_FORGE_SERVICE:-mcp-context-forge}"   # compose service name
FORGE_INTERNAL_PORT="4444"
TOKEN="${MCP_AUTH_TOKEN:-}"

if [[ -z "${TOKEN}" ]]; then
  warn "MCP_AUTH_TOKEN missing in .mcp.env; cannot authenticate with Forge."
  exit 0
fi

# One-shot curl on the docker network. Discards the response body (we
# only care about the status code for the readiness probe). No volume
# mounts -- avoids the WSL/Docker-Desktop bind-mount permission quirk.
forge_curl_status() {
  docker run --rm --network "${NETWORK}" \
    curlimages/curl:8.10.1 \
    -sS -o /dev/null -w "%{http_code}" "$@"
}

# Pre-pull the curl image once (silent on cache hit).
docker image pull --quiet curlimages/curl:8.10.1 >/dev/null 2>&1 || true

# --- wait for Forge --------------------------------------------------------
step "Probing Forge at http://${FORGE_HOST}:${FORGE_INTERNAL_PORT}/health from inside the ${NETWORK} network (up to 120s)..."
READY=0
for i in $(seq 1 60); do
  code=$(forge_curl_status \
    "http://${FORGE_HOST}:${FORGE_INTERNAL_PORT}/health" 2>/dev/null || true)
  if [[ "${code}" == "200" ]]; then
    ok "Forge healthy after $((i * 2))s."
    READY=1
    break
  fi
  sleep 2
done
if [[ "${READY}" -ne 1 ]]; then
  warn "Forge did not respond in 120s; skipping registration."
  info "Tail logs with: make logs-mcp"
  exit 0
fi

# --- register one gateway --------------------------------------------------
#
# JSON travels via STDIN, never via a bind mount. Docker Desktop on
# Windows + WSL2 has uid + filesystem-overlay quirks that make
# `-v /tmp/foo:/tmp/foo --data-binary @/tmp/foo` flaky inside curl
# containers (the file mounts but curl can't read it -- "error
# encountered when reading a file"). Stdin works on every Docker host
# the project supports. This is the same recipe the kubectl docs use
# for one-shot `kubectl exec ... curl --data @-` probes.
register() {
  local name="$1" container_url="$2" tags="$3"
  step "Registering ${name}..."

  local body
  body=$(cat <<JSON
{
  "name": "${name}",
  "url": "${container_url}",
  "description": "Auto-registered by gitpilot make run-mcp.",
  "transport": "STREAMABLEHTTP",
  "auth_type": "bearer",
  "auth_token": "${TOKEN}",
  "tags": ${tags},
  "visibility": "public"
}
JSON
)

  # `-i` keeps STDIN open in the container; curl reads the JSON via @-.
  # No volume mounts means no uid mismatch, no Windows-path translation.
  # `-w '\n%{http_code}'` appends the HTTP status on its own final line,
  # so we can split body and status with shell parameter expansion.
  local out
  out=$(printf '%s' "${body}" | docker run --rm -i \
    --network "${NETWORK}" \
    curlimages/curl:8.10.1 \
    -sS -o - -w '\n%{http_code}' \
    -X POST "http://${FORGE_HOST}:${FORGE_INTERNAL_PORT}/gateways" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary @-)
  local code resp_body
  code="${out##*$'\n'}"
  resp_body="${out%$'\n'*}"

  case "${code}" in
    200|201)
      ok "${name}: registered (HTTP ${code})." ;;
    409)
      skip "${name}: already registered." ;;
    401|403)
      warn "${name}: auth rejected (HTTP ${code}). Check MCP_AUTH_TOKEN matches Forge's."
      printf '%.240s\n' "${resp_body}" ;;
    *)
      warn "${name}: unexpected HTTP ${code:-<empty>}."
      printf '%.240s\n' "${resp_body}" ;;
  esac
}

# Containers reach each other by service name on the gitpilot-mcp network.
register "mcp-postgre-server"   "http://mcp-postgre-server:8080/mcp"   '["postgresql","database","code-generation","testing"]'
register "mcp-milvus-server"    "http://mcp-milvus-server:8082/mcp"    '["milvus","vector-database","rag","code-generation"]'
register "mcp-inspector-server" "http://mcp-inspector-server:8081/mcp" '["mcp","diagnostics"]'

echo
bold "✨ Forge registry sync complete."
info "Open GitPilot → Settings → MCP Servers → click 'Sync' to mirror locally."
exit 0
