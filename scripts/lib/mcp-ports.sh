# scripts/lib/mcp-ports.sh — pick host ports for the MCP stack.
#
# Sourced by scripts/mcp-stack.sh. Also runnable on its own:
#
#     bash scripts/lib/mcp-ports.sh --backend-env
#
# prints the environment the GitPilot backend needs to find a Forge that was
# moved off its default port (used by `make run`).
#
# The rule: a port our own container already publishes is kept; a port anything
# else holds moves the service to the next free port, and the move is saved to
# .mcp.env so every later command (register, stop, logs, smoke) agrees on it.
# Never stops or touches whatever holds the port.

MCP_ENV_FILE="${MCP_ENV_FILE:-.mcp.env}"
MCP_PORT_SEARCH_SPAN="${MCP_PORT_SEARCH_SPAN:-100}"

# Is something already accepting on (or publishing) this host port?
mcp_port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1 \
      && ss -Hltn "sport = :${port}" 2>/dev/null | grep -q .; then
    return 0
  fi
  # Docker Desktop publishes on the host VM, which `ss` may not see.
  if command -v docker >/dev/null 2>&1 \
      && docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->"; then
    return 0
  fi
  # Last resort where `ss` is missing (macOS): can we connect?
  (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && return 0
  return 1
}

# Does our own container already publish this port? Then it is ours to reuse.
mcp_port_held_by() {
  local container="$1" port="$2"
  command -v docker >/dev/null 2>&1 || return 1
  docker ps --filter "name=^/${container}$" --format '{{.Ports}}' 2>/dev/null \
    | grep -q ":${port}->"
}

# Write VAR=VALUE into .mcp.env, replacing an existing line or appending one.
mcp_save_env() {
  local var="$1" value="$2" tmp
  [[ -f "${MCP_ENV_FILE}" ]] || return 0
  tmp="$(mktemp "${MCP_ENV_FILE}.XXXXXX")" || return 1
  awk -v var="${var}" -v value="${value}" '
    BEGIN { done = 0 }
    $0 ~ "^" var "=" { print var "=" value; done = 1; next }
    { print }
    END { if (!done) print var "=" value }
  ' "${MCP_ENV_FILE}" > "${tmp}" && mv "${tmp}" "${MCP_ENV_FILE}"
}

# Decide the host port for one service and export it as VAR.
#   mcp_claim_port MCP_FORGE_PORT 4444 gitpilot-mcp-context-forge
mcp_claim_port() {
  local var="$1" default="$2" container="$3"
  local want="${!var:-${default}}" port last
  if mcp_port_held_by "${container}" "${want}" || ! mcp_port_in_use "${want}"; then
    export "${var}=${want}"
    return 0
  fi
  last=$(( want + MCP_PORT_SEARCH_SPAN ))
  for (( port = want + 1; port <= last; port++ )); do
    if ! mcp_port_in_use "${port}"; then
      printf '⚠️  Port %s is in use — %s will publish on %s instead.\n' \
        "${want}" "${container#gitpilot-}" "${port}"
      export "${var}=${port}"
      mcp_save_env "${var}" "${port}" \
        && printf '   Saved %s=%s to %s so the other MCP commands use it too.\n' \
          "${var}" "${port}" "${MCP_ENV_FILE}"
      return 0
    fi
  done
  printf '❌ No free port in %s-%s for %s. Free one, or set %s in %s.\n' \
    "${want}" "${last}" "${container#gitpilot-}" "${var}" "${MCP_ENV_FILE}"
  return 1
}

# What the backend needs to reach a Forge that moved off :4444.
#
# Printed as VAR=value words for `env`. Nothing is printed when the Forge is on
# its default port, or when the operator already chose a URL, so the backend's
# own default stays in charge exactly as before.
mcp_backend_env() {
  [[ -f "${MCP_ENV_FILE}" ]] || return 0
  local port url
  port="$(sed -n 's/^MCP_FORGE_PORT=\([0-9][0-9]*\)[[:space:]]*$/\1/p' "${MCP_ENV_FILE}" | tail -1)"
  url="$(sed -n 's/^MCP_FORGE_URL=\(.*\)$/\1/p' "${MCP_ENV_FILE}" | tail -1)"
  [[ -n "${port}" ]] && printf 'MCP_FORGE_PORT=%s ' "${port}"
  if [[ -z "${GITPILOT_MCP_FORGE_URL:-}" ]]; then
    if [[ -n "${url}" ]]; then
      printf 'GITPILOT_MCP_FORGE_URL=%s ' "${url%/}"
    elif [[ -n "${port}" && "${port}" != "4444" ]]; then
      printf 'GITPILOT_MCP_FORGE_URL=http://localhost:%s ' "${port}"
    fi
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    --backend-env) mcp_backend_env ;;
    *) printf 'usage: %s --backend-env\n' "$(basename "$0")" >&2; exit 2 ;;
  esac
fi
