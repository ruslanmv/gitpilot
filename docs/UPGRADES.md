# GitPilot Upgrades — Context, Tools, Modes, Sandbox

All changes in this document are **additive and non-destructive**.
Existing GitPilot installations keep working with no configuration; the
new features are opt-in.

---

## 1. Persistent project context — `AGENTS.md`

`AGENTS.md` at the workspace root is loaded into every session as a
high-priority context block.  It is the recommended place for project
conventions, directory map, stack notes, and workflow shortcuts.

### Generate one

```bash
gitpilot init                # writes AGENTS.md if it does not exist
```

The starter document is produced by scanning the workspace (detects
Python, Node, Docker, Makefile targets, top-level layout).  Edit it
freely afterwards.

### Mode-specific overlays

Place per-mode overrides in `.gitpilot/AGENTS.<mode>.md`.  They are
loaded **after** the root file, so the most specific rules apply last.

### Includes

Any `AGENTS.md` may include other markdown files with a single line:

```markdown
@./fragments/db-conventions.md
```

* relative or absolute paths are supported
* circular includes are detected and broken automatically
* total size is capped to protect the context budget

---

## 2. `@`-mentions in chat

The chat input recognises typed references:

| Token | Expands to |
|---|---|
| `@./src/app.py` | the file's contents (size-capped) |
| `@glob:src/**/*.ts` | a list of matching paths |
| `@problems` | the diagnostics dumped to `.gitpilot/problems.json` |
| `@commit:<sha>` | `git show` of that commit |
| `@diff:<range>` | `git diff <range>` |
| `@selection` | the snippet sent from the editor |
| `@pr:<n>` | placeholder resolved by the API layer |

Unknown tokens are reported but otherwise left alone — typing is
forgiving.

---

## 3. Context budget + live token counter

A new module (`gitpilot.context_budget`) tracks token usage per session
and condenses older history when the running total crosses a
configurable threshold.

* Default budget: **200 000 tokens**, condense at **70 %**.
* Strategy: drop oversize tool outputs first, then summarise older
  non-pinned turns into a single recap message, then keep the most
  recent six turns verbatim.
* `ContextStats` exposes `{prompt_tokens, max_tokens, ratio,
  condensations}` for surfacing a live counter in the web UI and
  editor extension.

Token estimation uses `tiktoken` when available and falls back to a
length-based heuristic.

---

## 4. Tool categories + per-mode policy

Every tool now belongs to one of six categories:

```
read  edit  command  browser  mcp  mode
```

A mode may declare which categories it wants and add fine-grained
guards:

```yaml
groups:
  - read
  - mcp:
      allow: ["postgres.*"]
      alwaysAllow: ["postgres.explain"]
      disabledServers: ["github"]
  - edit:
      fileRegex: "^migrations/.*\\.sql$"
```

* `fileRegex` is enforced at edit time — a write outside the pattern
  is rejected before any bytes hit disk.
* `alwaysAllow` lets specific MCP tools run without the per-call
  approval prompt.

Plugins can register their own categories with
`gitpilot.tool_groups.register_category(name, category)`.

---

## 5. Per-MCP-tool toggles + tool-output validator

`.gitpilot/mcp.json` (project) or `~/.gitpilot/mcp.json` (user) accept
per-server toggles:

```json
{
  "servers": [
    {
      "name": "github",
      "enabledTools": ["search_code", "list_issues"],
      "disabledTools": ["create_pr"],
      "alwaysAllow":  ["search_code"],
      "disabled":     false
    }
  ]
}
```

Disabled tools are removed from the model's tool descriptions — every
disabled tool is a small win on the prompt budget.  Project file wins
on conflicts.

Tool outputs pass through `validate_tool_output` before being injected
into history.  Outputs with control characters are flagged; oversize
outputs are truncated.  Both responses are returned as
`ToolOutputCheck`, so the caller can ask the user instead of poisoning
context.

---

## 6. Custom modes

A mode is a YAML record describing a persona, its instructions, the
tool categories it may use, and (optionally) MCP servers that live and
die with the mode.

```yaml
# .gitpilot/modes.yaml
customModes:
  - slug: db-pilot
    name: "DB Pilot"
    description: Natural-language queries against staging Postgres
    roleDefinition: |
      You are a senior DBA.  Always EXPLAIN before mutating.
    whenToUse: |
      Use for schema, queries, or migrations.
    customInstructions: |
      Refuse DROP / TRUNCATE without explicit confirmation.
    groups:
      - read
      - mcp:
          allow: ["postgres.query", "postgres.explain"]
          alwaysAllow: ["postgres.explain"]
      - edit:
          fileRegex: "^migrations/.*\\.sql$"
    mcpServers:
      postgres:
        command: uvx
        args: [mcp-postgres-server]
        env: { PG_URL: "${STAGING_PG_URL}" }
        alwaysAllow: [postgres.explain]
```

Lookup order:

1. `~/.gitpilot/modes.yaml`         — user-global
2. `<workspace>/.gitpilot/modes.yaml` — project (wins on slug clash)

`activate_mode(registry, "db-pilot")` returns an `ActiveModeContext`
bundle ready to plug into the executor:

* `system_prompt_block` — for prompt injection
* `tool_policy` — pass to the executor / approval layer
* `mcp_server_configs` — for the MCP client to spin up
* `extra_mcp_toggles` — apply via `MCPToggleRegistry`

When a mode is exited, its mode-scoped MCP servers stop and their tool
definitions leave the prompt automatically.

---

## 7. Slash commands as markdown

Drop a file into `.gitpilot/commands/<name>.md` (project) or
`~/.gitpilot/commands/<name>.md` (user) to define a reusable command:

```markdown
---
description: Create a new API endpoint
argument-hint: <endpoint-name> <http-method>
---

Create a new endpoint called $1 handling $2 requests.
Include error handling, tests, and OpenAPI docs.
```

* Filename → command name (lower-case, dash-separated).
* `$1`..`$9` are positional; `$ARGS` expands to the full arg string.
* Front-matter `description` powers the `/` menu.

---

## 8. Checkpointing

Before any mutating tool call, `CheckpointStore.snapshot` records:

1. A git commit in a **shadow** repo at
   `~/.gitpilot/history/<workspace-hash>/snapshot`.
2. The conversation transcript up to that point.
3. The exact tool call that was about to run.

`store.restore(checkpoint_id)` rolls the workspace files back and
returns the saved transcript so the chat can resume from the same
state.  The shadow repo never touches the project's `.git/` directory.

```python
from gitpilot.checkpoints import CheckpointStore, ToolCallDescriptor

store = CheckpointStore(workspace)
record = store.snapshot(
    ToolCallDescriptor(name="write_local_file", target_path="src/app.py"),
    transcript=conversation,
)
# …later…
restored = store.restore(record.id)
```

`store.prune(keep_last=50)` removes older checkpoints for housekeeping.

---

## 9. Custom rules

Rule files steer style and process without filling the chat with
boilerplate.  Discovery (global → workspace, last wins):

```
~/.gitpilot/rules/*.md
~/.gitpilot/rules-<mode>/*.md
<ws>/.gitpilotrules
<ws>/.gitpilotrules-<mode>
<ws>/.gitpilot/rules/*.md
<ws>/.gitpilot/rules-<mode>/*.md
```

```python
from gitpilot.rules import compose_rules

markdown, ruleset = compose_rules(workspace_path=ws, mode_slug="coder")
```

The returned block is bounded — over-budget rules are tail-trimmed so
the freshest instructions stay visible.

---

## 10. Sandboxed tool execution

A new `gitpilot.sandbox` module introduces pluggable execution
backends.  By default GitPilot uses the **subprocess** backend (cwd
jailed to the workspace, secret env vars stripped, blocked-pattern
deny list).  For real containerised isolation, point GitPilot at a
[MatrixLab](https://github.com/agent-matrix/matrixlab) runner:

```bash
export GITPILOT_SANDBOX=matrixlab
export GITPILOT_MATRIXLAB_URL=http://localhost:8000   # default
export GITPILOT_MATRIXLAB_TOKEN=<bearer if needed>
```

```python
from gitpilot.sandbox import get_sandbox, SandboxPolicy

sb = get_sandbox(policy=SandboxPolicy(workspace=ws, timeout_sec=120))
result = await sb.run(["pytest", "-q"])
print(result.stdout, result.exit_code, result.sandbox_id)
```

| Backend | Isolation | Setup |
|---|---|---|
| `off` | none (legacy host exec) | always available |
| `subprocess` (default) | cwd jail + env scrub + deny patterns | always available |
| `matrixlab` | ephemeral container, resource caps, no host FS | requires a running MatrixLab runner |

Selection precedence: explicit argument → `GITPILOT_SANDBOX` env →
`settings.json` `tools.sandbox` → `subprocess`.  An unknown backend
falls back to `subprocess` rather than running on the host.

---

## 11. Trusted folders

GitPilot now records a per-workspace trust decision in
`~/.gitpilot/trusted.json`:

```python
from gitpilot.trusted_folders import TrustStore, TrustStatus

store = TrustStore.default()
status = store.status(workspace)
if status is TrustStatus.UNKNOWN:
    # Prompt the user, then:
    store.trust(workspace, note="onboarded 2026-05")
elif status is TrustStatus.FINGERPRINT_MISMATCH:
    # The workspace's structural files changed since we trusted it —
    # ask the user to re-confirm before proceeding.
    ...
```

The fingerprint covers a small set of structural files
(`package.json`, `pyproject.toml`, `Cargo.toml`, `Makefile`,
`AGENTS.md`, `.gitpilot/modes.yaml`, …) so wholesale folder swaps
invalidate trust automatically.

---

## Backwards compatibility

* No existing module was modified — every change ships as a new file
  under `gitpilot/`.
* All 956 pre-existing tests continue to pass; 79 new tests cover the
  new modules (1035 total).
* Default behaviour is unchanged: a session that doesn't load
  `AGENTS.md`, doesn't activate a custom mode, and doesn't ask for a
  sandbox behaves exactly as before.

---

## Quick adoption checklist

1. `gitpilot init` — drop a starter `AGENTS.md` in the repo.
2. Add `.gitpilot/modes.yaml` with the modes your team uses.
3. Tighten `.gitpilot/mcp.json` — turn off tools you don't need; mark
   read-only tools `alwaysAllow`.
4. Drop a few `.gitpilot/commands/*.md` for recurring prompts.
5. Set `GITPILOT_SANDBOX=matrixlab` (and point at a running MatrixLab
   runner) for production-grade isolation of shell tools.
6. Wire the `ContextBudgetManager.stats()` output into the chat UI to
   surface a live token counter.
