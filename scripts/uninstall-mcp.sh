#!/usr/bin/env bash
# scripts/uninstall-mcp.sh
#
# Tear down the MCP Context Forge stack. Prompts unless --yes is given.
# Removes containers, volumes, and pulled images for the mcp profile.
# Does NOT touch GitPilot core, the .mcp.env file, or any committed config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

ASSUME_YES=false
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=true

cat <<EOF
This will remove the MCP Context Forge stack:
  - containers from docker-compose.mcp.yml (mcp profile)
  - named volumes (forge-data, postgre-data) — local-only data is lost
  - the gitpilot-mcp network

It will NOT remove:
  - GitPilot core
  - .mcp.env (delete it yourself if you want to forget the token)
  - committed configuration

EOF

if ! $ASSUME_YES; then
  read -r -p "Proceed? [y/N] " yn
  case "$yn" in
    [Yy]*) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not installed; nothing to uninstall." >&2
  exit 0
fi

# `--volumes` removes the named volumes; `--remove-orphans` cleans stragglers.
docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp down --volumes --remove-orphans || true

# Best-effort image removal (ignores "no such image" errors).
for img in \
  "${MCP_FORGE_IMAGE:-ruslanmv/mcp-context-forge}:${MCP_FORGE_TAG:-latest}" \
  "${MCP_POSTGRE_IMAGE:-ruslanmv/mcp-postgre-server}:${MCP_POSTGRE_TAG:-latest}" \
  "${MCP_MILVUS_IMAGE:-ruslanmv/milvus-admin-ui}:${MCP_MILVUS_TAG:-latest}" \
  "${MCP_INSPECTOR_IMAGE:-ruslanmv/mcp-inspector-server}:${MCP_INSPECTOR_TAG:-latest}"; do
  docker rmi "$img" 2>/dev/null || true
done

echo "✅ MCP environment removed."
