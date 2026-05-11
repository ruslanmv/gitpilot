# Installing the MCP Context Forge stack

The GitPilot **MCP Context Forge** is a bundled sidecar environment
that runs alongside GitPilot core. When it is up, GitPilot's agents can
call out to external MCP servers (PostgreSQL schema discovery, Milvus
vector search, MCP inspector, plus anything else you attach) **as
first-class tools during code generation**, the way Claude Code uses
its built-in toolbox.

The stack is **installed by default and runtime-additive**:

* `make install` prepares GitPilot core, the frontend, and the MCP stack.
  On machines without Docker, MCP preparation prints a friendly skip
  message and exits 0 so the baseline app install still succeeds.
* Re-running `make install` is incremental: existing MCP checkouts skip
  network fetches unless `MCP_UPDATE=1` is set, and existing Docker images
  skip rebuilds unless `MCP_BUILD=1` is set.
* `make run` starts the MCP stack first, verifies the Forge health endpoint
  is host-reachable, then starts GitPilot backend/frontend. Use `make run-all`
  only when you also want to force-restart an already-running backend.
* No Docker?  Use `make run-bare` to start GitPilot without the MCP stack;
  the UI will show the gateway as Unreachable, but everything else works.

---

## TL;DR

```bash
make install        # backend + frontend + (if Docker) MCP image cache
make run            # MCP Context Forge + GitPilot backend/frontend
# In a browser: Settings → MCP Servers → click "Sync"
```

That's it. The "Sync" button copies every server registered with the
running Forge into GitPilot's local registry. Toggle each server on,
toggle individual tools on, and they immediately become available to
GitPilot's agents.

---

## What gets installed

| Service | Built from | Default port | Purpose |
|---------|-----------|--------------|---------|
| `mcp-context-forge` | `mcp-stack/mcp-context-forge/Containerfile` | 4444 | Gateway + registry |
| `mcp-postgre-server` | `mcp-stack/mcp-postgre-server/Dockerfile` | 8080 | Schema discovery, safe SELECT |
| `mcp-postgre-db` | `postgres:16-alpine` (Docker Hub) | (internal) | Backing DB for postgre-server |
| `mcp-inspector-server` | `mcp-stack/mcp-inspector-server/Dockerfile` | 8081 | Validate / contract-test other servers |
| `mcp-milvus-server` *(opt-in sub-profile)* | `mcp-stack/milvus-admin-ui/mcp-server/Dockerfile` | 8082 | Vector search & RAG codegen |

All five live under the Compose **`mcp` profile**, so `docker compose up`
without `--profile mcp` ignores them entirely.

### Build-from-source (mirrors HomePilot)

The four MCP repos have **no published Docker Hub images**, so we follow
the same approach HomePilot uses for its MCP servers stack: clone each
upstream repo into `./mcp-stack/` and let Compose build the image from
its Dockerfile. Branches / refs are pinned via `.mcp.env`
(`MCP_FORGE_REF`, `MCP_POSTGRE_REF`, `MCP_MILVUS_REF`,
`MCP_INSPECTOR_REF`). Re-run with `MCP_UPDATE=1` when you want to fetch
those pinned refs again.

`./mcp-stack/` is git-ignored — it's a build-time scratch dir, not part
of the repo.

---

## File anatomy (additive)

```
docker-compose.mcp.yml      NEW   profile-gated stack
.env.template.mcp           NEW   copied to .mcp.env on first install
scripts/install-mcp.sh      NEW   Docker-aware install (idempotent, skip-safe)
scripts/sync-mcp.sh         NEW   curl wrapper for /api/mcp/sync
scripts/uninstall-mcp.sh    NEW   prompts, then compose down -v
Makefile                    +6 new targets (no edits to existing ones except
                                 install: depending on the new install-mcp,
                                 which is itself a no-op without Docker)
.gitignore                  +.mcp.env (added on first install)
```

No existing service, route, test or build target is modified.

---

## Make targets

| Target | What it does | Needs Docker? |
|--------|--------------|---------------|
| `make install` | uv + npm + `install-mcp` (skip-safe) | no |
| `make install-mcp` | Seed `.mcp.env`, clone missing MCP repos, build missing images | yes (else no-op) |
| `make run` | Start MCP stack, verify Forge, then start GitPilot core/frontend | yes for MCP |
| `make run-bare` | Start GitPilot core/frontend WITHOUT the MCP stack | no |
| `make run-mcp` | Start Forge + 3 reference servers | yes |
| `make run-all` | Stop stale backend, then `run` | yes |
| `make stop-mcp` | Stop the MCP stack (volumes preserved) | yes |
| `make logs-mcp` | Tail logs from the MCP stack | yes |
| `make sync-mcp` | Trigger `/api/mcp/sync` against running GitPilot | no (curl) |
| `make uninstall-mcp` | Prompt y/N, then `compose down -v` and remove images | yes |

---

## How GitPilot uses the sync'd tools

This is the bit that makes it feel like Claude Code:

1. `make run` brings up Forge with three pre-registered servers, verifies
   `http://localhost:4444/health`, and starts GitPilot.
2. Its **MCP Servers** tab now shows the gateway as **Connected** instead
   of *Unreachable*.
3. Click **Sync**. GitPilot calls Forge's registry, mirrors every
   server into its local store, and shows a banner:
   `+3 added · 0 refreshed · 0 orphaned`.
4. Toggle a server on. Toggle the safe tools on (the destructive ones
   are locked off; see policies docs).
5. Open a chat in GitPilot. When the planner classifies a request as
   needing a database schema, the **Coder** agent calls
   `postgres.describe_table` *automatically*; when it needs vector
   search, it calls `milvus.search`. The agents pick from the same
   toolbox you ticked, exactly as Claude Code uses its built-ins.

The wiring is in `gitpilot/mcp_tools_bridge.py` (next batch). The bridge
exposes every enabled tool as an `agent_tools.AgentTool` so the existing
agent runtime sees them with no extra plumbing.

---

## Two-network-context endpoint model

The MCP servers live in two network contexts at once:

| Context | Reaches the servers via | Used by |
|---------|------------------------|---------|
| **Docker network** `gitpilot-mcp` | `http://mcp-postgre-server:8080/mcp` (compose service name) | Forge federation, the registration sidecar, agent calls routed *through* Forge |
| **Host** (WSL / macOS / Linux) | `http://localhost:8080/mcp` (compose-published port) | The GitPilot backend's per-server **Test** button, your browser, any host-side curl |

Both addresses are derived from a single source of truth — `MCP_*_PORT`
in `.mcp.env`, which is also what `docker-compose.mcp.yml` interpolates.
GitPilot stores the in-network URL (because that's what Forge needs) and
translates to the host form on demand via `gitpilot.mcp_admin_api.to_host_url`.
This mirrors k8s `ClusterIP` vs `NodePort`, Consul's `service.address`
vs `service.taggedAddresses`, and Linkerd's in-cluster-vs-ingress URL
distinction. A custom server you add (real DNS / IP) is left untouched.

---

## Industry best-practice checklist

| Practice | Where applied |
|----------|---------------|
| Profile-based opt-in (Compose v2) | `profiles: ["mcp"]` on every service |
| Required env vars fail fast (`${VAR:?}`) | `MCP_AUTH_TOKEN` in compose |
| Pinned image tags, never `:latest` in production | `MCP_FORGE_TAG` etc. in `.env.template.mcp` |
| Healthcheck-gated `depends_on` | postgre/milvus/inspector wait for Forge |
| GitOps pull model | GitPilot pulls registry from Forge; never pushes |
| Idempotent install | `install-mcp.sh` is safe to re-run |
| Reversible | `uninstall-mcp.sh` cleans containers + volumes + images |
| Token never committed | `.mcp.env` auto-added to `.gitignore`; tokens generated locally |
| Skip-safe on minimal hosts | `install-mcp.sh` exits 0 when Docker is absent |
| One-command happy path | `make install && make run` |

---

## Troubleshooting

**"port 4444 already in use"**
`MCP_FORGE_PORT=4445 make run-mcp` (or edit `.mcp.env`).

**"MCP_AUTH_TOKEN: Run 'make install-mcp' first..."**
Compose was started without `--env-file .mcp.env`. The Make targets
already pass it; if you're calling compose by hand, do the same.

**"Sync button greyed out"**
GitPilot can't reach Forge. Check `make logs-mcp` and the gateway dot
in the UI header.

**"My mutation tool is locked"**
By design. Set `GITPILOT_MCP_REQUIRE_APPROVAL_FOR_MUTATIONS=false`
*and* mint a separate mutation token for the server in question.

**"How do I add a fourth MCP server?"**
Either (a) attach it to Forge directly and click **Sync** in the UI, or
(b) drop a `register.json` into `extensions/mcp_plugins/<name>/` and
restart GitPilot. Either path is non-destructive.

**"`scripts/install-mcp.sh: line 18: $'\r': command not found`" on WSL / Windows**

Your Git checkout converted `LF` → `CRLF` (Windows default). The repo
ships a `.gitattributes` that pins `*.sh`, `Makefile`, `*.yml` to LF —
but only files checked out *after* it landed are normalised. Recovery:

```bash
make fix-line-endings    # strips CRLF from scripts/*.sh + Makefile + compose
make install             # works again
```

To prevent it for future clones on Windows:

```bash
git config --global core.autocrlf input
```
