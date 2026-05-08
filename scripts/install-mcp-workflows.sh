#!/usr/bin/env bash
# scripts/install-mcp-workflows.sh
#
# Drop the docker-publish workflow into each cloned MCP-server repo
# under mcp-stack/, commit, and (if GH_PAT_WORKFLOW is set) push.
#
# Why this script exists: GitHub PATs without the `workflow` scope
# refuse to write under .github/workflows/. Rather than ask every
# contributor to mint a special PAT just to add CI files, this script
# stages the commits with the existing repo auth and prints the push
# command if it can't push itself.
#
# Required: GH_PAT_WORKFLOW=<token-with-repo+workflow-scopes>
#           (mint at https://github.com/settings/tokens?type=beta)
#
# Idempotent: safe to re-run; if the workflow file already matches the
# template, the per-repo step is a no-op.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATES_DIR="${ROOT_DIR}/extensions/mcp_workflows"
STACK_DIR="${ROOT_DIR}/mcp-stack"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
step() { printf '🔹 %s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
skip() { printf '➖ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }

bold "🛠  Installing docker-publish workflows into MCP-server repos"

if [[ ! -d "${STACK_DIR}" ]]; then
  warn "mcp-stack/ not found. Run 'make install-mcp' first to clone the repos."
  exit 0
fi

# repo_name -> (template basename, target path inside repo)
declare -A REPOS=(
  ["mcp-postgre-server"]="mcp-postgre-server.docker-publish.yml"
  ["mcp-inspector-server"]="mcp-inspector-server.docker-publish.yml"
  ["milvus-admin-ui"]="mcp-milvus-server.docker-publish.yml"
)

install_workflow() {
  local repo="$1"
  local template_basename="$2"
  local repo_dir="${STACK_DIR}/${repo}"
  local template="${TEMPLATES_DIR}/${template_basename}"
  local target_dir="${repo_dir}/.github/workflows"
  local target="${target_dir}/docker-publish.yml"

  if [[ ! -d "${repo_dir}/.git" ]]; then
    warn "${repo}: not cloned (skip). Run 'make install-mcp' first."
    return
  fi
  if [[ ! -f "${template}" ]]; then
    warn "${repo}: template missing at ${template} (skip)."
    return
  fi

  mkdir -p "${target_dir}"
  if [[ -f "${target}" ]] && cmp -s "${template}" "${target}"; then
    skip "${repo}: workflow already up to date."
    return
  fi

  cp "${template}" "${target}"
  step "${repo}: workflow copied; committing..."

  (
    cd "${repo_dir}"
    git add .github/workflows/docker-publish.yml
    if git diff --cached --quiet; then
      skip "${repo}: nothing to commit."
      return 0
    fi
    git -c commit.gpgsign=false commit \
      -m "ci: publish Docker image on release / workflow_dispatch" \
      -m "Sibling to existing CI; same shape as the other MCP-stack publish workflows so all four images carry consistent tags and labels." \
      --quiet || warn "${repo}: commit failed."
    ok "${repo}: committed."

    if [[ -n "${GH_PAT_WORKFLOW:-}" ]]; then
      step "${repo}: pushing with GH_PAT_WORKFLOW..."
      local origin
      origin="$(git remote get-url origin 2>/dev/null || echo '')"
      if [[ -z "${origin}" ]]; then
        warn "${repo}: no origin remote; cannot push automatically."
        return 0
      fi
      # Insert the token into the URL only for the duration of this push.
      local push_url="${origin/https:\/\//https:\/\/x-access-token:${GH_PAT_WORKFLOW}@}"
      if git push "${push_url}" HEAD 2>&1 | tail -3; then
        ok "${repo}: pushed."
      else
        warn "${repo}: push failed. Run: git -C \"${repo_dir}\" push"
      fi
    else
      info "GH_PAT_WORKFLOW not set; commit only. To push:"
      info "    cd ${repo_dir} && git push"
    fi
  )
}

for repo in "${!REPOS[@]}"; do
  install_workflow "${repo}" "${REPOS[$repo]}"
done

echo
bold "✨ Done."
if [[ -z "${GH_PAT_WORKFLOW:-}" ]]; then
  info "Set GH_PAT_WORKFLOW=<token-with-repo+workflow-scopes> and re-run to push automatically."
fi
exit 0
