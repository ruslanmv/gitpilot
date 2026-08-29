# Agent architecture and topologies

GitPilot is not one model behind a chat box. A **request router** classifies
each request and dispatches it to the specialists that fit, and a **topology**
decides whether that routing happens at all or a fixed pipeline runs instead.

This page is the reference for both. Everything in it is asserted by
`tests/test_agent_topologies.py`, so it stays true as the code changes.

---

## The agent roster

Ten specialists, defined by `AgentType` in `gitpilot/agent_router.py` and
built in `gitpilot/agentic.py`.

| Agent | Id | Tools | What it does |
|---|---|---|---|
| Repository Explorer | `explorer` | Repository (read) | Maps project structure, finds the relevant files, identifies patterns, dependencies and test conventions |
| Repository Refactor Planner | `planner` | Repository (read) | Turns exploration into a step-by-step plan: files to change, order, test strategy, trade-offs |
| Expert Code Writer | `code_writer` | Repository + write | Executes the plan step by step, running tests between steps and fixing failures before moving on |
| Code Review & Analysis Specialist | `code_reviewer` | Repository (read) | Audits changes for security, quality, coverage and performance; groups findings as Critical / Warning / Suggestion |
| GitHub Issue Management Specialist | `issue_manager` | GitHub API | Creates, updates, triages and closes issues |
| Pull Request Management Specialist | `pr_manager` | GitHub API | Branches, commits, pushes, and opens PRs with a summary and test plan |
| Search & Discovery Specialist | `search` | GitHub API | Searches code, repositories, issues and users |
| GitHub Learning & Guidance Specialist | `learning` | — | Explains GitHub features and best practices |
| Local File Editor | `local_editor` | Local file I/O | Reads and writes files directly in your workspace |
| Terminal & Shell Executor | `terminal` | Sandboxed shell | Runs commands inside the [sandbox](SANDBOX.md) |

A **Request Router** sits in front of these. It is not an agent — it holds no
tools and writes nothing — it classifies intent and chooses the destination.

### Why the count matters

Earlier documentation described GitPilot as "four specialized agents
(Explorer, Planner, Coder, Reviewer)". That was two claims in one, and both
were imprecise:

- The system has **ten** agents, not four.
- The **default** plan-and-execute flow runs **three** of them —
  Explorer → Planner → Coder. There is no Reviewer stage in it.

The four-stage description was really describing a *topology*, and the
closest one — Feature Builder — has five stages, not four. Where a Reviewer
runs is covered below.

---

## When does the Reviewer run?

Two ways, and neither is the default plan-and-execute path:

1. **The router sends a request to it.** Ask GitPilot to review code and the
   router dispatches straight to the Code Reviewer — no exploration or
   planning phase.
2. **A topology includes it in its sequence.** Feature Builder, Bug Hunter
   and Code Inspector all do.

If you want every change reviewed, pin a topology that includes a reviewer.
That is what topologies are for.

---

## Topologies

A topology is a named execution shape, and since the v4 engine landed it is a
declarative **policy document**: which engine runs, which dialect, which capabilities
are granted, who approves what, whether the run must verify itself, and where its
limits are. Nine ship with GitPilot, in two categories, and you can add your own.

### Pipelines — fixed agent sequences

Every request runs the same agents in the same order. Predictable, and the
right choice when you want a guaranteed review or PR step.

| Topology | Sequence | Use it when |
|---|---|---|
| **Feature Builder** 🚀 | Explorer → Planner → Coder → Reviewer → PR Manager | Building a feature end to end, ending in a pull request |
| **Bug Hunter** 🐛 | Explorer → Coder → Reviewer → PR Manager | Fixing a defect — skips planning, keeps the review |
| **Code Inspector** 🔍 | Explorer → Reviewer | Auditing code without changing it. Read-only |
| **Architect Mode** 📐 | Explorer → Planner | Designing an approach before committing to it. Read-only |
| **Quick Fix** ⚡ | Coder → PR Manager | A change you have already scoped. Fastest path, no review |

`developer` and `git_agent` are the sequence ids for the Coder and PR Manager
respectively — the ids the API returns in `agents_used`.

Pipelines that include a write-capable agent (`developer`, `git_agent`)
create a working branch named `gitpilot-<topology>-<slug>-<timestamp>` before
touching anything, so your default branch is never modified in place.

### System topologies

| Topology | Id | Use it when |
|---|---|---|
| **Autonomous Engineer** 🧠 | `default` | General use. One agent in a loop with the full tool registry and subagents on demand |
| **Classic (CrewAI routing)** | `classic` | You want the pre-v4 behaviour: classify the request, dispatch it to one of ten specialists |
| **Lite Mode (Small LLMs)** 💡 | `lite_mode` | Models under ~7B parameters. A constrained line protocol with a pre-fetched file list instead of tool-calling |
| **Tool-Augmented ReAct** 🧪 | `tool_augmented_react` | Experimental — the topology the engine was piloted on |

`gitpilot_code` and `autonomous_engineer` both resolve to `default`, so a
preference saved under either name still works.

**Automatic** — the absence of a saved preference — leaves routing to the router.

What each of these *declares* and what actually runs are two different things right
now; see [declaring is not running](#declaring-is-not-running).

---

## Choosing a topology in VS Code

Two routes, both of which write to the GitPilot backend (the backend is what
actually routes work, so a setting that only lived in VS Code would do
nothing):

**Settings UI** — `GitPilot: Settings` → **Agent** → *Agent topology*. Each
topology is a card showing its description and full agent sequence. Click one
to make it the default; the active choice is badged.

**Command palette** — `GitPilot: Select Topology`.

Selecting **Automatic (recommended)** clears the preference and restores
per-request routing.

### Per-request override

A topology chosen in settings is a *default*. An explicit `topology_id` on an
API request always wins, so automation can pin a pipeline without changing
what the UI does.

---

## Choosing a topology from the API

```bash
# List every preset, with its agent sequence
curl http://127.0.0.1:8000/api/flow/topologies

# Read the current default ("topology": null means automatic)
curl http://127.0.0.1:8000/api/settings/topology

# Pin a pipeline
curl -X POST http://127.0.0.1:8000/api/settings/topology \
  -H 'Content-Type: application/json' \
  -d '{"topology": "feature_builder"}'

# Back to automatic routing
curl -X POST http://127.0.0.1:8000/api/settings/topology \
  -H 'Content-Type: application/json' \
  -d '{"topology": "auto"}'
```

An unknown topology id returns **400** rather than being stored. A silently
accepted bad name would look saved and do nothing.

The full graph for one topology — nodes and edges, for rendering — comes from
`GET /api/flow/topology/{id}`.

---

## Execution styles

`execution_style` in the API payload tells you what engine a topology **declares**:

| Style | Meaning |
|---|---|
| `agentic_loop` | The v4 engine: one agent in a `while(tool_use)` loop over the real tool registry, policy-bound, with subagents on demand |
| `crew_pipeline` | A multi-task CrewAI crew. Each agent gets its own task; step *N*'s output chains into step *N+1* |
| `single_task` | One task, with the router selecting the agent |
| `react_loop` | Historical. No topology declares it any more |

Every topology except `classic` now declares `agentic_loop`, because every topology
except `classic` is a **policy document** — a YAML file saying which engine runs,
which dialect, which capabilities are granted, who approves what, whether the run
must prove itself, and where its limits are. The built-ins live in
`gitpilot/topology/defaults/`; you can read any of them, and you can add your own
(see below).

### Declaring is not running

There is a gap between the two right now, and it is deliberate.

A topology's document says the agentic engine should run it. Whether it *does* is
decided by a flag:

```bash
# ~/.gitpilot/flags.json, or .gitpilot/flags.json in the workspace
{"agent_loop": true}            # the tool_augmented_react pilot
{"agent_loop_default": true}    # every topology whose document declares agentic_loop
```

Both are **off by default**. With both off, every topology takes the path it always
took — `default` routes through the CrewAI dispatcher, the five pipelines run their
agent sequences — and a policy document changes nothing. This is why each migrated
pipeline still carries the agent `sequence` it always had: one document drives both
paths.

`agent_loop_default` turns on for everyone only after a benchmark gate: the loop has
to match the legacy path's success rate on all three model tiers (a frontier model,
an 8B local model, a 1.5B local model) with wall time and tokens inside a 1.5×
budget. The harness is `bench/` in the repository:

```bash
python -m bench --list                     # the tasks and which tiers are reachable
python -m bench --out results.json         # run the matrix
python -m bench.gate results.json          # may the default flip?
```

Dominance on all three metrics is explicitly *not* the gate. A run that reads four
files before editing one costs more than a run that edits blind, and is worth it.

### Backing out

`classic` is the pre-v4 architecture — the CrewAI fan-out router that `default` used
to be, under its own name, for one release cycle. Selecting it opts out of the loop
whatever the flags say. Two other ids resolve to the current `default` so a saved
preference never breaks: `gitpilot_code` and `autonomous_engineer`.

### The loop, without a server

The easiest way to watch what the engine does:

```bash
gitpilot run --engine loop --workspace . --headless \
  --max-iterations 12 --read-only -m "how does the test suite work?"
```

`--headless` prints one JSON object per event — every turn, tool call, result and
state change — rather than only a summary at the end. The loop works against the
local checkout and needs no GitHub token, so `--repo` is only required for
`--engine legacy`.

One thing improves for everyone the moment the engine is on: a session with **no
GitHub repository attached** — a plain folder, or a local git checkout — can stream.
The legacy executor closes the v2 stream immediately for those sessions, which is
precisely the local-model case this engine exists for.

### Writing your own topology

A topology is a document. Drop one in `~/.gitpilot/topologies.yaml` (yours) or
`.gitpilot/topologies.yaml` (the project's):

```yaml
topologies:
  - id: docs_only
    name: Docs Only
    icon: "📝"
    execution:
      engine: agentic_loop
      dialect: auto          # auto | native | react_text | lite
    agent:
      role: software_engineer
      subagents: [explorer]
    capabilities:
      fs.read: true
      fs.glob: true
      fs.grep: true
      fs.write: { paths: ["docs/**", "*.md"] }
      git.status: true
      git.commit: ask
    approval:
      mode: session_default   # may only *tighten* the session's mode
    verification:
      tests: "off"            # quote it — YAML reads a bare `off` as false
    limits:
      max_iterations: 20
      max_tool_calls: 60
      max_runtime_seconds: 900
```

Two rules are worth knowing before you write one:

**`approval.mode` can only tighten.** A document may force `plan` or `normal`; it
cannot raise a `normal` session to `auto`. A topology is configuration, and
configuration is not an authenticated escalation channel.

**A project's topologies need trust.** `.gitpilot/topologies.yaml` inside a
workspace is only read once you have trusted that workspace. Cloning a stranger's
repository must not hand it a say in what GitPilot may do without asking.

Naming capabilities is how a document says *only these* — a topology that lists
anything is closed by default, so a misspelled capability withholds a tool rather
than silently granting one. The flow graph in the Agent Workflow view is generated
from the document, so it cannot claim a tool the policy does not grant.

### MCP servers: adding one gives the agent its tools

Add an MCP server in **GitPilot: Settings → MCP servers** (or install MCP Context
Forge from the same panel, which federates many servers behind one endpoint), and its
tools reach the model as `mcp.<server>.<tool>` carrying the server's own
`inputSchema`. Add a Postgres MCP server and the agent can query Postgres; the tools
it offers, their arguments and their descriptions all come from the server itself
rather than from anything GitPilot guessed.

Three things follow from a tool being someone else's:

- **A mutating tool asks first.** `classify_risk` sorts tool names into low / medium /
  high, and medium and high require approval — the same classifier behind the risk
  badge the settings panel shows you, so the badge and the prompt cannot disagree.
  Mutating MCP tools also declare an external write, which means a read-only topology
  refuses them outright while still permitting a query.
- **A tool you switch off is absent, not refused.** Per-tool toggles in the panel are
  applied before registration, so a disabled tool never appears in the model's tool
  list at all. Withholding one costs nothing; offering it and then refusing it costs a
  turn.
- **They are the first thing to go when the window is tight.** A 1.5B model with six
  tool slots gets the filesystem and the shell, not a third-party server's twentieth
  helper. MCP tools are also withheld from the LITE dialect, whose names-only
  rendering cannot carry their arguments intact.

Registration is behind the `mcp_tools` flag, off by default: an MCP server is a third
party, and a run that starts calling one because a config file exists is not a run you
asked for.

```bash
{"mcp_tools": true}   # ~/.gitpilot/flags.json
```

Compatibility with [MCP Context Forge](https://github.com/ruslanmv/mcp-context-forge)
is a contract, not an integration: GitPilot's gateway URL is the Forge's `/mcp`
streamable-HTTP mount, `/health` is the reachability probe, and `/gateways` backs the
sync. A server added through the panel gets its transport derived from its endpoint, so
the panel is the only place you need to configure it.

### What you see while it runs

Four things the engine surfaces that the single-pass path could not:

**A task checklist.** For work with several steps the model keeps a list, marking
one item in progress and completing it before starting the next. It is not a plan
— nothing enforces that the list be followed, and a model that rewrites it after
learning something is behaving correctly. A model too small to maintain the list
gets the same panel anyway: the engine derives a coarse read → change → check list
from the phase it is in, so the UI does not depend on the model's size.

**Subagents, nested.** The agent can hand a self-contained piece of work to a
focused child — `explorer`, `reviewer`, `researcher` or `test_analyst` — and gets
a structured answer back rather than the child's whole narration. The child's
activity appears as a nested block, and it has its own run journal. A child can
never do more than its parent: the capability mask is the *intersection* of the
two, so a read-only run delegates read-only work, whatever the template asks for.

**Verification, before the answer.** A topology can require that changes be
tested. A run that modified files is refused a finish until the tests have run,
and the model is told what is missing so it can act. If it runs out of attempts,
the answer says the changes are unverified rather than reporting success. A project
with no test suite is not blocked on tests it does not have.

**Real streaming.** Tokens as the provider produces them. A `react_text` model
streams whole lines rather than tokens, because a half-written tool call is not
something worth showing; a `lite` model announces each phase, because its output
is a line protocol and streaming it verbatim would show you protocol instead of
progress.

### Resuming

A run keeps a journal, and the journal is enough to pick it up again — after a
crash, a disconnect, or an approval nobody answered. What replays is *facts*, not
messages: the calls made, their results, the files read and changed, the budget
spent. The transcript is re-rendered for whichever dialect resumes, so swapping the
model between the crash and the recovery works rather than conflicting.

A run is resumable when its journal has no terminal line. Pressing stop writes one,
so a cancelled run stays cancelled.

---

## Permission modes are separate

Topology decides *which agents run*. Permission mode decides *what they may do
without asking*. They compose:

| Mode | Behaviour |
|---|---|
| **Ask** (`normal`, default) | Anything that mutates state shows an approval card |
| **Auto** | Tools execute without prompting — but still snapshot, and still refuse the destructive |
| **Plan** | Read-only. The plan is produced; all writes and commands are blocked |

Plan mode with a write-capable pipeline is not a contradiction: the pipeline
runs, and the writes are blocked. You get the plan and the review without the
changes.

### The mode lives on the session

One place, and one way to raise it. `PUT /api/permissions/mode` with a
`session_id` sets it, and that is the only route to `auto`:

```bash
curl -X PUT http://127.0.0.1:8000/api/permissions/mode \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "abc123", "mode": "auto"}'
```

A `permission_mode` field on an individual chat request may only **restrict** the
session's mode for that request — an `auto` session plus a `plan` request runs in
plan mode, and a `normal` session plus an `auto` request stays normal. The
asymmetry is deliberate: a request body is not an authenticated channel, so it
cannot be used to grant privileges the session does not have.

### What "dangerous" means

Not a list of tool names. A `terminal.run` is judged by the command it was given,
so `pytest` and `rm -rf /` reach different verdicts through the same tool:

| Class | Examples | Ask mode |
|---|---|---|
| `READ_ONLY` | `ls`, `cat`, `git status`, `git diff` | runs |
| `TEST` | `pytest`, `npm test`, `cargo test`, `make test` | runs |
| `BUILD` | `make`, `npm run build`, `tsc`, `cargo build` | runs |
| `MUTATING` | `pip install`, `mkdir`, `sed -i`, anything unrecognised | asks |
| `GIT_MUTATION` | `git add`, `git commit`, `git checkout -b` | asks |
| `REMOTE_MUTATION` | `git push`, `gh pr create`, `npm publish` | asks |
| `NETWORK` | `curl`, `wget` | asks, or is refused when the sandbox has no network |
| `DESTRUCTIVE` | `rm -rf`, `mkfs`, `dd of=/dev/…`, `shutdown` | **refused, with no prompt** |
| `PRIVILEGED` | `sudo`, `su`, `apt-get`, `docker run` | **refused, with no prompt** |

Chains are judged by their worst part, so `ls && sudo rm -rf /tmp/x` is
privileged rather than a directory listing. A command we do not recognise is
treated as `MUTATING` and asks — the safe direction for a binary nobody has
classified.

The two refused classes are refused rather than prompted on purpose: an approval
dialog for `rm -rf /` has one safe answer, and the unsafe one is a misclick away.
To remove project files, the agent uses `fs.delete`, which asks.

### Capabilities, per session

Beyond the mode, a session can be restricted to specific capabilities — with
path constraints, a network switch, or a blanket "always ask":

```yaml
capabilities:
  fs.read: true
  fs.write: { paths: "src/**", exclude: ["**/*.lock"] }
  terminal.run: { network: false }
  git.commit: true
  git.push: false
  github.pr.create: ask        # asks regardless of how safe it looks
```

A capability that is not granted is not offered: the model never sees the tool's
schema, so it cannot spend a turn calling something that would only be refused.
A mode's `groups:` list from `modes.yaml` maps onto the same thing — `edit` with
a `fileRegex` becomes the path constraint on `fs.write`.

Every decision is written to the run journal before the tool runs, including
plain allows, so "what was this run permitted to do?" is answerable after the
fact rather than inferred.

---

## See also

- [AI provider setup in VS Code](vscode/ai-providers.md)
- [Sandbox and approvals](SANDBOX.md)
- [API stability](API_STABILITY.md)
- Verifying it works end to end: `make verify-e2e` (a local stub) or
  `make verify-e2e-real MODEL=qwen2.5:1.5b` (a pulled Ollama model). The second is
  the one that tests the *model*; see `scripts/verify_end_to_end.py`.
- [How GitPilot fits the Matrix ecosystem](matrix-ecosystem-compatibility.md)
- The engine's design and the work still outstanding:
  [upgrade-plan-v4-agentic-runtime.md](upgrade-plan-v4-agentic-runtime.md),
  [upgrade-v4-batches.md](upgrade-v4-batches.md)
