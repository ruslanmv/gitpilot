# Configuring agent topologies in VS Code

A **topology** decides which agents run on your requests and in what order.
This page covers driving that from VS Code; for what each agent and topology
actually does, see [Agent architecture and topologies](../agents.md).

---

## Where it lives

**`GitPilot: Settings`** → **Agent** → *Agent topology*.

Topologies load when you open the section, and each one is a card showing its
description and full agent sequence:

```
AGENT TOPOLOGY

  Automatic (recommended)                              Active
  GitPilot routes each request to the agents that fit it.
  Agents chosen per request

  PIPELINES — FIXED AGENT SEQUENCES

  🚀 Feature Builder
  Full pipeline: explore > plan > implement > review > PR
  Explorer  →  Planner  →  Coder  →  Reviewer  →  PR Manager

  🐛 Bug Hunter
  Find and fix a defect, then open a PR
  Explorer  →  Coder  →  Reviewer  →  PR Manager

  🔍 Code Inspector
  Read-only audit
  Explorer  →  Reviewer
  ...
```

Click a card to make it the default. The choice is saved to the GitPilot
backend immediately — the backend is what routes work, so this is not a
setting that only takes effect on restart.

The command palette equivalent is **`GitPilot: Select Topology`**.

---

## Automatic vs. a pinned pipeline

**Automatic** is the recommended default and is the *absence* of a saved
topology. Each request is classified and routed: a review request goes to the
Reviewer, an issue request to the Issue Manager, a build request through
explore → plan → code.

**Pin a pipeline** when you want the same thing to happen every time. The
usual reason is a guaranteed stage the default flow does not include:

| You want | Pick |
|---|---|
| Every change reviewed before it lands | **Feature Builder** or **Bug Hunter** |
| A pull request opened automatically | any pipeline ending in PR Manager |
| Analysis with no possibility of a write | **Code Inspector** or **Architect Mode** |
| Minimum latency on a change you have already scoped | **Quick Fix** |

Selecting **Automatic (recommended)** clears the preference again.

---

## What a pinned pipeline changes

- **Every** request runs the full sequence, including small ones. Quick
  questions get slower.
- Pipelines containing a write-capable agent create a working branch —
  `gitpilot-<topology>-<slug>-<timestamp>` — before touching anything. Your
  default branch is not modified in place.
- Step *N*'s output is chained into step *N+1*, so the Reviewer sees what the
  Coder produced.

---

## Combining with permission mode

Topology decides *which agents run*; permission mode decides *what they may
do unattended*. Both are on the **Agent** page and they compose.

A useful pairing: **Feature Builder** with **Plan** mode. The full pipeline
runs — including the review — and every write is blocked. You get the plan
and the audit without the changes.

| Goal | Topology | Mode |
|---|---|---|
| Review a codebase, change nothing | Code Inspector | Plan |
| Design an approach first | Architect Mode | Plan |
| Ship a feature with approval at each write | Feature Builder | Ask |
| Unattended batch work | Quick Fix | Auto |

!!! warning
    **Auto** mode executes every tool without prompting. Pair it with a
    pipeline whose scope you trust.

---

## Enterprise rollout

**Standardise the pipeline.** Pin one topology so every engineer's work
follows the same path. Feature Builder gives an audit trail: explored,
planned, implemented, reviewed, PR'd — each stage's output visible in the
run.

**Keep review non-optional.** A pinned topology containing a Reviewer means
the review cannot be skipped by phrasing a request differently. Routed mode
cannot promise that.

**Automate per-request.** A topology set in settings is a default; an
explicit `topology_id` on an API request overrides it. CI can pin
`code_inspector` for audit runs without disturbing what developers see:

```bash
curl -X POST http://127.0.0.1:8000/api/settings/topology \
  -H 'Content-Type: application/json' \
  -d '{"topology": "feature_builder"}'
```

An unknown id returns 400 rather than being stored silently.

**Constrain execution.** Topology governs agents, not blast radius. Pair it
with permission mode and the [sandbox](../SANDBOX.md), which jails the
working directory, scrubs secrets and applies a destructive-command denylist.

---

## Troubleshooting

**The Agent page says topologies need the server**
Topology presets and the saved preference both live on the backend. Connect
from **AI Providers** — which has [recovery
actions](ai-providers.md#when-the-gitpilot-server-is-not-running) — then
return.

**My topology choice had no effect**
Earlier builds wrote `gitpilot.defaultTopology` into VS Code settings only,
where nothing read it. It now writes through to the backend. If you set a
topology in an older version, set it again.

**A pipeline seems to skip a stage**
An agent id that does not resolve is skipped with only a log line. Check the
**GitPilot** output channel; `tests/test_agent_topologies.py` guards the
shipped sequences against this.

---

## See also

- [Agent architecture and topologies](../agents.md) — the agents and presets
- [AI provider setup](ai-providers.md) — choosing the model behind the agents
- [Sandbox and approvals](../SANDBOX.md)
