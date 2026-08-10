#!/usr/bin/env bash
# scripts/install-cli.sh
#
# Make `gitpilot` on PATH be this checkout.
#
# `make install` prepares .venv, which `make run` uses. It deliberately does
# not touch PATH — installing a command outside the project is the kind of
# thing that should be asked for, not done to you. This is the asking.
#
# Editable on purpose: the point is that `gitpilot serve` picks up what you
# just edited, so the install has to track the tree rather than snapshot it.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ -t 1 ]; then
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m'); DIM=$(printf '\033[2m'); RESET=$(printf '\033[0m')
else
  GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
fi

printf "\n🔗 Installing the 'gitpilot' command from this checkout\n"

PREVIOUS="$(command -v gitpilot 2>/dev/null || true)"
[ -n "${PREVIOUS}" ] && printf "   ${DIM}replacing: %s${RESET}\n" "${PREVIOUS}"

installed=0

# uv first: it owns this project's environment, and `uv tool` is its answer
# for "put this CLI on my PATH". --force because the whole reason we are
# here is that something else already holds the name.
if command -v uv >/dev/null 2>&1; then
  printf "   using uv tool install (editable)...\n"
  if uv tool install --force --editable . >/dev/null 2>&1; then
    installed=1
  else
    printf "   ${YELLOW}⚠${RESET}  uv tool install failed; trying pip.\n"
  fi
fi

# pip fallback, into the same user site the shadowing release came from, so
# the deps already there are reused rather than duplicated.
if [ "${installed}" -eq 0 ]; then
  PY="$(command -v python3 || command -v python || true)"
  if [ -z "${PY}" ]; then
    printf "   ${RED}✗${RESET} No uv and no python on PATH — cannot install.\n"
    exit 1
  fi
  printf "   using %s -m pip install --user -e .\n" "${PY}"
  if ! "${PY}" -m pip install --user -e . ; then
    printf "\n   ${RED}✗${RESET} Install failed.\n"
    printf "      ${DIM}'make run' still works — it uses .venv and needs none of this.${RESET}\n"
    exit 1
  fi
fi

printf "   ${GREEN}✓${RESET} installed\n"

# Say what actually happened rather than asserting success. If the shell has
# the old path cached, or the tool bin dir is not on PATH, the check below
# is what tells you.
hash -r 2>/dev/null || true
bash "${SCRIPT_DIR}/check-cli-version.sh"

printf "   ${DIM}Still showing the old version?  Open a new shell (the old path is cached),${RESET}\n"
printf "   ${DIM}and make sure uv's tool dir is on PATH:  uv tool update-shell${RESET}\n\n"
