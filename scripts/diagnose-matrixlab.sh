#!/usr/bin/env bash
# scripts/diagnose-matrixlab.sh
#
# Pretty-prints every piece of state that explains the "MatrixLab is
# installed, but GitPilot cannot connect to the runner" symptom.
# Use when the install modal says ``Needs attention`` and you want
# to see, side by side:
#
#   - what GitPilot thinks the runner URL is
#   - what's actually listening on each candidate port
#   - what /api/matrixlab/status returns
#   - what /api/matrixlab/native/status returns
#   - which Docker containers are up
#   - last 40 lines of the runner log
#
# Output is plain-text and copy-paste friendly for bug reports.
# No state is modified (read-only probe).
set -uo pipefail

GITPILOT_URL="${GITPILOT_URL:-http://127.0.0.1:8000}"
PORTS=(8000 8765)

if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m')
  BLUE=$(printf '\033[34m'); BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; BLUE=""; BOLD=""; DIM=""; RESET=""
fi
hdr() { printf "\n${BOLD}${BLUE}══ %s ══${RESET}\n" "$*"; }
kv()  { printf "  ${BOLD}%-22s${RESET} %s\n" "$1" "$2"; }

# -------------------------------------------------------------------------
hdr "1 · System"
kv "uname"      "$(uname -srm)"
kv "docker"     "$(docker --version 2>/dev/null | head -c 80 || echo 'not on PATH')"
kv "docker daemon" "$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo 'NOT REACHABLE')"
kv "curl"       "$(curl --version 2>/dev/null | head -1 | head -c 60)"
kv "python3"    "$(python3 --version 2>/dev/null || echo 'not on PATH')"

# -------------------------------------------------------------------------
hdr "2 · Candidate runner ports"
for p in "${PORTS[@]}"; do
  status=""
  body=""
  status=$(curl -fsS -m 2 -o /dev/null -w '%{http_code}' "http://localhost:${p}/health" 2>/dev/null || echo "unreachable")
  if [ "${status}" = "200" ]; then
    body=$(curl -fsS -m 2 "http://localhost:${p}/health" 2>/dev/null | head -c 200)
    printf "  ${GREEN}✓${RESET} localhost:%-6s HTTP 200  %s\n" "${p}" "${body}"
  else
    printf "  ${RED}✗${RESET} localhost:%-6s %s\n" "${p}" "${status}"
  fi
done

# -------------------------------------------------------------------------
hdr "3 · Listening processes on candidate ports"
for p in "${PORTS[@]}"; do
  who=""
  if command -v lsof >/dev/null 2>&1; then
    who=$(lsof -nP -iTCP:"${p}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1' | head -3)
  elif command -v ss >/dev/null 2>&1; then
    who=$(ss -tlnp "sport = :${p}" 2>/dev/null | awk 'NR>1' | head -3)
  fi
  if [ -z "${who}" ]; then
    printf "  ${DIM}port %-6s no listener${RESET}\n" "${p}"
  else
    printf "  port %-6s\n%s\n" "${p}" "${who}" | sed 's/^/    /'
  fi
done

# -------------------------------------------------------------------------
hdr "4 · GitPilot's view of sandbox state"
if ! curl -fsS -m 2 -o /dev/null "${GITPILOT_URL}/api/ping" 2>/dev/null; then
  printf "  ${YELLOW}⚠${RESET} GitPilot backend not reachable at ${GITPILOT_URL} — skipping API probes\n"
  printf "      start it with:  make run     (or:  make run-bare)\n"
else
  printf "  ${GREEN}✓${RESET} ${GITPILOT_URL}/api/ping responding\n\n"

  for endpoint in \
    "/api/sandbox/status" \
    "/api/matrixlab/status" \
    "/api/matrixlab/native/status" \
    "/api/sandbox/matrixlab/lifecycle" ; do
    printf "  ${BOLD}GET ${endpoint}${RESET}\n"
    resp=$(curl -fsS -m 5 "${GITPILOT_URL}${endpoint}" 2>/dev/null || echo '{"error":"endpoint failed"}')
    echo "${resp}" | python3 -m json.tool 2>/dev/null | sed 's/^/      /' || echo "      ${resp}"
    echo
  done
fi

# -------------------------------------------------------------------------
hdr "5 · Docker containers"
if docker info >/dev/null 2>&1; then
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
    | head -20 | sed 's/^/  /'
  echo
  related=$(docker ps -a --format '{{.Names}}' 2>/dev/null \
    | grep -E '^(gitpilot|matrix-lab|matrixlab)' | head -10)
  if [ -n "${related}" ]; then
    printf "  ${BOLD}Project-related containers (state):${RESET}\n"
    while IFS= read -r name; do
      st=$(docker inspect --format '{{.State.Status}} (exit={{.State.ExitCode}})' "${name}" 2>/dev/null)
      printf "    %-40s  %s\n" "${name}" "${st}"
    done <<< "${related}"
  fi
fi

# -------------------------------------------------------------------------
hdr "6 · MatrixLab runner logs (last 40 lines)"
for candidate in gitpilot-matrixlab matrix-lab-runner-1 matrixlab-runner; do
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "${candidate}"; then
    printf "  ${BOLD}docker logs ${candidate} --tail 40${RESET}\n"
    docker logs "${candidate}" --tail 40 2>&1 | sed 's/^/    /' | head -42
    break
  fi
done

# -------------------------------------------------------------------------
hdr "7 · Recommended next step"
if curl -fsS -m 2 -o /dev/null "http://localhost:8765/health" 2>/dev/null; then
  printf "  ${GREEN}✓${RESET} Runner is alive on :8765\n"
  # Check if GitPilot's configured URL agrees.
  if curl -fsS -m 2 -o /dev/null "${GITPILOT_URL}/api/ping" 2>/dev/null; then
    cfg_url=$(curl -fsS -m 2 "${GITPILOT_URL}/api/sandbox/status" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('matrixlab_url',''))" 2>/dev/null)
    if [ "${cfg_url}" != "http://localhost:8765" ] && [ -n "${cfg_url}" ]; then
      printf "  ${YELLOW}⚠${RESET} GitPilot's matrixlab_url is '${cfg_url}', should be 'http://localhost:8765'\n"
      printf "  Fix:  ${BOLD}make fix-matrixlab-url${RESET}\n"
    else
      printf "  ${GREEN}✓${RESET} GitPilot's matrixlab_url is correctly set\n"
    fi
  fi
elif curl -fsS -m 2 -o /dev/null "http://localhost:8000/health" 2>/dev/null; then
  printf "  ${YELLOW}⚠${RESET} Runner is on legacy port :8000 (current default is :8765)\n"
  printf "  Either move it to :8765 (recommended) or run:\n"
  printf "      ${BOLD}make fix-matrixlab-url${RESET}     # accepts whichever port responds\n"
else
  printf "  ${RED}✗${RESET} Runner is not reachable on any candidate port.  Install it:\n"
  printf "      ${BOLD}make install-matrixlab${RESET}\n"
fi
echo
