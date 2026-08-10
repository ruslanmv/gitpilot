#!/usr/bin/env bash
# scripts/check-cli-version.sh
#
# Which GitPilot does `gitpilot` actually run?
#
# `make install` syncs the checkout into .venv, and `make run` uses it. But
# typing `gitpilot serve` runs whatever is first on PATH, which on a machine
# that ever did `pip install gitcopilot` is a released wheel under
# ~/.local/lib/pythonX.Y/site-packages/gitpilot/ — a different version, with
# different dependencies, and none of the local changes.
#
# Nothing warns you. The two only differ in a version number printed inside
# a banner, so a fix that is definitely in the tree appears not to work, and
# the traceback names a path nobody thinks to read.
#
# This prints the comparison and, when they differ, the command that fixes
# it. Never fails the build: shadowing is a valid setup if you know about
# it, and `make install` should not refuse to finish over it.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); DIM=$(printf '\033[2m'); RESET=$(printf '\033[0m')
else
  GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

REPO_PKG="${REPO_ROOT}/gitpilot"

# Ask an interpreter where it would import gitpilot from, and what version
# that copy reports. Printed as "<version>|<package dir>"; empty on failure.
#
# Run from somewhere other than the repo. `python -c` puts the working
# directory on sys.path, so probing from the repo root imports ./gitpilot
# no matter which interpreter is asked — every install then looks like the
# checkout, which is the one answer this script must never give wrongly.
probe() {
  (cd "${TMPDIR:-/tmp}" 2>/dev/null || cd /; "$1" -c '
import pathlib
try:
    import gitpilot
    print(f"{gitpilot.__version__}|{pathlib.Path(gitpilot.__file__).resolve().parent}")
except Exception:
    pass
' 2>/dev/null)
}

# The interpreter behind a console script, read from its shebang. Console
# scripts are generated wrappers, so this is stable across pip and uv.
interpreter_of() {
  local shebang
  shebang="$(head -n 1 "$1" 2>/dev/null)" || return 1
  case "${shebang}" in
    '#!'*) printf '%s\n' "${shebang#\#!}" | awk '{print $1}' ;;
    *) return 1 ;;
  esac
}

printf "\n🔎 Checking which GitPilot the 'gitpilot' command runs\n"

CLI_PATH="$(command -v gitpilot 2>/dev/null || true)"

if [ -z "${CLI_PATH}" ]; then
  printf "  ${DIM}➖${RESET} No 'gitpilot' on PATH — 'make run' and 'make serve' use the checkout, so nothing is wrong.\n"
  printf "     ${DIM}Want the command available everywhere?  make install-cli${RESET}\n"
  exit 0
fi

CLI_PY="$(interpreter_of "${CLI_PATH}" || true)"
CLI_INFO=""
[ -n "${CLI_PY}" ] && [ -x "${CLI_PY}" ] && CLI_INFO="$(probe "${CLI_PY}")"

if [ -z "${CLI_INFO}" ]; then
  printf "  ${YELLOW}⚠${RESET}  Found %s but could not determine which package it imports.\n" "${CLI_PATH}"
  printf "     ${DIM}Check by hand:  gitpilot version${RESET}\n"
  exit 0
fi

CLI_VERSION="${CLI_INFO%%|*}"
CLI_PKG="${CLI_INFO##*|}"

if [ "${CLI_PKG}" = "$(cd "${REPO_PKG}" 2>/dev/null && pwd -P)" ]; then
  printf "  ${GREEN}✓${RESET} gitpilot v%s → this checkout\n" "${CLI_VERSION}"
  printf "     ${DIM}%s${RESET}\n" "${CLI_PATH}"
  exit 0
fi

printf "  ${YELLOW}⚠${RESET}  'gitpilot' on PATH is NOT this checkout.\n\n"
printf "       on PATH:  v%-10s %s\n" "${CLI_VERSION}" "${CLI_PKG}"
printf "     this repo:  %s\n\n" "${REPO_PKG}"
printf "     So 'gitpilot serve' runs the installed release, not your code —\n"
printf "     different version, different dependencies, none of your changes.\n"
printf "     ('make run' and 'make serve' are unaffected; they use .venv.)\n\n"
printf "     Point the command at this checkout:\n"
printf "       ${GREEN}make install-cli${RESET}\n\n"
exit 0
