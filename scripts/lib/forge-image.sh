# scripts/lib/forge-image.sh — keep the Forge image's MCP SDK compatible.
#
# Sourced by scripts/install-mcp.sh and scripts/mcp-stack.sh.
#
# The Forge Containerfile installs with `uv pip install ".[...]"`, which ignores
# the checkout's uv.lock and resolves `mcp>=1.25.0` to the newest release. MCP
# SDK 2.x renamed `streamablehttp_client` to `streamable_http_client`, so a Forge
# checkout that still imports the old name builds fine and then crash-loops at
# startup with an ImportError — Compose reports that as "container is unhealthy".
#
# The check asks the image itself: does its Forge source use the old name, and
# does its installed SDK lack it? Only then is a one-line layer added that pins
# the SDK below 2. A newer Forge that uses the new name is left alone.

FORGE_IMAGE="${FORGE_IMAGE:-gitpilot/mcp-context-forge:local}"
FORGE_SDK_SPEC="${MCP_FORGE_SDK_SPEC:-mcp>=1.25.0,<2}"

_forge_image_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORGE_SDK_PIN_DOCKERFILE="${_forge_image_lib_dir}/../../deploy/mcp-forge/sdk-pin.Dockerfile"

# Exit 0 = compatible, 3 = needs the pin, anything else = could not tell.
forge_image_sdk_status() {
  docker run --rm --entrypoint /app/.venv/bin/python3 "${FORGE_IMAGE}" -c '
import pathlib, sys
src = pathlib.Path("/app/mcpgateway")
uses_old = any("streamablehttp_client" in p.read_text(errors="ignore") for p in src.rglob("*.py"))
if not uses_old:
    sys.exit(0)
import mcp.client.streamable_http as http
sys.exit(0 if hasattr(http, "streamablehttp_client") else 3)
' >/dev/null 2>&1
}

# Make the image start, or say why it cannot. Returns non-zero only when the
# image is known to be broken and the pin could not be applied.
forge_image_ensure_sdk() {
  docker image inspect "${FORGE_IMAGE}" >/dev/null 2>&1 || return 0
  local status=0
  forge_image_sdk_status || status=$?
  case "${status}" in
    0) return 0 ;;
    3) ;;
    *) printf '⚠️  Could not check the MCP SDK in %s (exit %s); starting it as built.\n' \
         "${FORGE_IMAGE}" "${status}"
       return 0 ;;
  esac

  printf '🔧 %s has an MCP SDK too new for its Forge code; pinning %s...\n' \
    "${FORGE_IMAGE}" "${FORGE_SDK_SPEC}"
  if docker build --quiet \
       --build-arg "FORGE_IMAGE=${FORGE_IMAGE}" \
       --build-arg "MCP_SDK_SPEC=${FORGE_SDK_SPEC}" \
       -t "${FORGE_IMAGE}" -f "${FORGE_SDK_PIN_DOCKERFILE}" \
       "$(dirname "${FORGE_SDK_PIN_DOCKERFILE}")" >/dev/null; then
    printf '✅ MCP SDK pinned in %s.\n' "${FORGE_IMAGE}"
    return 0
  fi
  printf '❌ Could not pin the MCP SDK in %s. Rebuild with MCP_BUILD=1, or set MCP_FORGE_REF to a Forge release that supports mcp 2.x.\n' \
    "${FORGE_IMAGE}"
  return 1
}
