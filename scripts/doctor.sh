#!/usr/bin/env bash
# scripts/doctor.sh
#
# Preflight + diagnose for GitPilot. Runs a set of cheap checks that
# catch the failure modes a fresh-install operator can hit, and
# prints a one-line fix command alongside each red ✗.
#
# Exits 0 when every required check passes, 1 otherwise.  Optional
# checks (MatrixLab, MCP) print yellow ⚠ but don't fail the overall
# run unless the relevant profile is active.
set -uo pipefail

# Resolve repo root so `bash scripts/doctor.sh` works from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Colours — degrade gracefully when stdout isn't a TTY.
if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; RESET=""
fi
ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
fail()  { printf "  ${RED}✗${RESET} %s\n" "$*"; FAILED=$((FAILED + 1)); }
warn()  { printf "  ${YELLOW}⚠${RESET} %s\n" "$*"; }
hint()  { printf "      ↳ fix: %s\n" "$*"; }
section() { printf "\n%s\n" "$1"; }

FAILED=0

section "🔧 Host tools"

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable ($(docker --version | head -c 60))"
  else
    fail "docker installed but daemon not running"
    hint "start Docker Desktop, or  sudo systemctl start docker"
  fi
else
  fail "docker not on PATH"
  hint "install Docker — https://docs.docker.com/get-docker/"
fi

if command -v uv >/dev/null 2>&1; then
  uv_v=$(uv --version 2>/dev/null | awk '{print $2}')
  ok "uv ${uv_v}"
else
  fail "uv not on PATH (needed for the Python runtime)"
  hint "curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if command -v node >/dev/null 2>&1; then
  node_v=$(node -v 2>/dev/null)
  major=${node_v:1}; major=${major%%.*}
  if [ "${major:-0}" -ge 20 ]; then
    ok "node ${node_v} (≥ 20 OK)"
  else
    warn "node ${node_v} (recommend ≥ 20 for Vite 6)"
  fi
else
  fail "node not on PATH (needed for the frontend build)"
  hint "install via nvm:  nvm install 20 && nvm use 20"
fi

if command -v curl >/dev/null 2>&1; then
  ok "curl present"
else
  fail "curl not on PATH"
  hint "apt-get install curl  (used by readiness probes)"
fi

section "🌐 Network ports"

# Check each port; "free" or "held by our own container" is OK.
port_status() {
  local port="$1"
  local label="$2"
  local our_container_pattern="${3:-}"

  # Anything bound on this port?
  if ! command -v lsof >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
    warn "port ${port} (${label}): can't probe — neither lsof nor ss present"
    return
  fi
  local who=""
  if command -v lsof >/dev/null 2>&1; then
    who=$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1}' | head -1)
  fi
  if [ -z "${who}" ] && command -v ss >/dev/null 2>&1; then
    who=$(ss -tlnp "sport = :${port}" 2>/dev/null | awk 'NR>1' | head -1)
  fi
  if [ -z "${who}" ]; then
    ok "port ${port} (${label}) free"
    return
  fi
  # Held — is it our own container?
  if [ -n "${our_container_pattern}" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${our_container_pattern}"; then
    ok "port ${port} (${label}) held by our own container"
  else
    fail "port ${port} (${label}) held by ${who}"
    hint "stop the conflicting process, or  make stop"
  fi
}

port_status 8000 "GitPilot backend"        "gitpilot-backend\|^gitpilot$"
port_status 5173 "Vite dev frontend"       "gitpilot-frontend"
port_status 4444 "MCP Context Forge"       "gitpilot-mcp-context-forge"
port_status 8765 "MatrixLab runner"        "gitpilot-matrixlab\|matrix-lab-runner"

section "📄 Configuration files"

if [ -f .env ]; then
  if grep -q '^GITHUB_CLIENT_ID=..*' .env 2>/dev/null \
     && grep -q '^GITHUB_CLIENT_SECRET=..*' .env 2>/dev/null; then
    ok ".env has GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET populated"
  else
    fail ".env exists but GitHub OAuth credentials are missing"
    hint "set GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET in .env"
  fi
else
  warn ".env not found — using template defaults"
  hint "cp .env.template .env  and fill in GitHub OAuth credentials"
fi

if [ -f .mcp.env ]; then
  if grep -q '^MCP_AUTH_TOKEN=..*' .mcp.env 2>/dev/null; then
    ok ".mcp.env has MCP_AUTH_TOKEN populated"
  else
    fail ".mcp.env exists but MCP_AUTH_TOKEN is empty"
    hint "make install-mcp  (regenerates the token)"
  fi
else
  warn ".mcp.env not found — MCP stack won't start"
  hint "make install-mcp"
fi

section "🐳 Local images"

check_image() {
  local image="$1"
  local optional="${2:-required}"
  if docker image inspect "${image}" >/dev/null 2>&1; then
    ok "${image} present locally"
  else
    if [ "${optional}" = "optional" ]; then
      warn "${image} not pulled (optional)"
      hint "docker pull ${image}"
    else
      fail "${image} not pulled"
      hint "docker pull ${image}  or  make build"
    fi
  fi
}

check_image "ghcr.io/ibm/mcp-context-forge:stable"    "optional"
check_image "ruslanmv/matrixlab-runner:latest"        "optional"
check_image "ruslanmv/matrix-lab-sandbox-python:latest" "optional"

section "🧪 Running containers"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^(gitpilot|matrix-lab|matrixlab)-' | sort)
  if [ -z "${running}" ]; then
    ok "no GitPilot / MatrixLab containers running yet"
  else
    while IFS= read -r name; do
      ok "container running: ${name}"
    done <<< "${running}"
  fi
fi

section "🔗 MCP token consistency"

# Catches the silent-401 bug.  When Forge is up, ask it to validate the
# token we'd send; mismatch → red ✗ + rotate hint.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'gitpilot-mcp-context-forge'; then
  if [ -f .mcp.env ] && grep -q '^MCP_AUTH_TOKEN=' .mcp.env; then
    token=$(grep '^MCP_AUTH_TOKEN=' .mcp.env | head -1 | cut -d= -f2- | tr -d '"')
    code=$(docker run --rm --network gitpilot-mcp curlimages/curl:8.10.1 \
        -sS -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer ${token}" \
        "http://mcp-context-forge:4444/gateways" 2>/dev/null || echo "000")
    case "${code}" in
      200|404)
        ok ".mcp.env token matches the running Forge"
        ;;
      401|403)
        # Soft signal — operator-recoverable, doesn't block ``make run``.
        warn ".mcp.env token does NOT match Forge — auto-registration will skip"
        hint "make rotate-mcp-token   (or just register servers from Settings → MCP Servers → Sync)"
        ;;
      *)
        warn "Forge probe returned HTTP ${code} — inspect:  docker logs gitpilot-mcp-context-forge --tail 40"
        ;;
    esac
  fi
else
  ok "MCP Forge not running — token check skipped"
fi

section "🧪 MatrixLab addon"

# Only flagged when the operator already opted in.
if [ -f .gitpilot/sandbox-settings.json ] && grep -q '"backend"[[:space:]]*:[[:space:]]*"matrixlab"' .gitpilot/sandbox-settings.json 2>/dev/null; then
  matrixlab_url="http://localhost:8765"
  if curl -fsS -m 2 "${matrixlab_url}/health" >/dev/null 2>&1; then
    ok "MatrixLab runner reachable at ${matrixlab_url}/health"
  else
    fail "MatrixLab backend selected but runner is unreachable at ${matrixlab_url}"
    hint "make install-matrixlab  or open the Install MatrixLab Addon modal"
  fi
else
  warn "MatrixLab not the active sandbox backend (using local subprocess)"
fi

# Final verdict
echo
if [ "${FAILED}" -eq 0 ]; then
  printf "${GREEN}✓ All required checks passed.${RESET}  Run:  make run\n"
  exit 0
else
  printf "${RED}✗ ${FAILED} required check(s) failed.${RESET}  Fix the items above and re-run \`make doctor\`.\n"
  exit 1
fi
