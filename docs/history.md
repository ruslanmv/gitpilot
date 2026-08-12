# Shipped history

What each phase of work put in the repository, in one page.

This replaces the nine per-phase planning documents that used to live in `docs/`.
Those files were working plans — status tables, batch checklists, notes to whoever
picked the work up next — and once the work shipped they described the past in the
present tense, which is the least useful thing a document can do. What was worth
keeping is here; the reasoning behind each decision is in the commit that made it,
and the batch discipline they established is stated once in
[`upgrade-v4-batches.md`](upgrade-v4-batches.md).

The rails those phases put down are what Phase v4 was able to build on: a feature
flag service, a coverage gate, a strict-typing foothold, and a public API with a
deprecation policy. None of them changed behaviour on their own. All of them are why
a change like "replace every topology with a policy document" could land in five
reviewable batches instead of one merge nobody could read.

---

## Phase 1 — Foundations

No user-visible change. Rails only.

| Shipped | Where |
|---|---|
| Feature-flag service (env, user file, project file, runtime override) | `gitpilot/flags.py` |
| Coverage gate ≥ 80 % over an explicit module allowlist | `pyproject.toml`, `.github/workflows/coverage.yml` |
| `mypy --strict` foothold, grown by every later batch | `mypy.ini` |
| Error envelope decorator | flag `error_envelope` |
| `gitpilot doctor` — nine checks, offline, under 100 ms | `gitpilot/doctor.py` |

The flag service is the single most reused thing in the repository: every batch from
Phase 2 onward ships behind one, which is what made "flag off = byte-identical
behaviour" a rule that could actually be enforced by a test.

## Phase 2 — Performance

Five batches on perceived speed and per-turn cost. All flag-gated, all shipped off.

| Shipped | Flag |
|---|---|
| Anthropic prompt-cache builder (`cache_control: ephemeral` markers) | `prompt_cache` |
| Lazy MCP tool definitions — drops tools the mode policy forbids | `lazy_tool_defs` |
| Context-pack memoisation, LRU keyed on workspace, mode, query and mtimes | `context_cache` |
| Conversation budget manager and condensation | — |
| SSE framing and stream metrics | — |

Two of these were still dark when Phase v4 began, and finding that out is a large
part of what v4 turned out to be: `prompt_cache.build_system_blocks` had been
composing a complete cacheable system payload for months with no caller that talked
to a model (wired in Batch V4-H2), and `tool_def_pruner` had exactly one caller —
the MCP bridge that Batch V4-H1 retired.

## Phase 3 — Configuration and safety primitives

| Shipped | Where |
|---|---|
| Custom modes: YAML personas with bound tool policies and MCP servers | `gitpilot/modes.py` |
| Tool groups and `ToolPolicy` | `gitpilot/tool_groups.py` |
| Slash commands, skills, plugin manager | `gitpilot/slash_commands.py`, `skills.py`, `plugins.py` |
| Checkpoint store and rewind | `gitpilot/checkpoints.py` |
| Sandbox backends (subprocess, matrixlab) with secret stripping | `gitpilot/sandbox.py` |
| Trusted folders with content fingerprinting | `gitpilot/trusted_folders.py` |
| Permission manager and modes (`plan`, `normal`, `auto`) | `gitpilot/permissions.py` |
| First-run wizard | `gitpilot/init_wizard.py` |

This is the phase that produced the most *unwired* code, and Phase v4's Phase D is
mostly the story of giving it callers: the permission manager had been constructed
once at module scope and never asked a question, `ToolPolicy` had no caller outside
its own tests, and `modes.activate_mode` had none at all until Batch V4-H2.

## Phase 4 — Quality safety net

| Shipped | Where |
|---|---|
| Public API stability layer with a deprecation cycle | `gitpilot/public_api/`, `gitpilot/_deprecation.py`, [`API_STABILITY.md`](API_STABILITY.md) |
| README rewrite, docs site, in-repo link checker | `mkdocs.yml`, `make docs-serve` |
| Supply chain: CycloneDX SBOM, Sigstore-signed releases, npm audit baseline | `make sbom`, `make audit-npm` |

## Phase v4 — The agentic execution engine

The current programme, and the only one with its plan still in `docs/`, because it is
not finished:

- [`upgrade-plan-v4-agentic-runtime.md`](upgrade-plan-v4-agentic-runtime.md) — the
  design: why one loop with three dialects, what the policy pipeline owes the user,
  what a topology becomes.
- [`upgrade-v4-batches.md`](upgrade-v4-batches.md) — the execution plan and the
  live status table.

Shipped so far: the loop itself, the policy pipeline every tool call goes through,
the Claude-Code console UX, crash-resumable runs, and every topology re-expressed as
a declarative policy document over one engine. The default topology now *declares*
the agentic engine; the flag that executes it stays off until the benchmark gate in
[`upgrade-v4-batches.md`](upgrade-v4-batches.md) has been demonstrated on all three
model tiers.
