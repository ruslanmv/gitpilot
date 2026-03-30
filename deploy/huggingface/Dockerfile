# =============================================================================
# GitPilot - Hugging Face Spaces Dockerfile
# =============================================================================
# Deploys GitPilot (FastAPI backend + React frontend) as a single container
# with OllaBridge Cloud integration for LLM inference.
#
# Architecture:
#   React UI (Vite build) -> FastAPI backend -> OllaBridge Cloud / any LLM
# =============================================================================

# -- Stage 1: Build React frontend -------------------------------------------
FROM node:20-slim AS frontend-builder

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --production=false

COPY frontend/ ./
RUN npm run build

# -- Stage 2: Python runtime -------------------------------------------------
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser && \
    mkdir -p /app /tmp/gitpilot && \
    chown -R appuser:appuser /app /tmp/gitpilot

WORKDIR /app

COPY pyproject.toml README.md ./
COPY gitpilot ./gitpilot

# Copy built frontend into gitpilot/web/
COPY --from=frontend-builder /build/dist/ ./gitpilot/web/

# Install Python dependencies in a single pip-resolve pass.
# Step 1: install the lightweight, fast-to-resolve deps (cached layer).
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.30.0" \
        "httpx>=0.27.0" \
        "python-dotenv>=1.1.0" \
        "typer>=0.12.0" \
        "pydantic>=2.7.0" \
        "rich>=13.0.0" \
        "pyjwt[crypto]>=2.8.0"

# Step 2: install the heavy ML/agent deps (separate layer for better caching).
RUN pip install --no-cache-dir \
        "litellm" \
        "crewai[anthropic]>=0.76.9" \
        "crewai-tools>=0.13.4" \
        "anthropic>=0.39.0" \
        "ibm-watsonx-ai>=1.1.0" \
        "langchain-ibm>=0.3.0"

# Step 3: editable install of gitpilot itself (deps already satisfied).
RUN pip install --no-cache-dir --no-deps -e .

COPY deploy/huggingface/start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV PORT=7860 \
    HOST=0.0.0.0 \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    GITPILOT_PROVIDER=ollabridge \
    OLLABRIDGE_BASE_URL=https://ruslanmv-ollabridge.hf.space \
    GITPILOT_OLLABRIDGE_MODEL=qwen2.5:1.5b \
    CORS_ORIGINS="*" \
    GITPILOT_CONFIG_DIR=/tmp/gitpilot

USER appuser
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:7860/api/health || exit 1

CMD ["/app/start.sh"]
