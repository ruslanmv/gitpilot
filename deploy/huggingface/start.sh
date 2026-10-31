#!/bin/bash
# =============================================================================
# GitPilot — HF Spaces Startup Script
# =============================================================================
# Starts GitPilot FastAPI server with React frontend on HuggingFace Spaces.
# Pre-configured to use OllaBridge Cloud as the default LLM provider.
# =============================================================================

set -e

# Ensure Python output is not buffered (critical for HF Spaces log visibility)
export PYTHONUNBUFFERED=1

echo ""
echo "=============================================="
echo "  GitPilot — Hugging Face Spaces"
echo "  Started at: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=============================================="
echo ""

# -- Ensure writable directories exist ----------------------------------------
mkdir -p /tmp/gitpilot /tmp/gitpilot/workspaces /tmp/gitpilot/sessions
export HOME=/tmp
export GITPILOT_CONFIG_DIR=/tmp/gitpilot

# -- Display configuration ---------------------------------------------------
echo "[1/2] Configuration:"
echo "       Provider: ${GITPILOT_PROVIDER:-ollabridge}"
echo "       OllaBridge URL: ${OLLABRIDGE_BASE_URL:-https://app.ollabridge.com}"
echo "       Model: ${GITPILOT_OLLABRIDGE_MODEL:-qwen2.5:1.5b}"
echo ""

# -- Check OllaBridge Cloud connectivity (non-blocking) ----------------------
echo "[2/2] Checking LLM provider..."
if curl -sf --connect-timeout 5 --max-time 10 "${OLLABRIDGE_BASE_URL:-https://app.ollabridge.com}/health" > /dev/null 2>&1; then
    echo "       OllaBridge Cloud is reachable"
else
    echo "       OllaBridge Cloud not reachable (will retry on first request)"
    echo "       You can configure a different provider in Admin / LLM Settings"
fi
echo ""

echo "=============================================="
echo "  Ready! Endpoints:"
echo "  - UI:        / (React frontend)"
echo "  - API:       /api/health (+ /api/health/deep)"
echo "  - API Docs:  /docs"
echo "  - Chat:      /api/chat/message"
echo "  - Settings:  /api/settings"
echo "=============================================="
echo ""

# -- Start GitPilot (foreground) ----------------------------------------------
# --workers 2: prevent one stuck request from blocking the entire app
# --limit-concurrency 200: counts open connections, idle keep-alive ones
#   included; at 10 a scanner burst plus polling turned chat streams into 503s
# --timeout-keep-alive 75: longer than the proxy's idle timeout, so the proxy
#   never reuses a connection uvicorn has just closed (that would be a 502)
exec python -m uvicorn gitpilot.api:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-7860}" \
    --workers 2 \
    --limit-concurrency 200 \
    --timeout-keep-alive 75 \
    --no-access-log
