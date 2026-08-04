#!/usr/bin/env bash
# scripts/register-mcp-servers.sh
#
# After the MCP stack is up, the servers are running -- but Context Forge does
# not know they exist yet. This script federates them by POSTing to Forge's
# /gateways endpoint, so:
#
#   * the GitPilot UI's Sync button has something to pull,
#   * the inspector's batch_validate can route through Forge,
#   * 'forge transport error' on the per-server cards goes away.
#
# Two things it is careful about, both learned the hard way:
#
#   WHICH FORGE. A Forge started by HomePilot (or any sibling stack) is
#   adopted rather than duplicated. When we adopt one, the servers must be
#   registered by an address that Forge can actually reach: it is not on our
#   docker network, so "http://mcp-postgre-server:8080" would resolve to
#   nothing. Registering an unreachable URL succeeds and then fails forever
#   afterwards, which is the worst kind of failure.
#
#   WHICH CREDENTIAL. Forge is asked what it accepts instead of being handed a
#   guess. With AUTH_REQUIRED=false the correct request carries NO
#   Authorization header at all -- a bad token is worse than no token, because
#   Forge's anonymous path is only reached when the header is absent.
#
# Best practices applied:
#   * Adopt-don't-duplicate  — reuse a healthy Forge, never fight for :4444
#   * Preflight once         — one actionable error, not one per server
#   * Reachability-correct   — advertise the address Forge can dial
#   * Idempotent             — 409 Conflict treated as success
#   * Independent per server — one failure does not abort the others
#   * No secrets on argv     — credentials travel in env and stdin only

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

# shellcheck disable=SC1091
set -a
source .mcp.env
set +a

# shellcheck source=scripts/lib/forge-auth.sh
source "${SCRIPT_DIR}/lib/forge-auth.sh"

# --- 1. find a Forge -------------------------------------------------------
step "Looking for a running MCP Context Forge..."
READY=0
for _ in $(seq 1 60); do
  if forge_discover; then
    READY=1
    break
  fi
  sleep 2
done

if [[ "${READY}" -ne 1 ]]; then
  warn "No MCP Context Forge is reachable; skipping registration."
  info "Start one with 'make run-mcp', or point MCP_FORGE_URL at an existing gateway."
  exit 0
fi

case "${FORGE_OWNERSHIP}" in
  gitpilot) ok "Using GitPilot's own Forge at ${FORGE_URL}" ;;
  external) ok "Reusing the Forge already running at ${FORGE_URL} (not started by GitPilot)" ;;
esac

# --- 2. resolve the credential ONCE ----------------------------------------
if ! forge_resolve_auth "${FORGE_URL}"; then
  forge_explain_auth_failure
  info "Nothing was registered; the MCP servers are running and can be registered later"
  info "with 'make register-mcp-servers' once a credential is configured."
  exit 0
fi

if [[ "${FORGE_AUTH_MODE}" == "anonymous" ]]; then
  info "Forge has authentication disabled — registering without a credential."
else
  info "Authenticated with Forge."
fi

mapfile -t AUTH_ARGS < <(forge_auth_args)

# --- 3. work out how Forge can reach our servers ---------------------------
ADVERTISE_HOST="$(forge_advertise_host)"

if [[ -n "${ADVERTISE_HOST}" ]]; then
  info "Advertising servers to Forge as ${ADVERTISE_HOST}:<published port>."
fi

# --- 4. register ------------------------------------------------------------
#
# Forge does not merely record a gateway: it dials the URL and performs an MCP
# `initialize` handshake before accepting it. A failure comes back as a flat
# 503 "Unable to connect to gateway" that says nothing about which of the three
# possible causes it was. So each target is checked here first — is it up, and
# which path does it actually speak MCP on — and the answer is used, or
# reported.
register() {
  local name="$1" service="$2" internal_port="$3" host_port="$4" tags="$5"

  local host_base="http://localhost:${host_port}"
  if ! mcp_wait_for_server "${host_base}" "${MCP_SERVER_WAIT_SECONDS:-45}"; then
    warn "${name}: not answering on ${host_base} — skipping registration."
    info "It may still be starting. Check with: make logs-mcp"
    info "Then re-run: make register-mcp-servers"
    return
  fi

  # Ask the server which path speaks MCP rather than assuming /mcp.
  local path="/mcp" transport="STREAMABLEHTTP" discovered
  if discovered=$(mcp_discover_endpoint "${host_base}"); then
    path="${discovered%% *}"
    transport="${discovered##* }"
  else
    warn "${name}: no MCP endpoint answered on ${host_base} (tried /mcp, /mcp/, /sse, /)."
    info "Registering ${path} anyway; if Forge rejects it the server is not speaking MCP yet."
  fi

  # Same network → service name (stable, no host port needed).
  # Adopted Forge  → the published host port via the advertise host.
  local url
  if [[ -z "${ADVERTISE_HOST}" ]]; then
    url="http://${service}:${internal_port}${path}"
  else
    url="http://${ADVERTISE_HOST}:${host_port}${path}"
  fi

  step "Registering ${name} → ${url} (${transport})"

  local body
  body=$(cat <<JSON
{
  "name": "${name}",
  "url": "${url}",
  "description": "Auto-registered by gitpilot make run-mcp.",
  "transport": "${transport}",
  "tags": ${tags},
  "visibility": "public"
}
JSON
)

  local out code resp_body
  out=$(printf '%s' "${body}" | curl -sS -o - -w '\n%{http_code}' \
    --max-time 30 \
    -X POST "${FORGE_URL%/}/gateways" \
    "${AUTH_ARGS[@]}" \
    -H "Content-Type: application/json" \
    --data-binary @- 2>/dev/null || printf '\n000')
  code="${out##*$'\n'}"
  resp_body="${out%$'\n'*}"

  case "${code}" in
    200|201) ok "${name}: registered (HTTP ${code})." ;;
    409)     skip "${name}: already registered." ;;
    401|403)
      # Preflight passed, so this is a scope/permission issue, not a bad token.
      warn "${name}: rejected (HTTP ${code}) although the credential was accepted."
      info "The credential may lack permission to federate gateways."
      printf '%.240s\n' "${resp_body}" ;;
    503)
      # Forge reached us but the MCP handshake failed. We already know the
      # server is up and which path answered, so name the remaining causes.
      warn "${name}: Forge could not complete an MCP handshake with ${url}."
      if [[ -n "${ADVERTISE_HOST}" ]]; then
        info "Forge runs elsewhere and must reach this host as ${ADVERTISE_HOST}."
        info "If that name does not resolve inside it, set MCP_ADVERTISE_HOST to this host's LAN IP."
      else
        info "The server answered on ${host_base}${path} from here, so it is running;"
        info "Forge could not speak ${transport} to it. Check: make logs-mcp"
      fi ;;
    000)
      warn "${name}: no response from ${FORGE_URL}." ;;
    *)
      warn "${name}: unexpected HTTP ${code:-<empty>}."
      printf '%.240s\n' "${resp_body}" ;;
  esac
}

register "mcp-postgre-server" mcp-postgre-server 8080 "${MCP_POSTGRE_PORT:-8080}" \
  '["postgresql","database","code-generation","testing"]'
register "mcp-inspector-server" mcp-inspector-server 8081 "${MCP_INSPECTOR_PORT:-8081}" \
  '["mcp","diagnostics"]'
# Milvus is on an opt-in profile; only register it when it is actually running.
if [[ "$(forge_status "http://localhost:${MCP_MILVUS_PORT:-8082}/health")" != "000" ]]; then
  register "mcp-milvus-server" mcp-milvus-server 8082 "${MCP_MILVUS_PORT:-8082}" \
    '["milvus","vector-database","rag","code-generation"]'
else
  skip "mcp-milvus-server: not running (start it with the milvus profile)."
fi

echo
bold "✨ Forge registry sync complete."
info "Open GitPilot → Settings → MCP Servers → click 'Sync' to mirror locally."
exit 0
