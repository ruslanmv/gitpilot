#!/usr/bin/env bash
# scripts/install-mcp.sh
#
# Prepare the MCP Context Forge environment so `make run-mcp` is one
# command. Strategy mirrors HomePilot's mcp-servers stack: build each
# image from a cloned upstream rather than pulling pre-built images
# from a registry. The four upstreams are cloned (or fetched + checked
# out) under ./mcp-stack/.
#
# Idempotent: safe to re-run; on a fully-warm system every step prints
# a status glyph so you can see the script actually executed.
#
# Three execution paths:
#
#   1. Docker not installed         -> friendly skip, exit 0
#   2. Docker installed, daemon DOWN -> friendly skip with "start docker"
#                                       hint, exit 0
#   3. Docker daemon UP             -> seed config, clone/fetch upstreams,
#                                       (optionally) `compose build`
#
# Make never breaks: every path returns exit 0 so `make install` keeps
# working on hosts where Docker is unavailable.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

# --- pretty printing -------------------------------------------------------
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '   %s\n' "$*"; }
step()  { printf '🔹 %s\n' "$*"; }
ok()    { printf '✅ %s\n' "$*"; }
skip()  { printf '➖ %s\n' "$*"; }
warn()  { printf '⚠️  %s\n' "$*"; }

bold "🐳 Preparing MCP Context Forge environment"

# 1. Docker availability ----------------------------------------------------
DOCKER_OK=1
DAEMON_OK=1

if ! command -v docker >/dev/null 2>&1; then
  DOCKER_OK=0
  warn "docker not found. MCP image build will be skipped."
  info "GitPilot itself is fully usable. Install Docker Desktop / engine"
  info "later and re-run: make install-mcp"
elif ! docker compose version >/dev/null 2>&1; then
  DOCKER_OK=0
  warn "docker compose v2 not available. MCP image build will be skipped."
  info "Upgrade to Docker Desktop or install the compose v2 plugin."
elif ! docker info >/dev/null 2>&1; then
  DAEMON_OK=0
  warn "docker daemon is not running. MCP image build will be skipped."
  info "Start Docker Desktop (or 'sudo systemctl start docker') and"
  info "re-run: make install-mcp"
fi

# 2. Seed .mcp.env ----------------------------------------------------------
if [[ ! -f .mcp.env ]]; then
  cp .env.template.mcp .mcp.env
  ok ".mcp.env written from template."
else
  skip ".mcp.env already exists (kept as-is)."
fi

# 3. Mint a real token if the placeholder is still there --------------------
if grep -q "change-me-please-32-bytes-of-randomness" .mcp.env 2>/dev/null; then
  TOKEN=$(openssl rand -hex 32 2>/dev/null \
        || head -c 64 /dev/urandom | base64 | tr -d '/+\n=' | head -c 64)
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$TOKEN" <<'PY'
import pathlib, sys
token = sys.argv[1]
p = pathlib.Path(".mcp.env")
text = p.read_text()
text = text.replace("change-me-please-32-bytes-of-randomness", token, 1)
p.write_text(text)
PY
    ok "Generated a fresh MCP_AUTH_TOKEN."
  else
    sed -i.bak "s|change-me-please-32-bytes-of-randomness|${TOKEN}|" .mcp.env \
      && rm -f .mcp.env.bak \
      && ok "Generated a fresh MCP_AUTH_TOKEN (sed)."
  fi
else
  skip "MCP_AUTH_TOKEN already personalised."
fi

# 4. .gitignore -------------------------------------------------------------
if [[ -f .gitignore ]]; then
  for entry in ".mcp.env" "mcp-stack/"; do
    if grep -qE "^${entry//./\\.}\$" .gitignore 2>/dev/null; then
      skip ".gitignore already excludes ${entry}."
    else
      echo "${entry}" >> .gitignore
      ok ".gitignore now excludes ${entry}."
    fi
  done
fi

# 5. Read .mcp.env so REPO/REF vars are visible -----------------------------
# shellcheck disable=SC1091
set -a
source .mcp.env 2>/dev/null || true
set +a

# 6. Clone / fetch the four upstream repos ----------------------------------
clone_or_update() {
  local name="$1"
  local repo="$2"
  local ref="$3"
  local target="mcp-stack/${name}"

  if ! command -v git >/dev/null 2>&1; then
    warn "git not found; skipping ${name} clone."
    return
  fi

  if [[ -d "${target}/.git" ]]; then
    step "Updating ${name} to ${ref}..."
    git -C "${target}" fetch --quiet --depth 1 origin "${ref}" 2>/dev/null \
      || { warn "fetch failed for ${name} (network?). Keeping current checkout."; return; }
    git -C "${target}" checkout --quiet "FETCH_HEAD" 2>/dev/null \
      && ok "${name}: checked out ${ref} ($(git -C "${target}" rev-parse --short HEAD))" \
      || warn "checkout ${ref} failed for ${name}; leaving working tree intact."
  else
    step "Cloning ${name}@${ref}..."
    if git clone --quiet --depth 1 --branch "${ref}" "${repo}" "${target}" 2>/dev/null; then
      ok "${name}: cloned ${ref} ($(git -C "${target}" rev-parse --short HEAD))"
    else
      warn "Could not clone ${name}@${ref}. Falling back to default branch."
      rm -rf "${target}"
      git clone --quiet --depth 1 "${repo}" "${target}" 2>/dev/null \
        && ok "${name}: cloned default branch" \
        || warn "Clone failed for ${name}. Image build will be unavailable."
    fi
  fi
}

# Hardcoded fallback defaults match .env.template.mcp so that older
# .mcp.env files (without the MCP_*_REF keys) still pick the integration
# branch where the four MCP-stack changes live. Once those changes merge
# upstream, change these defaults to "main" or a release tag.
DEFAULT_REF="claude/add-mcp-context-tools-xt3It"

if [[ "${DOCKER_OK}" -eq 1 ]]; then
  mkdir -p mcp-stack
  clone_or_update "mcp-context-forge"   "${MCP_FORGE_REPO:-https://github.com/ruslanmv/mcp-context-forge.git}"     "${MCP_FORGE_REF:-${DEFAULT_REF}}"
  clone_or_update "mcp-postgre-server"  "${MCP_POSTGRE_REPO:-https://github.com/ruslanmv/mcp-postgre-server.git}"  "${MCP_POSTGRE_REF:-${DEFAULT_REF}}"
  clone_or_update "milvus-admin-ui"     "${MCP_MILVUS_REPO:-https://github.com/ruslanmv/milvus-admin-ui.git}"      "${MCP_MILVUS_REF:-${DEFAULT_REF}}"
  clone_or_update "mcp-inspector-server" "${MCP_INSPECTOR_REPO:-https://github.com/ruslanmv/mcp-inspector-server.git}" "${MCP_INSPECTOR_REF:-${DEFAULT_REF}}"
else
  skip "Docker unavailable; upstream clones skipped (run after starting Docker)."
fi

# 7. Optionally pre-build the images so `make run-mcp` is fast --------------
if [[ "${DOCKER_OK}" -eq 1 && "${DAEMON_OK}" -eq 1 && -d mcp-stack/mcp-context-forge ]]; then
  step "Pre-building MCP images (one-time cost; subsequent runs reuse cache)..."
  if docker compose --env-file .mcp.env -f docker-compose.mcp.yml \
        --profile mcp build --quiet 2>&1 | tail -10; then
    ok "Image build complete."
  else
    warn "One or more images failed to build. Run 'make run-mcp' to see details."
  fi
else
  skip "Image build skipped (will happen on first 'make run-mcp')."
fi

# 8. Final summary ----------------------------------------------------------
echo
bold "✨ MCP environment ready."
if [[ "${DOCKER_OK}" -eq 1 && "${DAEMON_OK}" -eq 1 ]]; then
  info "Next: 'make run-mcp' to start Forge + 3 reference servers,"
  info "      then 'make run' to start GitPilot."
  info "      Or 'make run-all' to do both."
else
  info "Next: install / start Docker, then 'make install-mcp && make run-all'."
  info "      'make run' alone still works without the MCP stack."
fi

# Always exit 0 so the make pipeline keeps going.
exit 0
