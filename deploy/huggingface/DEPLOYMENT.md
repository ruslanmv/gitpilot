# Hugging Face Spaces Deployment Guide

This document covers how GitPilot is deployed to Hugging Face Spaces as a
Docker container, the pitfalls we encountered, and the best practices that
keep the deployment healthy.

Reference: <https://huggingface.co/docs/hub/spaces-sdks-docker>

---

## File Layout

```
deploy/huggingface/
  Dockerfile       # Multi-stage build (Node frontend + Python backend)
  README.md        # HF Space metadata (YAML frontmatter) + user-facing docs
  DEPLOYMENT.md    # This file
```

The GitHub Actions workflow (`.github/workflows/sync-hf-space.yml`) assembles a
clean deploy tree from these files plus source code and pushes it to the HF
Space repository.

---

## HF Docker Spaces Rules (must follow)

### 1. Port 7860

HF probes `app_port` (default **7860**) to decide if the container is healthy.
Set it in the README frontmatter and make sure your app listens on it:

```yaml
# README.md frontmatter
sdk: docker
app_port: 7860
```

### 2. UID 1000 and `--chown=user`

HF always runs containers as **UID 1000**. Follow the official pattern:

```dockerfile
RUN useradd -m -u 1000 user
USER user                                   # switch EARLY, before pip
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

COPY --chown=user pyproject.toml ./         # always --chown=user
RUN pip install --no-cache-dir ...          # installs to ~/.local/
COPY --chown=user src ./src                 # always --chown=user
```

**Why this matters:** If you run `pip install` as root and switch to `USER user`
late, Python may not find packages, or the container may hang silently at
startup because file permissions are wrong.

### 3. No Docker HEALTHCHECK

HF Spaces has its own HTTP probe on `app_port`. The Docker `HEALTHCHECK`
directive is **ignored** by HF's orchestration but **still executed** by the
container runtime. If it marks the container as unhealthy, HF may restart and
eventually rebuild the container in an endless loop:

```
BUILD -> START -> Docker HEALTHCHECK fails -> HF restarts -> BUILD -> ...
```

**Never add `HEALTHCHECK` to an HF Spaces Dockerfile.**

### 4. Direct CMD (no shell scripts)

Using a shell script as CMD adds failure points (wrong line endings, missing
bash, permission issues). Prefer a direct exec-form CMD:

```dockerfile
# Good
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]

# Avoid
CMD ["/app/start.sh"]
```

### 5. `startup_duration_timeout`

Set this in the README frontmatter to control how long HF waits before
flagging the Space as unhealthy. Default is 30 minutes; for GitPilot 5 minutes
is enough:

```yaml
startup_duration_timeout: 5m
```

---

## Docker Layer Caching (build speed)

HF rebuilds the Docker image on every push. Layer order matters:

```dockerfile
# 1. Copy ONLY dependency metadata first
COPY --chown=user pyproject.toml README.md ./

# 2. Install dependencies (cached when only code changes)
RUN pip install --no-cache-dir ...

# 3. Copy source code LAST (busts cache only for layers below)
COPY --chown=user gitpilot ./gitpilot
COPY --chown=user --from=frontend-builder /build/dist/ ./gitpilot/web/

# 4. Lightweight editable install (2s, always runs)
RUN pip install --no-cache-dir --no-deps -e .
```

**If source code is copied before `pip install`, every code change forces a
full dependency reinstall (~50 seconds).**

### Avoid version conflicts between layers

If Step 1 installs `pydantic>=2.7.0` (resolves to 2.12) and Step 2 installs
`crewai` (which requires `pydantic<2.12`), pip will uninstall and reinstall.
Add upper bounds in Step 1 to match downstream constraints:

```dockerfile
RUN pip install "pydantic>=2.7.0,<2.12.0"  # aligned with crewai
```

---

## Secrets and Variables

Secrets are configured in the HF Space Settings tab, not in the Dockerfile.

| Secret | Purpose | Required |
|--------|---------|----------|
| `GITHUB_TOKEN` | GitHub API access | For repo features |
| `OPENAI_API_KEY` | OpenAI provider | If using OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic provider | If using Claude |

At **build time**, secrets are accessed via `--mount=type=secret`:

```dockerfile
RUN --mount=type=secret,id=MY_SECRET cat /run/secrets/MY_SECRET
```

At **runtime**, they are available as environment variables:
`os.environ["MY_SECRET"]`.

---

## CI/CD Sync Workflow

`.github/workflows/sync-hf-space.yml` syncs on every push to `main`:

1. Checks out the GitHub repo
2. Builds a clean deploy directory with only HF-needed files
3. Force-pushes to the HF Space repository

**Required GitHub Secrets:**

| Secret | Value |
|--------|-------|
| `HF_TOKEN` | HuggingFace write token |
| `HF_USERNAME` | HuggingFace username (e.g. `ruslanmv`) |
| `SPACE_NAME` | Space name (e.g. `gitpilot`) |

**Important:** The workflow force-pushes an orphan commit. This means the HF
Space repo has no history — only the latest deploy. This is intentional to keep
the image small and avoid accumulating old layers.

---

## Debugging

### Check Space status

```bash
curl -s -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/api/spaces/$USER/$SPACE" | python3 -m json.tool
```

Key fields: `runtime.stage` (`BUILDING`, `APP_STARTING`, `RUNNING`,
`NO_APP_FILE`), `runtime.sha`, `sha`.

### Build logs (SSE stream)

```bash
curl -N -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/api/spaces/$USER/$SPACE/logs/build"
```

### Runtime logs (SSE stream)

```bash
curl -N -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/api/spaces/$USER/$SPACE/logs/run"
```

### Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `NO_APP_FILE` | Dockerfile missing from repo root | Ensure workflow copies `Dockerfile` to root |
| Stuck at `BUILDING` forever | New commits keep arriving during build | Wait for build to finish before pushing again |
| `APP_STARTING` never reaches `RUNNING` | HEALTHCHECK killing container, or app not on port 7860 | Remove HEALTHCHECK; verify `app_port` matches |
| `APP_STARTING` -> `BUILDING` loop | Docker HEALTHCHECK + HF probe conflict | Remove HEALTHCHECK from Dockerfile |
| Build slow on code-only changes | Source COPY before pip install | Reorder: pip install first, then COPY source |

---

## Data Persistence

Data written to disk is **lost on restart** unless you enable
[Persistent Storage](https://huggingface.co/docs/hub/spaces-storage) (uses the
`/data` directory). The `/data` volume is only available at runtime, not during
build.

---

## Hardware

| Tier | CPU | RAM | Cost |
|------|-----|-----|------|
| cpu-basic | 2 vCPU | 16 GB | Free |
| cpu-upgrade | 8 vCPU | 32 GB | $0.03/hr |

GitPilot runs on `cpu-basic` (free tier).
