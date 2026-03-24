#!/bin/bash
# =============================================================================
# GitPilot — HF Spaces Startup Script
# =============================================================================
# Starts GitPilot FastAPI server with React frontend on HuggingFace Spaces.
# Pre-configured to use OllaBridge Cloud as the default LLM provider.
# =============================================================================

set -e

echo "=============================================="
echo "  GitPilot — Hugging Face Spaces"
echo "=============================================="
echo ""

# -- Ensure writable directories exist ----------------------------------------
mkdir -p /tmp/gitpilot /tmp/gitpilot/workspaces /tmp/gitpilot/sessions
export HOME=/tmp
export GITPILOT_CONFIG_DIR=/tmp/gitpilot

# -- Display configuration ---------------------------------------------------
echo "[1/2] Configuration:"
echo "       Provider: ${GITPILOT_PROVIDER:-ollabridge}"
echo "       OllaBridge URL: ${OLLABRIDGE_BASE_URL:-https://ruslanmv-ollabridge.hf.space}"
echo "       Model: ${GITPILOT_OLLABRIDGE_MODEL:-qwen2.5:1.5b}"
echo ""

# -- Check OllaBridge Cloud connectivity (non-blocking) ----------------------
echo "[2/2] Checking LLM provider..."
if curl -sf "${OLLABRIDGE_BASE_URL:-https://ruslanmv-ollabridge.hf.space}/health" > /dev/null 2>&1; then
    echo "       OllaBridge Cloud is reachable"
else
    echo "       OllaBridge Cloud not reachable (will retry on first request)"
    echo "       You can configure a different provider in Admin / LLM Settings"
fi
echo ""

echo "=============================================="
echo "  Ready! Endpoints:"
echo "  - UI:        / (React frontend)"
echo "  - API:       /api/health"
echo "  - API Docs:  /docs"
echo "  - Chat:      /api/chat/message"
echo "  - Settings:  /api/settings"
echo "=============================================="
echo ""

# -- Start GitPilot (foreground) ----------------------------------------------
exec python -m uvicorn gitpilot.api:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-7860}" \
    --workers 1 \
    --timeout-keep-alive 120 \
    --no-access-log
