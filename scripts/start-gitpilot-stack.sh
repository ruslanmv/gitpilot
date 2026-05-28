#!/usr/bin/env bash
# scripts/start-gitpilot-stack.sh
#
# Full production startup that wraps the existing 'make run' flow
# and adds the MatrixLab addon on top.  Purely additive — does NOT
# modify the original Makefile recipes or any scripts on the canonical
# startup path.  Used by:
#
#   make startup            # one command, full stack
#   bash scripts/start-gitpilot-stack.sh
#
# What it does, in order:
#
#   1. Sanity check (docker + ports + .env populated).
#   2. Install/start the MatrixLab addon (idempotent, reuses container).
#   3. Start GitPilot (delegates to 'make run' — original recipe).
#   4. Wait for GitPilot's /api/ping.
#   5. Auto-fix the persisted matrixlab_url so the Install Addon
#      modal points at the actually-listening port (the bug behind
#      the "Runner URL: http://localhost:8000 / Needs attention"
#      screenshot — caused by stale settings.json from before the
#      port-shift to 8765).
#   6. Print a green summary with the URLs and the next-step hints.
#
# Verbose by default — every step is logged with elapsed time so an
# operator filing a bug report can paste the log and we can see
# exactly where things diverged.  Set GITPILOT_QUIET=1 to suppress.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

GITPILOT_PORT="${GITPILOT_PORT:-8000}"
GITPILOT_URL="http://127.0.0.1:${GITPILOT_PORT}"

# ---- Logging -------------------------------------------------------------
if [ -t 1 ] && [ -z "${GITPILOT_QUIET:-}" ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m')
  BLUE=$(printf '\033[34m'); BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; BLUE=""; BOLD=""; DIM=""; RESET=""
fi
__t0=$(date +%s)
ts()    { printf "${DIM}[+%3ds]${RESET}" $(($(date +%s) - __t0)); }
ok()    { printf "%s ${GREEN}✓${RESET} %s\n" "$(ts)" "$*"; }
warn()  { printf "%s ${YELLOW}⚠${RESET} %s\n" "$(ts)" "$*"; }
fail()  { printf "%s ${RED}✗${RESET} %s\n" "$(ts)" "$*" 1>&2; }
step()  { printf "\n%s ${BOLD}${BLUE}━━ %s ━━${RESET}\n" "$(ts)" "$*"; }
debug() { [ -n "${GITPILOT_DEBUG:-}" ] && printf "%s ${DIM}· %s${RESET}\n" "$(ts)" "$*" || true; }

# ---- 1. Preflight -------------------------------------------------------
step "1/5 · Preflight"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not on PATH — install Docker (https://docs.docker.com/get-docker/)"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  fail "docker daemon not running — start Docker Desktop or 'sudo systemctl start docker'"
  exit 1
fi
ok "docker reachable"

if [ ! -f .env ]; then
  warn ".env not found — GitPilot will boot with template defaults"
  warn "   Get OAuth credentials at:  https://github.com/settings/applications/new"
  warn "   cp .env.template .env  and fill in GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET"
else
  ok ".env present"
fi

# Port check — accept "held by our own container" as fine.
port_busy() {
  local port="$1"
  local our_match="$2"
  if command -v lsof >/dev/null 2>&1; then
    local who
    who=$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1}' | head -1)
    [ -z "${who}" ] && return 1
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${our_match}" && return 1
    echo "${who}"
    return 0
  fi
  return 1
}
for entry in "8000:gitpilot" "4444:gitpilot-mcp-context-forge" "8765:gitpilot-matrixlab"; do
  port="${entry%%:*}"
  pattern="${entry##*:}"
  who=$(port_busy "${port}" "${pattern}" || true)
  if [ -n "${who}" ]; then
    warn "port ${port} held by ${who} (not one of our containers — may conflict)"
  else
    debug "port ${port} free or held by our own container"
  fi
done

# ---- 2. Install / start MatrixLab addon ---------------------------------
step "2/5 · MatrixLab addon (idempotent install + start)"

# Force-disable interactive prompts in install-matrixlab.sh.  The
# script is already idempotent (re-running reuses the existing
# container).
MATRIXLAB_PORT="${MATRIXLAB_PORT:-8765}"
MATRIXLAB_NAMESPACE="${MATRIXLAB_NAMESPACE:-ruslanmv}"
export MATRIXLAB_PORT MATRIXLAB_NAMESPACE

if [ -x "${SCRIPT_DIR}/install-matrixlab.sh" ]; then
  if bash "${SCRIPT_DIR}/install-matrixlab.sh"; then
    ok "MatrixLab addon ready on :${MATRIXLAB_PORT}"
  else
    warn "MatrixLab install returned non-zero — continuing without it"
    warn "   Inspect with:  bash scripts/install-matrixlab.sh"
  fi
else
  warn "scripts/install-matrixlab.sh not present — skipping addon install"
fi

# ---- 3. Start GitPilot via the existing `make run` ----------------------
step "3/5 · GitPilot backend + frontend (delegating to 'make run')"

# If GitPilot is already up we don't need to start it.
if curl -fsS -m 2 -o /dev/null "${GITPILOT_URL}/api/ping" 2>/dev/null; then
  ok "GitPilot already running on ${GITPILOT_URL}"
else
  # Launch in the background so we can probe + fix the URL afterwards.
  # The user can still Ctrl-C the trailing process when they're done.
  ok "Starting 'make run' in the background — logs streamed below"
  echo
  # Foreground would block; we run it in a way the operator can see.
  make run &
  MAKE_PID=$!
  trap 'kill $MAKE_PID 2>/dev/null || true' INT TERM

  # Wait for /api/ping.  Bounded by GITPILOT_START_TIMEOUT (default 120s).
  __wait_start=$(date +%s)
  __wait_limit=$((${GITPILOT_START_TIMEOUT:-120}))
  while ! curl -fsS -m 2 -o /dev/null "${GITPILOT_URL}/api/ping" 2>/dev/null; do
    if [ $(($(date +%s) - __wait_start)) -gt "${__wait_limit}" ]; then
      fail "GitPilot did not answer /api/ping within ${__wait_limit}s"
      exit 5
    fi
    sleep 2
  done
  echo
  ok "GitPilot /api/ping responding after $(($(date +%s) - __wait_start))s"
fi

# ---- 4. Auto-fix the persisted matrixlab_url ----------------------------
step "4/5 · Auto-detect and fix the matrixlab_url in settings.json"

if [ -x "${SCRIPT_DIR}/fix-matrixlab-url.sh" ]; then
  GITPILOT_URL="${GITPILOT_URL}" bash "${SCRIPT_DIR}/fix-matrixlab-url.sh" || \
    warn "URL auto-fix returned non-zero — open Settings → Sandbox → Advanced to set it manually"
else
  warn "scripts/fix-matrixlab-url.sh missing — skipping URL auto-fix"
fi

# ---- 5. Summary ---------------------------------------------------------
step "5/5 · Stack ready"

cat <<SUMMARY

  ${BOLD}GitPilot      ${RESET}${GITPILOT_URL}
  ${BOLD}MCP Forge     ${RESET}http://localhost:4444         (if MCP stack started)
  ${BOLD}MatrixLab     ${RESET}http://localhost:${MATRIXLAB_PORT}/health
  ${BOLD}Frontend (dev)${RESET}http://localhost:5173

  ${BOLD}Diagnose anything that didn't come up clean:${RESET}
    make doctor                                # ports / images / token check
    make diagnose-matrixlab                    # full matrixlab-side probe
    docker logs gitpilot-matrixlab --tail 80   # runner logs

  ${BOLD}Stop everything:${RESET}
    make stop                                  # GitPilot + MCP
    docker stop gitpilot-matrixlab             # MatrixLab addon

SUMMARY

# Keep the trailing make-run in the foreground for the operator (so
# Ctrl-C still tears down the dev servers cleanly).  Bail when the
# subprocess exits.
if [ -n "${MAKE_PID:-}" ]; then
  wait "${MAKE_PID}" 2>/dev/null || true
fi
