# Production deploy — `gitpilot.ruslanmv.com`

This is the runbook for GitPilot's public production deployment and the target
for the Matrix Builder → GitPilot cloud handoff.

## Architecture

```text
gitpilot.ruslanmv.com  (Vercel, Vite/React UI)
        │  cross-origin calls via VITE_BACKEND_URL
        ▼
ruslanmv-gitpilot.hf.space  (Hugging Face Docker Space, FastAPI backend)
        │  GITPILOT_PROVIDER=ollabridge
        ▼
ruslanmv-ollabridge.hf.space  (OllaBridge, OpenAI-compatible /v1 gateway)
```

- **Frontend (Vercel):** the `frontend/` Vite app, built per `vercel.json`.
  The DNS is already pointed: `gitpilot.ruslanmv.com` CNAME →
  `b2aab4fcc3b40c0d.vercel-dns-017.com`, with the `_vercel` verification TXT
  present. Set **`VITE_BACKEND_URL=https://ruslanmv-gitpilot.hf.space`** in the
  Vercel project so the UI calls the HF backend.
- **Backend (HF Space):** the multi-stage Docker image in
  `deploy/huggingface/Dockerfile`, listening on port 7860. It is force-deployed
  by the `.github/workflows/sync-hf-space.yml` GitHub Action **on push to
  `main`** (or manual `workflow_dispatch`). The Space needs the repo secrets
  `HF_TOKEN`, `HF_USERNAME` (`ruslanmv`), `SPACE_NAME` (`gitpilot`).
- **Inference (OllaBridge):** the HF Space sets `GITPILOT_PROVIDER=ollabridge`
  and `OLLABRIDGE_BASE_URL=https://ruslanmv-ollabridge.hf.space`. GitPilot routes
  LLM calls through litellm with an `openai/<model>` prefix pointed at
  `${OLLABRIDGE_BASE_URL}/v1`.

## Backend environment (HF Space)

Set on the Space (already baked into `deploy/huggingface/Dockerfile`):

| Variable | Value |
|---|---|
| `GITPILOT_PROVIDER` | `ollabridge` |
| `OLLABRIDGE_BASE_URL` | `https://ruslanmv-ollabridge.hf.space` |
| `GITPILOT_OLLABRIDGE_MODEL` | `qwen2.5:1.5b` |
| `CORS_ORIGINS` | `*` (allows `gitpilot.ruslanmv.com` → Space cross-origin) |
| `GITPILOT_CONFIG_DIR` | `/tmp/gitpilot` |

For the Matrix cloud handoff (Batch 5+), also set on the Space:

| Variable | Value |
|---|---|
| `GITPILOT_A2A_REQUIRE_AUTH` | `true` |
| `GITPILOT_A2A_SHARED_SECRET` | a long random secret (shared with Matrix Builder) |

## Health & verification

```bash
# Frontend (Vercel)
curl -I https://gitpilot.ruslanmv.com/                       # 200

# Backend (HF Space)
curl https://ruslanmv-gitpilot.hf.space/api/health           # {"status":"healthy",...}
curl https://ruslanmv-gitpilot.hf.space/api/health/deep      # provider:"ollabridge", provider_reachable:true

# Inference (OllaBridge) — the chat/plan path
curl https://ruslanmv-ollabridge.hf.space/v1/chat/completions \
  -H 'content-type: application/json' -H 'authorization: Bearer ollabridge' \
  -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'
```

Last verified (Batch 4): `/api/health` and `/api/health/deep` return **200** with
`provider:"ollabridge"`, `provider_reachable:true`, `crewai_loaded:true`; the
OllaBridge `/v1/chat/completions` path returns a completion;
`gitpilot.ruslanmv.com` serves the UI (200).

## Deploying the Matrix facade to production

The Matrix-native run facade (`/api/v1/gitpilot/runs`, `/api/matrix/runs` and
their `/health` siblings) ships with the backend source, so it reaches the Space
the moment the branch lands on `main`:

1. Merge the feature branch into `main`.
2. The `sync-hf-space.yml` action force-pushes the deploy tree to the Space and
   it rebuilds (~a few minutes).
3. Confirm: `curl https://ruslanmv-gitpilot.hf.space/api/matrix/health` → `200`.

A manual `workflow_dispatch` of the same action deploys without a code change
(e.g. to re-sync the Space).
