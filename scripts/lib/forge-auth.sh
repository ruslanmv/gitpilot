#!/usr/bin/env bash
# scripts/lib/forge-auth.sh
#
# Finding an MCP Context Forge, and authenticating with it.
#
# Two problems this solves, both of which used to be guessed at:
#
# 1. WHICH FORGE. HomePilot (and other stacks in the family) run their own
#    Context Forge. Starting a second one wastes a container, splits the tool
#    registry in two, and fights over :4444. If a healthy Forge is already
#    reachable we adopt it — and, just as importantly, we remember we do not
#    own it, so `make stop-mcp` never takes down someone else's gateway.
#
# 2. WHICH CREDENTIAL. Forge accepts a JWT (from an email login or minted from
#    the signing secret) or a database API token. It does NOT accept the JWT
#    *signing secret* as a bearer token — that is a secret, not a token.
#    Sending one produces "Invalid authentication credentials", and — this is
#    the counter-intuitive part — sending a bad token is *worse* than sending
#    none: Forge's anonymous path (AUTH_REQUIRED=false) is only reached when
#    there is no Authorization header at all. So we probe what this Forge
#    actually wants and send exactly that, including nothing.
#
# Everything here is read-only with respect to Forge: no upstream change is
# required, and no endpoint is called that a normal operator could not call.
#
# Source it; do not execute it:
#     source scripts/lib/forge-auth.sh

# ── configuration ───────────────────────────────────────────────────────────
# Three separate secrets, deliberately not one:
#   MCP_FORGE_JWT_SECRET     signs tokens; never sent anywhere
#   MCP_FORGE_ADMIN_PASSWORD used to *obtain* a token
#   MCP_FORGE_API_TOKEN      a token, and the only one we ever send
# MCP_AUTH_TOKEN is the historical single value; it is still honoured as the
# signing secret so existing .mcp.env files keep working.

FORGE_TOKEN_CACHE="${FORGE_TOKEN_CACHE:-.mcp/forge-token}"
FORGE_CURL_IMAGE="${FORGE_CURL_IMAGE:-curlimages/curl:8.10.1}"
FORGE_OWNED_CONTAINER="${FORGE_OWNED_CONTAINER:-gitpilot-mcp-context-forge}"

# Populated by forge_discover:
FORGE_URL=""          # base URL reachable from THIS host
FORGE_INTERNAL_URL="" # base URL reachable from inside the gitpilot-mcp network
FORGE_OWNERSHIP=""    # gitpilot | external | none
FORGE_AUTH_MODE=""    # anonymous | token
FORGE_TOKEN=""        # empty when anonymous

_fa_info() { printf '   %s\n' "$*"; }
_fa_step() { printf '🔹 %s\n' "$*"; }
_fa_ok()   { printf '✅ %s\n' "$*"; }
_fa_warn() { printf '⚠️  %s\n' "$*"; }

# ── HTTP helpers ────────────────────────────────────────────────────────────

# Status code only, from the host. Short timeouts: this runs on every start.
#
# curl already prints "000" via -w when it cannot connect, so an `|| echo 000`
# on the failure path would concatenate into "000000" — which is not equal to
# "000" and quietly defeats every `[[ $code == 000 ]]` check. Capture, then
# normalise, and let the substitution supply the default only when curl printed
# nothing at all.
forge_status() {
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' \
    --max-time "${FORGE_HTTP_TIMEOUT:-5}" "$@" 2>/dev/null) || true
  printf '%s' "${code:-000}"
}

# Body + status on the last line, from the host.
forge_request() {
  curl -sS -o - -w '\n%{http_code}' --max-time "${FORGE_HTTP_TIMEOUT:-10}" "$@" 2>/dev/null || printf '\n000'
}

forge_healthy() {
  [[ "$(forge_status "${1%/}/health")" == "200" ]]
}

# ── 1. which Forge ──────────────────────────────────────────────────────────

# Is the reachable Forge the container we start ourselves?
forge_is_ours() {
  command -v docker >/dev/null 2>&1 || return 1
  local running
  running=$(docker ps --filter "name=^/${FORGE_OWNED_CONTAINER}$" --format '{{.Names}}' 2>/dev/null || true)
  [[ -n "${running}" ]]
}

# Candidate URLs, most explicit first. MCP_FORGE_URL is an operator's decision
# and is never second-guessed; the rest are the conventional local addresses a
# sibling stack would be on.
forge_candidates() {
  local port="${MCP_FORGE_PORT:-4444}"
  [[ -n "${MCP_FORGE_URL:-}" ]] && printf '%s\n' "${MCP_FORGE_URL%/}"
  printf '%s\n' "http://localhost:${port}"
  [[ "${port}" != "4444" ]] && printf '%s\n' "http://localhost:4444"
  printf '%s\n' "http://127.0.0.1:${port}"
}

# Find a Forge and decide whether it is ours to manage.
# Sets FORGE_URL and FORGE_OWNERSHIP; returns 1 when nothing is reachable.
forge_discover() {
  local url
  while read -r url; do
    [[ -z "${url}" ]] && continue
    if forge_healthy "${url}"; then
      FORGE_URL="${url}"
      if forge_is_ours; then
        FORGE_OWNERSHIP="gitpilot"
      else
        FORGE_OWNERSHIP="external"
      fi
      return 0
    fi
  done < <(forge_candidates)
  FORGE_URL=""
  FORGE_OWNERSHIP="none"
  return 1
}

# How our MCP servers must advertise themselves *to Forge*.
#
# A Forge we start shares the gitpilot-mcp network, so service names resolve.
# An adopted Forge lives somewhere else entirely, and would get a connection
# error from "http://mcp-postgre-server:8080" — it must be given a published
# host address instead. Registering a URL the gateway cannot reach is the
# quiet failure this avoids: registration returns 201 and every later call 502s.
forge_advertise_host() {
  if [[ -n "${MCP_ADVERTISE_HOST:-}" ]]; then
    printf '%s' "${MCP_ADVERTISE_HOST}"
  elif [[ "${FORGE_OWNERSHIP}" == "external" ]]; then
    # Reaches the host from inside a container on Docker Desktop, Rancher and
    # WSL2; on native Linux, Compose adds it via extra_hosts: host-gateway.
    printf '%s' "host.docker.internal"
  else
    printf '%s' ""   # same network: service names are correct and stable
  fi
}

# ── 2. which credential ─────────────────────────────────────────────────────

# What does this Forge accept? Asked, not assumed.
#
# GET /gateways with no Authorization header:
#   200 → anonymous access is on (AUTH_REQUIRED=false) → send NO header
#   401/403/302 → a credential is required
forge_probe_auth() {
  local url="${1:-${FORGE_URL}}"
  local code
  code=$(forge_status -H 'Accept: application/json' "${url%/}/gateways")
  case "${code}" in
    200) FORGE_AUTH_MODE="anonymous"; return 0 ;;
    401|403|302) FORGE_AUTH_MODE="token"; return 0 ;;
    *) FORGE_AUTH_MODE="token"; return 1 ;;   # unknown: assume the safe side
  esac
}

# Does this token actually work? The only definition that matters.
forge_token_works() {
  local url="${1}" token="${2}"
  [[ -z "${token}" ]] && return 1
  [[ "$(forge_status -H "Authorization: Bearer ${token}" \
        -H 'Accept: application/json' "${url%/}/gateways")" == "200" ]]
}

_forge_cached_token() {
  [[ -r "${FORGE_TOKEN_CACHE}" ]] && tr -d '[:space:]' < "${FORGE_TOKEN_CACHE}"
}

_forge_cache_token() {
  local token="$1"
  [[ -z "${token}" ]] && return 0
  mkdir -p "$(dirname "${FORGE_TOKEN_CACHE}")"
  # Owner-only: this is a live credential, not a config value.
  ( umask 077; printf '%s\n' "${token}" > "${FORGE_TOKEN_CACHE}" )
}

# Exchange the admin password for a JWT (Forge's own login endpoint).
forge_login() {
  local url="${1:-${FORGE_URL}}"
  local email="${MCP_FORGE_ADMIN_EMAIL:-${PLATFORM_ADMIN_EMAIL:-admin@example.com}}"
  local password="${MCP_FORGE_ADMIN_PASSWORD:-${PLATFORM_ADMIN_PASSWORD:-}}"
  [[ -z "${password}" ]] && return 1

  local out code body
  out=$(forge_request -X POST "${url%/}/auth/email/login" \
        -H 'Content-Type: application/json' \
        --data-binary @- <<JSON
{"email": "${email}", "password": "${password}"}
JSON
  )
  code="${out##*$'\n'}"
  body="${out%$'\n'*}"
  [[ "${code}" != "200" ]] && return 1

  # access_token, without requiring jq to be installed.
  local token
  token=$(printf '%s' "${body}" | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [[ -z "${token}" ]] && return 1
  printf '%s' "${token}"
}

# Resolve the credential to send. Sets FORGE_TOKEN (possibly empty) and
# FORGE_AUTH_MODE. Returns 1 only when a credential is needed and none works.
forge_resolve_auth() {
  local url="${1:-${FORGE_URL}}"
  FORGE_TOKEN=""

  forge_probe_auth "${url}"
  if [[ "${FORGE_AUTH_MODE}" == "anonymous" ]]; then
    # Deliberate: with auth off, the *absence* of the header is what works.
    return 0
  fi

  # 1. An operator-supplied token wins — it is the production path.
  if [[ -n "${MCP_FORGE_API_TOKEN:-}" ]] && forge_token_works "${url}" "${MCP_FORGE_API_TOKEN}"; then
    FORGE_TOKEN="${MCP_FORGE_API_TOKEN}"
    return 0
  fi

  # 2. A cached token from an earlier login, if it is still accepted.
  local cached
  cached=$(_forge_cached_token || true)
  if [[ -n "${cached}" ]] && forge_token_works "${url}" "${cached}"; then
    FORGE_TOKEN="${cached}"
    return 0
  fi

  # 3. Log in and cache the result.
  local fresh
  fresh=$(forge_login "${url}" || true)
  if [[ -n "${fresh}" ]] && forge_token_works "${url}" "${fresh}"; then
    FORGE_TOKEN="${fresh}"
    _forge_cache_token "${fresh}"
    return 0
  fi

  return 1
}

# The curl args carrying the credential — an empty array when anonymous, so
# no header is sent at all.
forge_auth_args() {
  if [[ -n "${FORGE_TOKEN}" ]]; then
    printf '%s\n' "-H" "Authorization: Bearer ${FORGE_TOKEN}"
  fi
}

# One line a human can act on, whatever the outcome.
forge_explain_auth_failure() {
  _fa_warn "Forge requires a credential and none of the available ones worked."
  _fa_info "Fix any one of these:"
  _fa_info "  • set MCP_FORGE_API_TOKEN in .mcp.env to a Forge API token"
  _fa_info "    (Forge UI → Tokens, or: docker compose exec mcp-context-forge \\"
  _fa_info "     python3 -m mcpgateway.utils.create_jwt_token -u admin --secret \$JWT_SECRET_KEY)"
  _fa_info "  • set MCP_FORGE_ADMIN_EMAIL / MCP_FORGE_ADMIN_PASSWORD to a real Forge login"
  _fa_info "  • or run Forge with AUTH_REQUIRED=false for local development"
  _fa_info "Note: the JWT signing secret is NOT a bearer token; Forge will reject it."
}


# ── target reachability ─────────────────────────────────────────────────────
#
# Registering a gateway is not a database insert: Forge dials the URL and
# performs an MCP `initialize` handshake before accepting it. A failure comes
# back as a flat `503 {"message":"Unable to connect to gateway"}` with no clue
# whether the server is down, on a different path, or speaking another
# transport. So we answer that question ourselves, first, and say which it was.

#: Paths an MCP server might serve, most conventional first, paired with the
#: transport Forge should be told to use.
MCP_ENDPOINT_CANDIDATES=(
  "/mcp:STREAMABLEHTTP"
  "/mcp/:STREAMABLEHTTP"
  "/sse:SSE"
  "/:STREAMABLEHTTP"
)

_MCP_INITIALIZE='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"gitpilot-preflight","version":"1"}}}'

# Wait until a server answers on its published host port. Returns 1 on timeout.
mcp_wait_for_server() {
  local base="$1" seconds="${2:-45}"
  local tries=$(( seconds / 3 ))
  for (( i = 0; i < tries; i++ )); do
    local code
    code=$(forge_status "${base%/}/health")
    # Any HTTP answer means the process is up; /health may legitimately 404.
    [[ "${code}" != "000" ]] && return 0
    sleep 3
  done
  return 1
}

# Which path does this server actually speak MCP on?
#
# Probes with the same `initialize` call Forge will make, so a path that passes
# here is a path that will pass there. Prints "<path> <transport>"; returns 1
# when nothing answered.
mcp_discover_endpoint() {
  local base="$1"
  local entry path transport code
  for entry in "${MCP_ENDPOINT_CANDIDATES[@]}"; do
    path="${entry%%:*}"
    transport="${entry##*:}"
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
      -X POST "${base%/}${path}" \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      --data-binary "${_MCP_INITIALIZE}" 2>/dev/null || echo "000")
    case "${code}" in
      # 2xx: it spoke. 400/406/415: it is an MCP endpoint that dislikes our
      # probe's shape — still the right path, and Forge's own client will do
      # the handshake properly.
      2??|400|406|415)
        printf '%s %s' "${path}" "${transport}"
        return 0
        ;;
    esac
  done
  return 1
}
