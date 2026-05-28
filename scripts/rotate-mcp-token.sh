#!/usr/bin/env bash
# scripts/rotate-mcp-token.sh
#
# Full reset of the MCP auth token.  Used when ``make run-mcp``'s
# auto-recovery couldn't fix a 401 — typically because Forge
# persisted an older token in Postgres on its first boot, and just
# force-recreating the Forge container with the new env doesn't
# clear that persisted state.
#
# Three steps:
#   1. Generate a fresh MCP_AUTH_TOKEN and write it into .mcp.env.
#   2. Stop the Forge container AND wipe its volumes so the persisted
#      credential cache is gone.  Postgres data (a separate volume)
#      is preserved — gitpilot's MCP registry stays intact.
#   3. Bring Forge back up with the new token, wait for /health, run
#      the registration sweep.
#
# Idempotent.  Safe to run any number of times.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Colours, degrading to plain when not a TTY.
if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); BOLD=$(printf '\033[1m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${RESET} %s\n" "$*"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$*" 1>&2; }
step()  { printf "${BOLD}🔹${RESET} %s\n" "$*"; }

ENV_FILE="${REPO_ROOT}/.mcp.env"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.mcp.yml"

if [ ! -f "${ENV_FILE}" ]; then
  fail ".mcp.env not found at ${ENV_FILE}"
  fail "Run 'make install-mcp' first to bootstrap the MCP env."
  exit 2
fi
if [ ! -f "${COMPOSE_FILE}" ]; then
  fail "docker-compose.mcp.yml not found at ${COMPOSE_FILE}"
  exit 2
fi

# -----------------------------------------------------------------------
# 1. Generate fresh token + update .mcp.env
# -----------------------------------------------------------------------
step "Rotating MCP_AUTH_TOKEN in .mcp.env"

if command -v openssl >/dev/null 2>&1; then
  NEW_TOKEN=$(openssl rand -hex 32)
elif command -v python3 >/dev/null 2>&1; then
  NEW_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
else
  fail "Need openssl or python3 to generate a secure token."
  exit 2
fi

# Portable sed -i (BSD/GNU). Try GNU form first; fall back to BSD.
if sed -i.bak "s|^MCP_AUTH_TOKEN=.*|MCP_AUTH_TOKEN=${NEW_TOKEN}|" "${ENV_FILE}" 2>/dev/null; then
  rm -f "${ENV_FILE}.bak"
else
  sed -i '' "s|^MCP_AUTH_TOKEN=.*|MCP_AUTH_TOKEN=${NEW_TOKEN}|" "${ENV_FILE}"
fi
ok "Wrote fresh MCP_AUTH_TOKEN to .mcp.env (32 bytes, hex-encoded)."

# -----------------------------------------------------------------------
# 2. Stop Forge + wipe its volumes
# -----------------------------------------------------------------------
step "Stopping mcp-context-forge and wiping its volumes"

# ``rm -fsv`` stops the container AND deletes its named volumes.
# Postgres uses different named volumes so its data is preserved.
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile mcp \
  rm -fsv mcp-context-forge >/dev/null 2>&1 || true

# Belt-and-suspenders: hunt for any Forge-specific anonymous volumes
# that survived (rare, but seen on older compose versions).
docker volume ls --format '{{.Name}}' 2>/dev/null \
  | grep -E 'gitpilot-mcp.*forge' \
  | xargs -r docker volume rm 2>/dev/null \
  || true
ok "Forge stopped, volumes removed."

# -----------------------------------------------------------------------
# 3. Re-up Forge with the new token + register
# -----------------------------------------------------------------------
step "Starting mcp-context-forge with the rotated token"

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile mcp \
  up -d --force-recreate --no-deps mcp-context-forge >/dev/null

# Wait for /health to answer through the published host port.
FORGE_PORT="${MCP_FORGE_PORT:-4444}"
HOST_PROBE="http://localhost:${FORGE_PORT}/health"
printf "  waiting for ${HOST_PROBE}"
for i in $(seq 1 60); do
  if curl -fsS -m 2 -o /dev/null "${HOST_PROBE}" 2>/dev/null; then
    echo " (${i}s)"
    break
  fi
  printf "."
  sleep 1
  if [ "${i}" -eq 60 ]; then
    echo
    fail "Forge did not return to healthy within 60s."
    fail "Inspect:  docker logs gitpilot-mcp-context-forge --tail 80"
    exit 5
  fi
done
ok "Forge healthy at ${HOST_PROBE}."

# Re-run registration with the rotated token.
step "Re-registering MCP servers with the new token"
if ! bash "${SCRIPT_DIR}/register-mcp-servers.sh"; then
  fail "Registration still failed after token rotation."
  fail "Last resort:  make stop-mcp && make uninstall-mcp && make run-mcp"
  exit 6
fi

echo
ok "MCP token rotation complete."
echo
echo "Next:"
echo "  make run           # start GitPilot with the rotated token"
echo "  curl -sf -H 'Authorization: Bearer ${NEW_TOKEN:0:12}…' \\"
echo "       http://localhost:${FORGE_PORT}/gateways   # verify the new token works"
