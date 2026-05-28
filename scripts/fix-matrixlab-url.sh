#!/usr/bin/env bash
# scripts/fix-matrixlab-url.sh
#
# Detects which port the MatrixLab runner is actually listening on,
# then writes that URL into GitPilot's sandbox settings.  Solves the
# "Runner URL: http://localhost:8000 / Needs attention" symptom that
# happens when a user's persisted settings.json has the old default
# port (8000) but the runner was started on the new default (8765).
#
# Strategy:
#   1. Probe http://localhost:{8765,8000}/health
#   2. Pick the first one that answers
#   3. PUT /api/sandbox/config with the matching URL
#   4. POST /api/matrixlab/test to confirm GitPilot now sees it
#
# Idempotent.  Safe to run any number of times.  Additive only:
# does not modify any source file, just talks to the running
# GitPilot backend's HTTP API.
set -uo pipefail

GITPILOT_URL="${GITPILOT_URL:-http://127.0.0.1:8000}"
CANDIDATES=(
  "${MATRIXLAB_URL:-}"               # explicit override wins
  "http://localhost:8765"            # current default (post port-shift)
  "http://localhost:8000"            # legacy default (pre port-shift)
  "http://host.docker.internal:8765" # Docker Desktop on macOS/Windows
)

# Colours — degrade gracefully when not a TTY.
if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); BOLD=$(printf '\033[1m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi
ok()   { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${RESET} %s\n" "$*"; }
fail() { printf "${RED}✗${RESET} %s\n" "$*" 1>&2; }
step() { printf "${BOLD}🔹${RESET} %s\n" "$*"; }

# ---- 1. GitPilot reachable? ---------------------------------------------
step "Probing GitPilot backend at ${GITPILOT_URL}"
if ! curl -fsS -m 3 -o /dev/null "${GITPILOT_URL}/api/ping" 2>/dev/null; then
  fail "GitPilot backend not reachable at ${GITPILOT_URL}/api/ping"
  fail "Start it first:  make run     (or:  make run-bare)"
  exit 2
fi
ok "GitPilot backend is up"

# ---- 2. What does GitPilot currently think the matrixlab URL is? --------
step "Reading current sandbox settings from /api/sandbox/status"
current_payload=$(curl -fsS -m 5 "${GITPILOT_URL}/api/sandbox/status" 2>/dev/null || true)
if [ -z "${current_payload}" ]; then
  fail "/api/sandbox/status did not respond"
  exit 3
fi
current_url=$(echo "${current_payload}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('matrixlab_url',''))" 2>/dev/null)
current_backend=$(echo "${current_payload}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('backend',''))" 2>/dev/null)
ok "Currently configured:  backend=${current_backend:-?}  matrixlab_url=${current_url:-?}"

# ---- 3. Probe each candidate runner URL ---------------------------------
step "Probing candidate runner URLs for a live /health"
chosen_url=""
for url in "${CANDIDATES[@]}"; do
  [ -z "${url}" ] && continue
  printf "   • %-45s " "${url}"
  resp=$(curl -fsS -m 2 -o /dev/null -w '%{http_code}' "${url}/health" 2>/dev/null || true)
  if [ "${resp}" = "200" ]; then
    printf "${GREEN}✓ HTTP 200${RESET}\n"
    if [ -z "${chosen_url}" ]; then
      chosen_url="${url}"
    fi
  else
    printf "%s\n" "${resp:-no-response}"
  fi
done

if [ -z "${chosen_url}" ]; then
  echo
  fail "No candidate URL answered /health."
  fail "Start the runner first:"
  fail "    make install-matrixlab"
  fail "or, if you have the addon image already:"
  fail "    docker run -d --name gitpilot-matrixlab -p 8765:8000 \\"
  fail "      -v /var/run/docker.sock:/var/run/docker.sock \\"
  fail "      ruslanmv/matrixlab-runner:latest"
  exit 4
fi

# ---- 4. Already pointing at the right URL? -------------------------------
if [ "${current_url}" = "${chosen_url}" ]; then
  ok "GitPilot's matrixlab_url already matches the live runner — nothing to fix."
  # Still flip the backend if it isn't matrixlab yet — common when the
  # operator just installed the addon and hasn't toggled in the UI.
  if [ "${current_backend}" != "matrixlab" ]; then
    warn "Active sandbox backend is '${current_backend}', flipping to 'matrixlab'..."
    curl -fsS -X PUT "${GITPILOT_URL}/api/sandbox/config" \
      -H 'Content-Type: application/json' \
      -d '{"backend":"matrixlab"}' >/dev/null
    ok "Backend flipped to matrixlab"
  fi
  exit 0
fi

# ---- 5. Update GitPilot's settings ---------------------------------------
step "Updating GitPilot:  matrixlab_url  ${current_url:-?}  →  ${chosen_url}"
body=$(python3 -c "import json,sys; print(json.dumps({'backend':'matrixlab','matrixlab_url':sys.argv[1]}))" "${chosen_url}")
if ! curl -fsS -X PUT "${GITPILOT_URL}/api/sandbox/config" \
     -H 'Content-Type: application/json' \
     -d "${body}" >/dev/null; then
  fail "PUT /api/sandbox/config failed."
  exit 5
fi
ok "Settings updated"

# ---- 6. Confirm GitPilot now sees the runner -----------------------------
step "Confirming via /api/matrixlab/test"
test_payload=$(curl -fsS -X POST "${GITPILOT_URL}/api/matrixlab/test" 2>/dev/null || true)
status=$(echo "${test_payload}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
case "${status}" in
  ready)
    ok "Status: ready"
    echo
    ok "Done.  Reload the Install MatrixLab Addon modal — it should now show 'Ready'."
    ;;
  needs_attention|failed)
    err=$(echo "${test_payload}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('errorCode','?'), '·', d.get('message','?'))" 2>/dev/null)
    warn "Status after update: ${status} (${err})"
    warn "URL is now correct, but the runner may need a restart.  Try:"
    warn "    make install-matrixlab   # idempotent — re-up the container"
    ;;
  *)
    warn "Unexpected status: ${status}"
    echo "${test_payload}" | python3 -m json.tool 2>/dev/null || echo "${test_payload}"
    ;;
esac
