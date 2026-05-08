# Production runbook — GitPilot + MCP Context Forge stack

This document is the operator's guide to running GitPilot with the
optional MCP Context Forge stack in production. It complements
[INSTALL_MCP.md](./INSTALL_MCP.md) (which targets developers).

The stack is **strictly additive**: enabling it never changes how
GitPilot core behaves. Disabling it (`GITPILOT_MCP_ENABLED=false`) is a
single env-var flip that returns the system to its baseline shape.

---

## TL;DR

```bash
make install         # uv + npm + MCP image cache (skip-safe without Docker)
make run-all         # GitPilot core + Forge + 3 reference MCP servers
make smoke-mcp       # post-deploy health sweep
make sync-mcp        # mirror Forge's registry into GitPilot's local store
```

---

## Topology

```
                       ┌─────────────────────────────┐
                       │  GitPilot core (port 8000)  │
                       │  - mcp_forge_sync           │
                       │  - mcp_tools_bridge         │
                       │  - admin REST /api/mcp/*    │
                       └──────────────┬──────────────┘
                                      │ HTTP
                       ┌──────────────▼──────────────┐
                       │  MCP Context Forge (4444)   │
                       │  - registry of MCP servers  │
                       │  - tool dispatcher          │
                       └─┬────────┬───────────────┬──┘
                         │        │               │
              ┌──────────▼─┐  ┌───▼──────┐  ┌─────▼──────┐
              │ postgre    │  │ inspector│  │  milvus    │
              │  :8080     │  │  :8081   │  │   :8082    │
              └────────────┘  └──────────┘  └────────────┘
```

All four MCP services live under the Compose **`mcp` profile** in
`docker-compose.mcp.yml`; default `docker compose up` ignores them.

---

## Pre-flight

| Requirement | Why |
|-------------|-----|
| Docker Engine 24+ with Compose v2 | `--profile`, healthcheck-gated `depends_on`, multi-stage builds |
| `git` | `install-mcp.sh` clones the four upstream repos |
| Outbound HTTPS to `github.com` | first-time clone of the four upstreams |
| Outbound to Docker Hub *or* a private registry | only if you switch to image pulls (see "Pinning to releases") |
| 4 GB free disk | the four images + Postgres data volume |
| 2 GB free RAM (4 GB with Milvus profile) | runtime |
| Two free TCP ports per service (4444, 8080, 8081, 8082, 5432 internal) | overridable via `.mcp.env` |

---

## Deployment

### 1. Bring up the stack

```bash
git pull
make install        # idempotent; safe on already-running hosts
make run-all
```

The first `make run-all` builds four images (3-8 minutes on a warm
broadband link). Subsequent runs reuse the build cache.

### 2. Verify

```bash
make smoke-mcp       # (./scripts/smoke-mcp.sh; exits 1 on any failure)
```

What it checks:

* GitPilot `/api/ping` is reachable.
* Each MCP service `/health` returns 200.
* `/api/mcp/status` reports `gateway_reachable=true`.
* `POST /api/mcp/sync` returns a structured `SyncReport` (no
  `forge_unreachable=true`).
* `/api/mcp/agent_tools` returns a well-formed JSON envelope.

Add `--milvus` to also smoke the milvus sub-profile:
`bash scripts/smoke-mcp.sh --milvus`.

### 3. Sync the registry into GitPilot

```bash
make sync-mcp     # POST /api/mcp/sync; prints SyncReport
```

You can also click **Sync** in the **Settings → MCP Servers** tab; both
paths invoke the same endpoint.

### 4. Wire the GitPilot Coder/Reviewer to the synced tools

In **Settings → MCP Servers**, for each server:

1. Toggle **Enable** (servers land disabled by default — opt-in).
2. Expand the tool list, tick the safe (`low`) tools you want.
3. Mutation tools (`high` risk) stay locked unless you also flip
   `GITPILOT_MCP_REQUIRE_APPROVAL_FOR_MUTATIONS=false` and mint a
   separate mutation token.

The Coder agent now sees those tools in its toolbox the same way
Claude Code sees its built-ins.

---

## Day-2 operations

### Update the stack

```bash
git pull
make install-mcp     # re-clones / fetches upstream repos to the pinned ref
make run-mcp         # rebuilds + recreates only what changed
make smoke-mcp
```

### Rotate the MCP auth token

```bash
make stop-mcp
sed -i "s|MCP_AUTH_TOKEN=.*|MCP_AUTH_TOKEN=$(openssl rand -hex 32)|" .mcp.env
make run-mcp
make smoke-mcp
```

### Inspect logs

```bash
make logs-mcp                      # tail -f all four services
docker compose -f docker-compose.mcp.yml logs mcp-postgre-server
```

### Stop the stack (volumes preserved)

```bash
make stop-mcp
```

### Tear it down completely

```bash
make uninstall-mcp     # prompts y/N; removes containers, volumes, images
```

---

## Pinning to release tags (post-publish)

Once Docker Hub publish workflows have run (see
[`extensions/mcp_workflows/README.md`](./extensions/mcp_workflows/README.md))
and tags exist for each image, you have two ways to pin to a known good
release:

**Option A — keep build-from-source, pin the git ref**

In `.mcp.env`:

```bash
MCP_FORGE_REF=v1.2.3
MCP_POSTGRE_REF=v1.0.0
MCP_MILVUS_REF=v0.3.1
MCP_INSPECTOR_REF=v0.1.0
```

Then `make install-mcp && make run-mcp` rebuilds against those pinned
checkouts.

**Option B — pull pre-built images** (faster, no build step)

Edit `docker-compose.mcp.yml` and replace each service's
`build: { context: ./mcp-stack/<repo> }` with
`image: ruslanmv/<image>:vX.Y.Z`. The generated workflows publish
multi-arch (`linux/amd64`, `linux/arm64`) so this works on Apple
Silicon too.

---

## Failure modes & recovery

| Symptom | Diagnosis | Recovery |
|---------|-----------|----------|
| `make smoke-mcp` reports `gateway_reachable=false` | Forge container not running or not reachable from GitPilot | `docker compose -f docker-compose.mcp.yml ps`; `make logs-mcp` |
| `forge_unreachable=true` from `/api/mcp/sync` | GitPilot can reach `/api/ping` but not Forge | Check `GITPILOT_MCP_GATEWAY_URL` and the Forge container's port mapping |
| Workflow rejected with `workflow scope` error | PAT lacks `workflow` scope when pushing CI files | Mint a PAT with `repo + workflow` and re-run `GH_PAT_WORKFLOW=… make install-mcp-workflows` |
| `manifest unknown` during `compose pull` | A pinned image tag doesn't exist on the registry yet | Either build-from-source (default) or wait for the publish workflow run |
| `port already in use` on 4444 / 8080 / etc. | Conflict with another local service | Override `MCP_FORGE_PORT` etc. in `.mcp.env` |
| CRLF errors on WSL | Windows Git checked the scripts out as CRLF | `make fix-line-endings && make install` |
| `npm run build` fails inside an inspector image rebuild | An upstream changed the strict tsconfig flags | Fixed in `200238b`+; pull, then `make run-mcp` |

---

## Observability

| Source | Where |
|--------|-------|
| GitPilot structured logs | `gitpilot/api.py` writes to stderr; `journald` / Docker default |
| MCP sync correlation IDs | every `/api/mcp/sync` response carries one; same id appears in the GitPilot log line `mcp forge sync completed` |
| Per-tool call tracing | `gitpilot/mcp_tools_bridge.py` emits one log entry per remote call with `X-Gitpilot-Origin: agent` |
| Forge access logs | `make logs-mcp` (the four containers' stdout) |
| MCP Servers tab | gateway health pill, per-server status dot, tool count |

---

## Security checklist

* `.mcp.env` is git-ignored. Never commit; tokens are minted at install
  time and rotated by re-running `make install-mcp`.
* Mutation tools (`high` risk) are locked off by default and require
  both `GITPILOT_MCP_REQUIRE_APPROVAL_FOR_MUTATIONS=false` *and* a
  separate mutation token.
* `X-Gitpilot-Origin: self` requests are rejected by the GitPilot MCP
  server — protects against agent self-call loops.
* Recursive sync is disabled: GitPilot pulls from Forge, never pushes.
* The Postgres container in the MCP stack is read-only by default
  (`POSTGRES_READ_ONLY=true`, `POSTGRES_BLOCK_DDL=true`,
  `POSTGRES_BLOCK_DML=true`).
* Forge bearer token is required on every authenticated call; missing
  token returns `401`, wrong scope returns `403`, recursion returns
  `409`.

---

## CI publish (Docker Hub)

Each MCP server repo carries `.github/workflows/docker-publish.yml`
which:

* triggers on a published GitHub Release or manual `workflow_dispatch`;
* builds multi-arch (`linux/amd64`, `linux/arm64`) via QEMU + Buildx;
* tags `<image>:<semver>`, `<image>:latest`, `<image>:sha-<short>`,
  `<image>:{{major}}.{{minor}}`;
* applies OCI labels (title, description, source, version, revision,
  created, license);
* uses GHA cache scoped per service for fast rebuilds;
* runs an import smoke for Python services / a real `/health` round-trip
  for the inspector;
* prints a summary with the published tags and pull commands.

Required org/repo secrets:

| Secret | Scope |
|--------|-------|
| `DOCKERHUB_USERNAME` | account that owns the images |
| `DOCKERHUB_TOKEN` | Docker Hub access token, write scope |

---

## Disabling the MCP stack

A single env-var flip returns the system to baseline:

```bash
echo "GITPILOT_MCP_ENABLED=false" >> .env
make stop-mcp
# 'make run' still works; the MCP Servers tab shows "plugin disabled"
```

The codebase paths are guarded so no MCP traffic ever leaves the
process when the flag is off.
