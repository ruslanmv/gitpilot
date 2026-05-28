#!/usr/bin/env bash
# scripts/install-matrixlab.sh
#
# Declaratively provision MatrixLab as a legacy addon for GitPilot.
# Fired by:
#   make install-matrixlab
#   make install WITH_MATRIXLAB=1
#
# What this script does (all idempotent):
#
#   1. Pull the published Docker images from Docker Hub.
#   2. Write a host-side jobs directory that's bind-mounted into the
#      runner so the Docker-in-Docker workspace path resolves
#      correctly (the same recipe gitpilot's install modal uses).
#   3. Start the runner on host port 8765 if it isn't already up.
#   4. Probe /health until it answers.
#   5. Print the curl smoke-test the operator should run next.
#
# Failure modes (each with a friendly hint, never a raw traceback):
#   - docker not installed                → install Docker
#   - docker daemon not reachable         → start Docker Desktop
#   - port 8765 held by something else    → choose a different port
#   - runner image pull rejected          → check registry credentials
#   - /health never answers within 60s    → inspect docker logs
#
# Configurable via env vars (prefixed with MATRIXLAB_ for clarity):
#
#   MATRIXLAB_VERSION       Image tag — defaults to "latest".
#                           Pin to v0.1.0 in production for reproducibility.
#   MATRIXLAB_PORT          Host port mapping — defaults to 8765.
#                           Avoid 8000 (GitPilot's own backend).
#   MATRIXLAB_JOBS_DIR      Host path for the runner workspace —
#                           defaults to /tmp/gitpilot-matrixlab-jobs.
#                           Override to a persistent path like
#                           /var/lib/matrixlab/jobs for production.
#   MATRIXLAB_NAMESPACE     Image namespace — defaults to "ruslanmv".
#                           Only change when running against a local
#                           registry mirror.
set -euo pipefail

# ---- Resolve repo root ---------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ---- Colours / helpers ---------------------------------------------------
if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); BOLD=$(printf '\033[1m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${RESET} %s\n" "$*"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$*" 1>&2; }
step()  { printf "${BOLD}🔹${RESET} %s\n" "$*"; }
hint()  { printf "      ↳ %s\n" "$*"; }

# ---- Configuration -------------------------------------------------------
MATRIXLAB_VERSION="${MATRIXLAB_VERSION:-latest}"
MATRIXLAB_PORT="${MATRIXLAB_PORT:-8765}"
MATRIXLAB_JOBS_DIR="${MATRIXLAB_JOBS_DIR:-/tmp/gitpilot-matrixlab-jobs}"
MATRIXLAB_NAMESPACE="${MATRIXLAB_NAMESPACE:-ruslanmv}"
MATRIXLAB_CONTAINER="${MATRIXLAB_CONTAINER:-gitpilot-matrixlab}"

RUNNER_IMAGE="${MATRIXLAB_NAMESPACE}/matrixlab-runner:${MATRIXLAB_VERSION}"
SANDBOX_PYTHON="${MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-python:${MATRIXLAB_VERSION}"
SANDBOX_NODE="${MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-node:${MATRIXLAB_VERSION}"
SANDBOX_UTILS="${MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-utils:${MATRIXLAB_VERSION}"

echo
printf "${BOLD}📦 Installing MatrixLab addon (legacy package)${RESET}\n"
echo "   Version:    ${MATRIXLAB_VERSION}"
echo "   Namespace:  ${MATRIXLAB_NAMESPACE}"
echo "   Host port:  ${MATRIXLAB_PORT}"
echo "   Jobs dir:   ${MATRIXLAB_JOBS_DIR}"
echo "   Container:  ${MATRIXLAB_CONTAINER}"
echo

# ---- 1. Preflight --------------------------------------------------------
step "Preflight"

# In ``MATRIXLAB_OPTIONAL=1`` mode (chained from ``make install``), a
# missing docker / dead daemon / held port should warn and skip — not
# break the parent installer.  The standalone ``make install-matrixlab``
# leaves this var unset and gets the strict fatal behavior.
__optional() { [ -n "${MATRIXLAB_OPTIONAL:-}" ]; }
__skip_or_die() {
  local reason="$1"; shift
  if __optional; then
    warn "MatrixLab install skipped — ${reason}"
    [ "$#" -gt 0 ] && for line in "$@"; do hint "$line"; done
    warn "Re-run with:  make install-matrixlab     # once the prerequisite is in place"
    exit 0
  fi
  fail "${reason}"
  [ "$#" -gt 0 ] && for line in "$@"; do hint "$line"; done
  exit 2
}

if ! command -v docker >/dev/null 2>&1; then
  __skip_or_die "docker not on PATH" \
    "Install Docker: https://docs.docker.com/get-docker/"
fi
if ! docker info >/dev/null 2>&1; then
  __skip_or_die "docker daemon not reachable" \
    "Start Docker Desktop, or:  sudo systemctl start docker"
fi
ok "docker reachable"

# Port check — accept "held by our own container" as fine.
#
# Robust detection: ``lsof`` can be missing on slim WSL distros (where
# this script frequently runs) and even when present may not see ports
# bound by the docker daemon (running as root) if invoked as a non-root
# user.  We try every available tool and *also* ask docker directly
# whether any container publishes the port — that catches the very
# common case where a stale matrixlab container from a previous
# install attempt is sitting in state ``created`` with the port
# reserved.
__port_in_use_by_proc() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${MATRIXLAB_PORT}" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH "( sport = :${MATRIXLAB_PORT} )" 2>/dev/null | grep -q . && return 0
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -E "[:.]${MATRIXLAB_PORT}\$" >/dev/null && return 0
  fi
  return 1
}
# Return the name of any docker container publishing MATRIXLAB_PORT,
# else empty.  Format: container_name ("" when none).
__port_in_use_by_docker() {
  docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v p=":${MATRIXLAB_PORT}->" '$2 ~ p { print $1; exit }'
}

if __port_in_use_by_proc || [ -n "$(__port_in_use_by_docker)" ]; then
  holder="$(__port_in_use_by_docker)"
  if [ "${holder}" = "${MATRIXLAB_CONTAINER}" ]; then
    ok "port ${MATRIXLAB_PORT} already held by ${MATRIXLAB_CONTAINER} (will reuse)"
  elif [ -n "${holder}" ]; then
    __skip_or_die "port ${MATRIXLAB_PORT} held by another docker container: ${holder}" \
      "Stop it:  docker stop ${holder}" \
      "or set:  MATRIXLAB_PORT=<other> make install-matrixlab"
  else
    __skip_or_die "port ${MATRIXLAB_PORT} held by another process" \
      "Find the process:  sudo lsof -nP -iTCP:${MATRIXLAB_PORT} -sTCP:LISTEN" \
      "or set:  MATRIXLAB_PORT=<other> make install-matrixlab"
  fi
else
  ok "port ${MATRIXLAB_PORT} free"
fi

# ---- 2. Pull images ------------------------------------------------------
step "Pulling images from Docker Hub"

pull_one() {
  local img="$1"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    ok "${img} already present (skip pull)"
    return 0
  fi
  if docker pull "${img}" >/dev/null 2>&1; then
    ok "${img} pulled"
    return 0
  fi
  fail "could not pull ${img}"
  hint "check registry credentials, or run 'docker pull ${img}' manually for the full error"
  return 4
}

pull_one "${RUNNER_IMAGE}"
# Sandbox images pull lazily on first /code/run, but pre-pulling them
# avoids a multi-second stall on the first execution attempt.
pull_one "${SANDBOX_PYTHON}"
pull_one "${SANDBOX_NODE}"
pull_one "${SANDBOX_UTILS}"

# ---- 3. Workspace directory ---------------------------------------------
step "Preparing workspace directory"

mkdir -p "${MATRIXLAB_JOBS_DIR}"
# 0777 because the runner writes as root and sandbox containers may
# run as non-root users with different uids — same recipe the
# docker-compose recipe uses.
chmod 0777 "${MATRIXLAB_JOBS_DIR}" || true
ok "${MATRIXLAB_JOBS_DIR} ready"

# ---- 4. Start the runner -------------------------------------------------
step "Starting MatrixLab runner"

# Bring the runner up.  Wrapped in a function so the OPTIONAL mode
# (chained from ``make install``) can intercept any failure with a
# friendly skip-or-die instead of a raw docker stack trace.  The
# function returns 0 on success and a non-zero code on failure.
__run_runner_container() {
  docker run -d --name "${MATRIXLAB_CONTAINER}" \
    --restart unless-stopped \
    -p "${MATRIXLAB_PORT}:8000" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${MATRIXLAB_JOBS_DIR}:${MATRIXLAB_JOBS_DIR}" \
    -e "MATRIXLAB_LOCAL_JOBS_DIR=${MATRIXLAB_JOBS_DIR}" \
    -e "MATRIXLAB_HOST_JOBS_DIR=${MATRIXLAB_JOBS_DIR}" \
    -e "MATRIXLAB_IMAGE_NAMESPACE=${MATRIXLAB_NAMESPACE}" \
    "${RUNNER_IMAGE}" >/dev/null 2>&1
}

existing_status=$(docker inspect --format '{{.State.Status}}' "${MATRIXLAB_CONTAINER}" 2>/dev/null || true)

if [ "${existing_status}" = "running" ]; then
  ok "container ${MATRIXLAB_CONTAINER} already running"
elif [ -n "${existing_status}" ]; then
  ok "container ${MATRIXLAB_CONTAINER} exists (status=${existing_status}) — starting"
  start_err=$(docker start "${MATRIXLAB_CONTAINER}" 2>&1 >/dev/null || true)
  if [ -n "${start_err}" ]; then
    warn "docker start failed: ${start_err}"
    # A container in state=created/exited that won't start usually
    # means the published port slipped to another container after the
    # previous attempt died.  The container has no state worth
    # preserving — recreate it cleanly.  This is the WSL/Docker
    # Desktop failure mode the user hit; auto-recovery beats a
    # confusing make-failure for an empty container.
    case "${existing_status}" in
      created|exited|dead)
        hint "auto-recovery: removing the stale container and recreating it"
        docker rm -f "${MATRIXLAB_CONTAINER}" >/dev/null 2>&1 || true
        if __run_runner_container; then
          ok "container ${MATRIXLAB_CONTAINER} recreated"
        else
          # The port is genuinely held by someone else — surface
          # who, then bail through the friendly path.
          holder="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
                     | awk -F'\t' -v p=":${MATRIXLAB_PORT}->" '$2 ~ p { print $1; exit }')"
          if [ -n "${holder}" ] && [ "${holder}" != "${MATRIXLAB_CONTAINER}" ]; then
            __skip_or_die "port ${MATRIXLAB_PORT} held by container ${holder}" \
              "Stop it:  docker stop ${holder}" \
              "or set:  MATRIXLAB_PORT=<other> make install-matrixlab"
          else
            __skip_or_die "could not start MatrixLab runner after auto-recovery" \
              "Inspect with:  docker logs ${MATRIXLAB_CONTAINER} --tail 50" \
              "or set:  MATRIXLAB_PORT=<other> make install-matrixlab"
          fi
        fi
        ;;
      *)
        __skip_or_die "container ${MATRIXLAB_CONTAINER} (status=${existing_status}) refused to start" \
          "Inspect with:  docker logs ${MATRIXLAB_CONTAINER} --tail 50"
        ;;
    esac
  fi
else
  if __run_runner_container; then
    ok "container ${MATRIXLAB_CONTAINER} created"
  else
    # Fresh-install failure path — surface the same port-collision
    # diagnosis the recovery path uses so the operator gets the
    # same actionable hint regardless of which branch tripped.
    holder="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
               | awk -F'\t' -v p=":${MATRIXLAB_PORT}->" '$2 ~ p { print $1; exit }')"
    if [ -n "${holder}" ]; then
      __skip_or_die "port ${MATRIXLAB_PORT} held by container ${holder}" \
        "Stop it:  docker stop ${holder}" \
        "or set:  MATRIXLAB_PORT=<other> make install-matrixlab"
    else
      __skip_or_die "docker run failed for ${RUNNER_IMAGE}" \
        "Reproduce manually to see the full error:" \
        "  docker run --rm -p ${MATRIXLAB_PORT}:8000 ${RUNNER_IMAGE}"
    fi
  fi
fi

# ---- 5. Probe /health ----------------------------------------------------
step "Waiting for runner /health"

RUNNER_URL="http://localhost:${MATRIXLAB_PORT}"
for i in $(seq 1 60); do
  if curl -fsS -m 2 "${RUNNER_URL}/health" >/dev/null 2>&1; then
    ok "${RUNNER_URL}/health responding after ${i}s"
    break
  fi
  if [ "${i}" -eq 60 ]; then
    fail "runner did not answer /health within 60s"
    hint "docker logs ${MATRIXLAB_CONTAINER} --tail 50"
    exit 5
  fi
  sleep 1
done

# Pull the version + capabilities to print a friendly summary.
caps=$(curl -fsS -m 5 "${RUNNER_URL}/capabilities" 2>/dev/null || true)
if [ -n "${caps}" ]; then
  version=$(echo "${caps}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'))" 2>/dev/null || echo "?")
  images=$(echo "${caps}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(d.get('images', [])))" 2>/dev/null || echo "?")
  echo
  echo "   Runner version:  ${version}"
  echo "   Sandbox langs:   ${images}"
fi

# ---- 6. Next-step hint ---------------------------------------------------
echo
ok "MatrixLab addon installed and reachable."
echo
echo "Next steps:"
echo "  • Smoke-test:   curl ${RUNNER_URL}/code/run -H 'Content-Type: application/json' \\"
echo "                       -d '{\"language\":\"python\",\"code\":\"print(2+2)\",\"timeout\":30}'"
echo "  • Activate in GitPilot:  open Admin → Sandbox → toggle backend to MatrixLab"
echo "  • Stop the runner:  docker stop ${MATRIXLAB_CONTAINER}"
echo
