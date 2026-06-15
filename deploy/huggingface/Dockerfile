# =============================================================================
# GitPilot - Hugging Face Spaces Dockerfile
# =============================================================================
# Follows the official HF Docker Spaces pattern:
#   https://huggingface.co/docs/hub/spaces-sdks-docker
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

# System deps needed at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces runs containers as UID 1000 — create user early (official pattern)
RUN useradd -m -u 1000 user

USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    GITPILOT_PROVIDER=ollabridge \
    OLLABRIDGE_BASE_URL=https://ruslanmv-ollabridge.hf.space \
    GITPILOT_OLLABRIDGE_MODEL=qwen2.5:1.5b \
    CORS_ORIGINS="*" \
    GITPILOT_CONFIG_DIR=/tmp/gitpilot

WORKDIR $HOME/app

# ── Install Python dependencies BEFORE copying source code ──────────
# This ensures pip install layers are cached even when code changes.
COPY --chown=user pyproject.toml README.md ./

# Step 1: lightweight deps (cached layer)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.30.0" \
        "httpx>=0.27.0" \
        "python-dotenv>=1.1.0,<1.2.0" \
        "typer>=0.12.0,<0.24.0" \
        "pydantic>=2.7.0,<2.12.0" \
        "rich>=13.0.0" \
        "pyjwt[crypto]>=2.8.0"

# Step 2: heavy ML/agent deps (separate layer for better caching)
RUN pip install --no-cache-dir \
        "litellm" \
        "crewai[anthropic]>=0.76.9" \
        "crewai-tools>=0.13.4" \
        "anthropic>=0.39.0" \
        "ibm-watsonx-ai>=1.1.0" \
        "langchain-ibm>=0.3.0"

# ── Now copy source code (cache-busting only affects layers below) ──
COPY --chown=user gitpilot ./gitpilot

# Copy built frontend into gitpilot/web/
COPY --chown=user --from=frontend-builder /build/dist/ ./gitpilot/web/

# Step 3: editable install of gitpilot itself (deps already satisfied)
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 7860

# NOTE: Do NOT add a Docker HEALTHCHECK here.
# HF Spaces has its own HTTP probe on app_port (7860) and ignores the
# Docker HEALTHCHECK directive.

# Direct CMD — no shell script, fewer failure points.
# Single worker: the Matrix run registry (gitpilot.matrix_runs_router._RUNS) is
# in-memory per worker, so multiple workers would scatter a run's status/diff/
# logs across processes and 404 intermittently. One worker keeps run state
# consistent; back the registry with a shared store to scale workers later.
CMD ["python", "-m", "uvicorn", "gitpilot.api:app", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "1", \
     "--limit-concurrency", "10", \
     "--timeout-keep-alive", "120"]
