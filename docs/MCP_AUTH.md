# MCP Context Forge: reusing one, and authenticating with it

Two decisions GitPilot used to guess at, and now asks about.

## 1. Which Forge — adopt, don't duplicate

HomePilot runs its own MCP Context Forge. So do several sibling stacks. A second
gateway on the same machine is not redundancy: it **splits the tool registry in
two**, so half your servers are invisible from whichever UI you happen to be
looking at, and both instances fight over `:4444`.

`make run` therefore looks for a healthy Forge first:

```
MCP_FORGE_URL if set  →  http://localhost:$MCP_FORGE_PORT  →  http://localhost:4444
```

| Found | What happens |
|---|---|
| A Forge **we** started (`gitpilot-mcp-context-forge`) | normal path, unchanged |
| A Forge **someone else** started | adopted: only our MCP servers start, registered against it |
| Nothing | we start the full stack |

**Ownership is enforced, not promised.** `make stop-mcp` runs `docker compose
down` against *our* compose file, which can only remove services declared in it —
an adopted Forge is out of reach by construction. It says so rather than
implying the gateway went down with the stack.

`make status-mcp` reports which Forge is in use, who started it, and whether
authentication works.

Pin or opt out:

```bash
MCP_FORGE_URL=http://localhost:4444   # always use this one, skip discovery
MCP_FORGE_ADOPT=never                 # always start our own
```

### The reachability detail that makes reuse actually work

An adopted Forge is on someone else's docker network, so `http://mcp-postgre-server:8080`
resolves to nothing from it. Registering a URL the gateway cannot dial *succeeds*
and then fails on every call afterwards — the worst kind of failure, because it
looks fine.

So the advertised address follows ownership:

| Forge | Servers registered as |
|---|---|
| ours (same network) | `http://mcp-postgre-server:8080/mcp` |
| adopted | `http://host.docker.internal:8080/mcp` (the published host port) |

Override with `MCP_ADVERTISE_HOST`. On Docker Desktop, Rancher and WSL2,
`host.docker.internal` works out of the box; on native Linux the adopting Forge's
own compose needs `extra_hosts: ["host.docker.internal:host-gateway"]` — ours has
it, but a Forge started by another project is that project's to configure. If the
adopted gateway can't resolve it, set `MCP_ADVERTISE_HOST` to the host's LAN IP.

## 2. Which credential — three secrets, not one

The 401 storm on every registration came from one variable doing three jobs:

```yaml
JWT_SECRET_KEY:      ${MCP_AUTH_TOKEN}   # signing secret
BASIC_AUTH_PASSWORD: ${MCP_AUTH_TOKEN}   # a password
Authorization: Bearer ${MCP_AUTH_TOKEN}  # ...and sent as a token
```

**A JWT signing secret is not a JWT.** Forge tries to decode it, fails, falls back
to a database API-token lookup, finds nothing, and answers
`Invalid authentication credentials`.

And the part that surprises everyone: **`AUTH_REQUIRED=false` does not rescue
you.** Forge's anonymous path sits inside `if not token:` — it is only reached
when there is *no* `Authorization` header at all.

| Request, with `AUTH_REQUIRED=false` | Result |
|---|---|
| no `Authorization` header | accepted as Platform Admin |
| `Authorization: Bearer <not-a-JWT>` | **401** |

Sending a placeholder credential is strictly worse than sending none.

So the three secrets are now separate, and only one of them is ever transmitted:

| Variable | What it is | Sent to Forge? |
|---|---|---|
| `MCP_FORGE_JWT_SECRET` (falls back to `MCP_AUTH_TOKEN`) | signs Forge's tokens | **never** |
| `MCP_FORGE_ADMIN_EMAIL` / `MCP_FORGE_ADMIN_PASSWORD` | used to *obtain* a token | only to `/auth/email/login` |
| `MCP_FORGE_API_TOKEN` | a token | yes, as `Bearer` |

### How the credential is resolved

Asked, not assumed — `GET /gateways` with no header tells us what this Forge wants:

1. **200** → auth is disabled → send **no header at all**.
2. **401/403** → a credential is needed:
   1. `MCP_FORGE_API_TOKEN`, if Forge accepts it
   2. the cached token in `.mcp/forge-token` (mode `0600`, gitignored), if still accepted
   3. `POST /auth/email/login` with the admin credentials → cached for next time
   4. otherwise: stop, and print which of the three to fix

A token is only used if Forge actually accepts it. "Configured" is not "valid".

### Getting an API token (recommended for anything shared)

```bash
# Forge UI → Tokens, or:
docker compose --env-file .mcp.env -f docker-compose.mcp.yml exec mcp-context-forge \
  python3 -m mcpgateway.utils.create_jwt_token -u admin --secret "$JWT_SECRET_KEY"
```

Put it in `MCP_FORGE_API_TOKEN`. It is revocable and needs no admin password on disk.

## 3. The Admin UI login loop

> *Serving over HTTP with secure cookies enabled…* — and login bounces back to
> the login page.

Forge marks its session cookie `Secure` when `SECURE_COOKIES=true` **or**
`ENVIRONMENT=production`. A browser silently discards a `Secure` cookie sent over
plain HTTP, so the login succeeds server-side and the session evaporates.

Our compose sets both explicitly for local development:

```yaml
ENVIRONMENT: ${MCP_FORGE_ENVIRONMENT:-development}
SECURE_COOKIES: "${MCP_FORGE_SECURE_COOKIES:-false}"
```

If you see that warning from a Forge **you** started elsewhere, the fix is the
same but must be applied where that container gets its environment. Editing a
`.env` inside the checkout is not enough: under Compose the container reads the
values passed to it, not a file on the host. Confirm what actually arrived:

```bash
docker compose exec mcp-context-forge env | grep -Ei 'secure_cookies|environment'
```

## Changing the gateway from the app

Everything above can be configured without touching a file. **Settings → MCP
Servers → Gateway** takes a URL, a sign-in method and a credential, and the
primary button is **Test connection**, not Save — the credential is proved
against the real gateway there, rather than discovered to be wrong during a
coding run.

| Surface | Where |
|---|---|
| Desktop & mobile | Settings → MCP Servers → **Gateway** (one responsive component) |
| VS Code | **GitPilot: Configure MCP Gateway** (⇧⌘P), plus `gitpilot.mcp.*` settings |

All three call the same three endpoints, so a gateway configured in one is
configured everywhere:

```
GET  /api/mcp/gateway        the connection, with secrets reduced to booleans
PUT  /api/mcp/gateway        save (partial; blank ≠ clear)
POST /api/mcp/gateway/test   probe a draft before committing it
```

Three rules the API enforces, so no front end has to remember them:

* **A saved secret is never returned.** Reads carry `hasPassword` /
  `hasApiToken`, never the values — a screen that can display a password is a
  screen that can leak one.
* **Blank means "keep it".** A settings form always posts an empty password
  box; that must not erase the stored password. Removing one is an explicit
  action.
* **Settings beat the environment.** The saved profile wins, then
  `GITPILOT_MCP_FORGE_*`, then the default — so a deployment's env vars are a
  starting point, not a cage. The UI labels which one a value came from.

In VS Code the URL, sign-in method and admin email are ordinary settings —
visible, shareable, reviewable — while the password and API token are typed
into a masked prompt and sent straight to the GitPilot server. Nothing secret is
written to `settings.json`, which syncs across machines and is readable by any
extension.

## When registration returns 503

> `⚠️  mcp-postgre-server: unexpected HTTP 503` — `{"message":"Unable to connect to gateway"}`

Registering is not a database insert: Forge **dials the URL and performs an MCP
`initialize` handshake** before accepting it. A 503 means that handshake failed,
and Forge does not say which of three things went wrong. So each target is
checked before we ask:

1. **Is it up?** We wait for the server on its published host port
   (`MCP_SERVER_WAIT_SECONDS`, default 45). A server that never answers is
   skipped with a pointer to `make logs-mcp`, not registered as broken.
2. **Which path does it speak MCP on?** `/mcp`, `/mcp/`, `/sse` and `/` are
   probed with the same `initialize` call Forge will make; the first that
   answers is registered, with the matching transport (`STREAMABLEHTTP` or
   `SSE`). Assuming `/mcp` is what produced the 503 on an SSE-only server.
3. **Can Forge reach it?** If the handshake still fails, the message says
   whether the problem is the transport or the address — and, for an adopted
   Forge, that `host.docker.internal` may not resolve inside it.

Milvus lives on an opt-in profile, so it is only registered when it is actually
running.

## Production posture

Same code, different values:

```bash
MCP_FORGE_AUTH_REQUIRED=true
MCP_FORGE_ENVIRONMENT=production
MCP_FORGE_SECURE_COOKIES=true      # behind TLS
MCP_FORGE_API_TOKEN=<revocable token>
```

The anonymous path then never triggers — which is why the development path
exercises the *token* flow too, rather than depending on the bypass.
