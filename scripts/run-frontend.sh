#!/usr/bin/env bash
# scripts/run-frontend.sh — start the Vite dev server, on any filesystem.
#
# `npm run dev` shells out to node_modules/.bin/vite, which is a symlink that
# has to be executable. On a Windows checkout used from WSL (/mnt/c/...), on a
# volume mounted `noexec`, or when `npm install` was run from the other OS, that
# bit is missing and npm fails with:
#
#     sh: 1: vite: Permission denied
#
# The bit is a property of the mount, not of the project, so "reinstall your
# dependencies" does not fix it and neither does chmod on a drvfs mount. The
# reliable path is to skip the launcher entirely and hand Vite's entry script to
# node, which needs no exec bit. Same Vite, same config, same flags.
#
# Order: the normal launcher first (so nothing changes where it already works),
# then node directly, then a clear explanation. Never a bare "Permission denied".

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${FRONTEND_DIR:-${ROOT_DIR}/frontend}"

cd "${FRONTEND_DIR}" || { printf '❌ %s\n' "no frontend/ directory at ${FRONTEND_DIR}"; exit 1; }

VITE_BIN="node_modules/.bin/vite"
VITE_ENTRY="node_modules/vite/bin/vite.js"

if [[ ! -d node_modules ]]; then
  printf '❌ %s\n' "frontend dependencies are missing. Run: make frontend-install"
  exit 1
fi

# The launcher works: use it, so behaviour is unchanged on healthy checkouts.
if [[ -x "${VITE_BIN}" ]]; then
  exec npm run dev -- "$@"
fi

# It exists but is not executable — the /mnt/c case. Try to repair the bit
# (harmless and permanent on a normal filesystem), then fall back to node,
# which does not care either way.
if [[ -e "${VITE_BIN}" ]]; then
  chmod +x "${VITE_BIN}" 2>/dev/null || true
  if [[ -x "${VITE_BIN}" ]]; then
    exec npm run dev -- "$@"
  fi
  printf '⚠️  %s\n' "node_modules/.bin/vite is not executable (a Windows/WSL or noexec mount)."
  printf '   %s\n' "Starting Vite through node instead — same server, no exec bit needed."
fi

if [[ -f "${VITE_ENTRY}" ]]; then
  # --host mirrors the "dev" script in package.json; extra flags pass through.
  exec node "${VITE_ENTRY}" --host "$@"
fi

printf '❌ %s\n' "Vite is not installed in frontend/node_modules."
printf '   %s\n' "Run: make frontend-install"
exit 1
