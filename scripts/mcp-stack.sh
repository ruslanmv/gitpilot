#!/usr/bin/env bash
# scripts/mcp-stack.sh — bring the MCP stack up, take it down, or report on it.
#
#     scripts/mcp-stack.sh up      start what is missing, reuse what is running
#     scripts/mcp-stack.sh down    stop only what we started
#     scripts/mcp-stack.sh status  say what is running and who owns it
#
# The one idea behind all three verbs: **adopt, don't duplicate**.
#
# HomePilot runs its own MCP Context Forge. So do several sibling stacks. A
# second gateway on the same machine is not redundancy — it splits the tool
# registry in two, so half your servers are invisible from whichever UI you
# happen to be looking at, and both instances fight over :4444.
#
# So: if a healthy Forge is already reachable, we register against it and start
# only our own MCP servers. And because we did not start it, we never stop it —
# `down` removes only containers declared in our compose file, which is a
# structural guarantee rather than a promise.
#
# Best practices applied:
#   * Adopt-don't-duplicate    — one gateway per machine, one registry
#   * Ownership is explicit    — reported on every verb, enforced on `down`
#   * Idempotent               — re-running `up` converges, never errors
#   * Fail with the next step  — every failure names what to do about it

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
skip() { printf '➖ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

COMPOSE=(docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp)

load_env() {
  [[ -f .mcp.env ]] || die ".mcp.env missing. Run 'make install-mcp' first."
  # shellcheck disable=SC1091
  set -a; source .mcp.env; set +a
  # shellcheck source=scripts/lib/forge-auth.sh
  source "${SCRIPT_DIR}/lib/forge-auth.sh"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is required for the MCP stack. Or run without it: make run-bare"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required. Or run without MCP: make run-bare"
  docker info >/dev/null 2>&1 || die "The Docker daemon is not running. Start Docker Desktop, then rerun."
}

wait_for_forge() {
  local url="$1" seconds="${2:-120}"
  local tries=$(( seconds / 2 ))
  for (( i = 1; i <= tries; i++ )); do
    if forge_healthy "${url}"; then
      ok "MCP Context Forge reachable after $(( i * 2 ))s."
      return 0
    fi
    sleep 2
  done
  return 1
}

# ── up ──────────────────────────────────────────────────────────────────────

cmd_up() {
  load_env
  require_docker

  # Only OUR servers, without pulling in the forge dependency: --no-deps is
  # exactly the switch for "the thing I depend on already exists".
  local our_servers=(mcp-postgre-db mcp-postgre-server mcp-inspector-server)

  if forge_discover && [[ "${FORGE_OWNERSHIP}" == "external" ]]; then
    bold "♻️  Reusing the MCP Context Forge already running at ${FORGE_URL}"
    info "Started outside GitPilot (HomePilot, or your own) — 'make stop-mcp' leaves it running."
    info "Set MCP_FORGE_URL in .mcp.env to pin a specific gateway, or MCP_FORGE_ADOPT=never to always start our own."
    if [[ "${MCP_FORGE_ADOPT:-auto}" == "never" ]]; then
      skip "MCP_FORGE_ADOPT=never — starting GitPilot's own Forge anyway."
    else
      echo "🚀 Starting GitPilot's MCP servers against it..."
      MCP_FORGE_URL="${FORGE_URL}" "${COMPOSE[@]}" up -d --no-deps "${our_servers[@]}" \
        || die "Could not start the MCP servers. Tail logs with: make logs-mcp"
      cmd_report
      return 0
    fi
  fi

  echo "🚀 Starting MCP Context Forge stack..."
  "${COMPOSE[@]}" up -d || die "Could not start the MCP stack. Tail logs with: make logs-mcp"

  local url="http://localhost:${MCP_FORGE_PORT:-4444}"
  echo "⏳ Waiting for MCP Context Forge on ${url}/health..."
  wait_for_forge "${url}" 120 \
    || die "MCP Context Forge did not become host-reachable on ${url}. Tail logs with: make logs-mcp"

  forge_discover >/dev/null 2>&1 || true
  cmd_report
}

# ── down ────────────────────────────────────────────────────────────────────

cmd_down() {
  [[ -f .mcp.env ]] || { skip "No .mcp.env; nothing to stop."; return 0; }
  load_env

  # `down` can only remove services declared in our compose file, so an adopted
  # Forge is safe by construction. Check first, so we can say so out loud —
  # "MCP stack stopped" would otherwise read as "the gateway is gone".
  local external=""
  if forge_discover 2>/dev/null && [[ "${FORGE_OWNERSHIP}" == "external" ]]; then
    external="${FORGE_URL}"
  fi

  "${COMPOSE[@]}" down 2>/dev/null || true
  [[ -n "${external}" ]] && skip "Left the externally-started Forge at ${external} running (not ours to stop)."
  echo "🛑 MCP stack stopped (volumes kept). 'make uninstall-mcp' to remove data."
}

# ── status ──────────────────────────────────────────────────────────────────

cmd_report() {
  if [[ -z "${FORGE_URL}" ]]; then
    forge_discover >/dev/null 2>&1 || true
  fi
  case "${FORGE_OWNERSHIP}" in
    gitpilot) ok "Forge: ${FORGE_URL} (started by GitPilot)" ;;
    external) ok "Forge: ${FORGE_URL} (reused — started elsewhere)" ;;
    *)        warn "Forge: not reachable" ;;
  esac
  info "Postgre:   http://localhost:${MCP_POSTGRE_PORT:-8080}"
  info "Inspector: http://localhost:${MCP_INSPECTOR_PORT:-8081}"
  info "Milvus (opt-in): docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile milvus up -d"
}

cmd_status() {
  load_env
  bold "🔎 MCP stack status"
  cmd_report
  if [[ -n "${FORGE_URL}" ]]; then
    if forge_resolve_auth "${FORGE_URL}"; then
      case "${FORGE_AUTH_MODE}" in
        anonymous) info "Auth: disabled on this Forge (no credential sent)" ;;
        token)     info "Auth: authenticated" ;;
      esac
    else
      warn "Auth: no working credential — run 'make register-mcp-servers' after fixing it."
      forge_explain_auth_failure
    fi
  fi
}

case "${1:-status}" in
  up)     cmd_up ;;
  down)   cmd_down ;;
  status) cmd_status ;;
  *)      die "usage: $(basename "$0") {up|down|status}" ;;
esac
