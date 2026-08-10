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

A topology is a named execution shape. Nine ship with GitPilot, in two
categories.

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

### System topologies — routed

No fixed sequence; agents are chosen per request.

| Topology | Style | Use it when |
|---|---|---|
| **Default (CrewAI Routing)** | single task | General use. The router picks agents by intent |
| **GitPilot Code (ReAct + Subagents)** | ReAct loop | Complex, open-ended work that needs iteration |
| **Lite Mode (Small LLMs)** | single task | Models under ~7B parameters. Simplified prompts, single-agent execution, pre-fetched context instead of tool-calling |
| **Tool-Augmented ReAct** | ReAct loop | Experimental |

**Automatic** — the absence of a saved preference — is the recommended
default. It leaves routing to the router.

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

`execution_style` in the API payload tells you how a topology runs:

| Style | Meaning |
|---|---|
| `crew_pipeline` | A multi-task CrewAI crew. Each agent gets its own task; step *N*'s output is chained into step *N+1* |
| `single_task` | One task, with the router selecting the agent |
| `react_loop` | An iterative reason-act loop with subagents |

---

## Permission modes are separate

Topology decides *which agents run*. Permission mode decides *what they may do
without asking*. They compose:

| Mode | Behaviour |
|---|---|
| **Ask** (default) | Each dangerous tool — write, edit, run, commit — shows an approval card |
| **Auto** | Tools execute without prompting |
| **Plan** | Read-only. The plan is produced; all writes and commands are blocked |

Plan mode with a write-capable pipeline is not a contradiction: the pipeline
runs, and the writes are blocked. You get the plan and the review without the
changes.

---

## See also

- [AI provider setup in VS Code](vscode/ai-providers.md)
- [Sandbox and approvals](SANDBOX.md)
- [API stability](API_STABILITY.md)
