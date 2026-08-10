# MCP servers in VS Code

GitPilot's agents ship with a fixed set of abilities: read the repo, plan,
write code, run tests, drive GitHub. **MCP servers extend that set.** Attach a
PostgreSQL server and the Explorer can read your live schema, the Coder can
write queries against real tables, and the Reviewer can check a migration
before it lands — for exactly as long as you leave it enabled.

This page covers doing that from VS Code. For the agents themselves, see
[Agent architecture and topologies](../agents.md).

Open it with **`GitPilot: Settings`** → **MCP Servers**.

---

## How this changes what the agents do

An enabled MCP server's tools are injected into the agents' tool list at the
start of a run. The Planner sees the tool descriptions and can plan around
them; the Coder can call them. Nothing is injected from a server that is
attached but disabled.

That is the whole mechanism, and it has one important consequence: **enabling
a server widens what the agents can do without asking again.** So the flow is
deliberately two steps — attaching is not enabling.

Tools are also mapped to the agents that use them, which is why each tool on
a server's page carries a "Used by" line:

| Tool | Used by |
|---|---|
| `postgres.list_tables` | Explorer |
| `postgres.describe_table` | Coder, Reviewer, test runner |
| `postgres.safe_select` | Coder |
| `postgres.explain_query` | Reviewer |
| `postgres.validate_migration` | Reviewer |

---

## The overview page

```
MCP Servers

  ● MCP Context Forge   Connected
    http://localhost:4444
    2 of 3 server(s) enabled · 11 tool(s) available to the agents
                                     [Configure] [Sync]

  ATTACHED SERVERS
    mcp-postgre-server                          Enabled  ›
    9 of 11 tool(s) available to the agents
    mcp-milvus-server                          Disabled  ›

  ADD A SERVER
    [ search a registry…        ] [Search] [Add manually]
    Bundled with GitPilot
    mcp-inspector-server                        [Attach]
```

Clicking a server opens its own page; **‹ Back to MCP Servers** returns.

---

## Installing MCP Context Forge

MCP servers are reached through a gateway — **MCP Context Forge** — and until
now getting one meant a terminal, a compose file and an env file. When no
gateway is reachable, the overview shows a single button:

**[Install MCP Context Forge]**

It checks Docker, starts Forge, waits for it to answer, and points GitPilot at
it. Progress appears as a notification and in the **GitPilot** output channel.
Two paths, chosen by what is on disk:

| Situation | What happens |
|---|---|
| Your workspace is a GitPilot checkout (`docker-compose.mcp.yml` present) | Uses the project's MCP stack. First run builds images from pinned upstreams and can take several minutes. A `.mcp.env` is seeded with a freshly generated signing secret if one does not exist. |
| No checkout — a plain `pip install gitcopilot` | Runs the published Forge image directly. |

The button disappears once a gateway is reachable — offering to install
something already running is noise.

!!! note "Prerequisites"
    Docker must be installed and its daemon running. The installer says which
    of those is missing rather than failing later; there is nothing to
    uninstall if you decline.

| Setting | Default | Purpose |
|---|---|---|
| `gitpilot.mcp.forgePort` | `4444` | Port Forge listens on |
| `gitpilot.mcp.forgeImage` | `ghcr.io/ibm/mcp-context-forge:latest` | Image used when there is no compose file. Point this at a registry your network can reach if the default is unavailable. |

**Configure** opens the gateway wizard (URL, sign-in method, credential) —
use it to point GitPilot at a Forge someone else runs. **Sync** re-reads the
gateway's registry and reconciles it with GitPilot's local list.

---

## Attaching a server

### From the bundled catalogue

GitPilot ships with four, listed under **Bundled with GitPilot**:

| Server | What it gives the agents |
|---|---|
| **mcp-postgre-server** | Schema discovery, safe SELECTs, EXPLAIN plans, migration validation, test-fixture generation |
| **mcp-milvus-server** | Collection discovery, vector and hybrid search, RAG pipeline context, deterministic test vectors |
| **mcp-inspector-server** | Validating, invoking and debugging other MCP servers |
| **gitpilot-mcp-server** | GitPilot's own surface, so external agents can drive it |

Press **Attach**. The server arrives **disabled** — open it and turn it on.

### From a registry

Type what you need into the search box and press **Search**. GitPilot queries
a remote MCP registry — [MatrixHub](https://matrixhub.io) by default — and
lists what it finds under the bundled entries.

```
[ postgres                    ] [Search] [Add manually]

12 result(s) from https://api.matrixhub.io
```

A registry that is unreachable says so and leaves the bundled catalogue
visible; browsing never changes what the agents can do.

| Variable | Purpose |
|---|---|
| `GITPILOT_MATRIXHUB_URL` | Registry to search. Defaults to `https://api.matrixhub.io`. Point it at your own registry to publish an internal catalogue. |
| `GITPILOT_MATRIXHUB_TOKEN` | Bearer token, if your registry requires one |

Any registry serving a JSON catalogue works — the results are normalised, so
`items`/`results`/`servers`/`data` envelopes and `id`/`name`/`slug` naming are
all accepted. Entries that are not MCP servers, or that carry no endpoint,
are dropped rather than offered.

### By hand

**Add manually** asks for three things:

1. **Server id** — how it will be listed
2. **MCP endpoint URL** — e.g. `http://localhost:8080/mcp`
3. **Environment variable holding its token** — the variable's *name*

!!! warning "Never paste a token here"
    GitPilot asks for the **name** of an environment variable, and reads its
    value on the GitPilot host. The token itself never enters VS Code, is
    never stored in `settings.json`, and is never synced by Settings Sync.

---

## A server's page

```
‹ Back to MCP Servers

  mcp-postgre-server                                  Enabled

  Endpoint
  http://mcp-postgre-server:8080/mcp
  Token read from MCP_POSTGRE_SERVER_TOKEN on the GitPilot host.

  Available to the agents                                  [●—]

  TOOLS
  postgres.list_tables          low       Used by explorer      [●—]
  postgres.safe_select          low       Used by coder         [●—]
  postgres.drop_table           high      Not mapped            [—○]

  [Test connection]  [Detach server]
```

**Available to the agents** is the master switch. **Test connection** probes
the server for real and reports what came back. **Detach** removes it after
confirming.

### Tool risk

Every tool is classified from its name, and the classification decides its
default:

| Risk | Examples | Default |
|---|---|---|
| **high** — destroys data | `drop`, `delete`, `truncate`, `remove`, `destroy` | **Off** |
| **medium** — mutates | `insert`, `update`, `upsert`, `create`, `alter`, `execute_write` | On |
| **low** — reads | everything else | On |

Enabling a high-risk tool asks for confirmation first. Disabling one never
does — removing a capability is not a risk.

Some bundled servers go further and deny destructive tools outright in their
manifest, so they are never offered.

---

## A worked example: giving the agents your database

1. **MCP Servers** → **Install MCP Context Forge** (if no gateway is running)
2. **Attach** `mcp-postgre-server` from the bundled catalogue
3. Set `MCP_POSTGRE_SERVER_TOKEN` and the server's connection string on the
   GitPilot host
4. Open the server → **Test connection**
5. Turn on **Available to the agents**
6. Leave `postgres.drop_table` off

Now ask GitPilot something that needs the schema:

> *"Add a `last_login` column to users and write the migration."*

The Explorer reads the live table definitions instead of guessing from the
ORM, the Planner writes a migration against what is actually there, and the
Reviewer runs `postgres.validate_migration` before you approve it.

When the task is done, disable the server. The agents lose those tools again,
and nothing about your database is in the prompt for unrelated work.

---

## Troubleshooting

**The catalogue is empty**
Fixed. The bundled manifests now ship inside the wheel; before that a
`pip install` had nothing to list because the catalogue lived in a directory
only present in a repo checkout. Upgrade if you still see it.

**"Install MCP Context Forge" fails immediately**
The message names the cause — Docker missing, daemon stopped, or the image
unreachable. For the last, set `gitpilot.mcp.forgeImage`.

**A server is attached but the agents do not use its tools**
Check three things, in order: the server is **enabled** (not just attached),
the individual tool is enabled, and the gateway badge says Connected.

**Registry search returns nothing**
The status line distinguishes "0 results" from "registry unavailable". For
the latter, check `GITPILOT_MATRIXHUB_URL`, and `GITPILOT_MATRIXHUB_TOKEN` if
it needs authentication.

**MCP Servers says it needs the GitPilot server**
Attached servers are stored by the backend. Connect from
[AI Providers](ai-providers.md#when-the-gitpilot-server-is-not-running), then
come back.

---

## See also

- [Agent architecture and topologies](../agents.md)
- [AI provider setup](ai-providers.md)
- [MCP gateway authentication](../MCP_AUTH.md)
- [Sandbox and approvals](../SANDBOX.md)
