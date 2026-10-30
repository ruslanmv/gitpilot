# deploy/mcp-forge/sdk-pin.Dockerfile
#
# A one-layer fix on top of the Forge image built from its upstream Containerfile.
# Applied by scripts/lib/forge-image.sh only when the image's Forge code imports
# `streamablehttp_client` and its installed MCP SDK (2.x) no longer has it.
ARG FORGE_IMAGE=gitpilot/mcp-context-forge:local
FROM ${FORGE_IMAGE}

ARG MCP_SDK_SPEC="mcp>=1.25.0,<2"

# /app is owned by the image's runtime user, so no USER switch is needed.
RUN /app/.venv/bin/python3 -m uv pip install --no-cache \
        --python /app/.venv/bin/python3 "${MCP_SDK_SPEC}" \
    && /app/.venv/bin/python3 -c "from mcp.client.streamable_http import streamablehttp_client"
