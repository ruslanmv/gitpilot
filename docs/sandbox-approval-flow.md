# Sandbox Execution — Approval-First Architecture

Status: **Production** as of the `add-sandbox-settings-tab` series.

This document is the source of truth for how code executes in
GitPilot's sandbox. It covers the four user entry points, the
deterministic ExecutionPlan that gates every run on user approval,
and the schema every entry point shares.

---

## Principles

1. **Intent decides the path, not the model.** A chat message classified
   as `execute` short-circuits the LLM planner and produces a
   deterministic ExecutionPlan in pure Python.
2. **No run starts without explicit user approval.** The approval surface
   (ExecutionPlanCard) is visibly distinct from the Action Plan card —
   green border vs orange, "Run in Sandbox" vs "Execute Plan".
3. **Local and MatrixLab are symmetric.** Same request/response shape,
   same UI, same safety checks. The backend pill tells the user which
   actually ran.
4. **Every run is recoverable.** Rerun re-enters the approval flow with
   the same payload — never silent re-execution.

---

## The four entry points

```
┌────────────────────────────────────────────────────────────────────┐
│                        FRONT DOOR                                  │
├────────────────────────────────────────────────────────────────────┤
│ Chat command  │ Code-block ▶ │ File ▶ (sidebar) │ Canvas Run │     │
└──────┬────────────┬────────────────┬─────────────────┬─────────────┘
       │            │                │                 │
       ▼            ▼                ▼                 ▼
   /api/chat   /api/sandbox      window event     /api/sandbox
   /plan       /plan             gitpilot:run-    /plan
   (short-     (inline)          file → /api/     (canvas)
    circuit)                     chat/plan
       │            │                │                 │
       └────────────┴────────────────┴─────────────────┘
                              │
                              ▼
                ┌─────────────────────────────┐
                │ ExecutionPlan { … } returned │
                │ Rendered as approval card    │
                └─────────────┬───────────────┘
                              │
                       user clicks
                       "Run in Sandbox"
                              │
                              ▼
                ┌─────────────────────────────┐
                │ /api/sandbox/run            │
                │ (or /api/chat/execute for   │
                │  file-run via Action Plan)  │
                └─────────────┬───────────────┘
                              │
                              ▼
                       ExecutionCard
                       in chat history
```

---

## The ExecutionPlan schema

Built deterministically by `gitpilot.sandbox_plan.build_execution_plan_for_file`
and `..._for_inline`. Serialized to JSON for the HTTP layer.

```jsonc
{
  "plan_id":         "plan_a1b2c3d4e5f6",
  "goal":            "Run demo.py",
  "source":          "chat | code_block | file_run | canvas | rerun",
  "language":        "python | javascript | bash",
  "command":         ["python", "demo.py"],
  "sandbox":         "subprocess | matrixlab",
  "timeout_sec":     120,
  "network":         false,
  "workdir":         ".",
  "capture_artifacts": true,
  "file":            "demo.py",          // for file-run, else null
  "inline_code":     null,               // for inline runs, else null
  "safety": {
    "checks":   [ { "label": "File exists", "ok": true }, … ],
    "warnings": [
      { "severity": "high",   "label": "Uses os.system / exec / eval", "detail": "…" },
      { "severity": "medium", "label": "Imports network library",       "detail": "…" },
      { "severity": "low",    "label": "Uses plt.show()",               "detail": "…" }
    ]
  },
  "requires_approval": true,
  "parent_run_id":     null               // set on Rerun
}
```

### Safety rules

Coarse, deterministic, never blocking. Each rule is one regex.

| Severity | Rule                                            |
|----------|-------------------------------------------------|
| high     | `os.system`, `subprocess.{call,run,Popen}`, `eval()`, `exec()` |
| medium   | imports `socket` / `urllib` / `requests` / `httpx` / `aiohttp` |
| medium   | reads `os.environ` / `os.getenv`                |
| medium   | spawns subprocesses                             |
| low      | `plt.show()` (MPLBACKEND=Agg auto-injected)     |
| low      | writes files (`open(…, 'w'…)`, `.write()`, `savefig()`, `to_csv()`) |

False positives are cheap — one extra chip; false negatives erode
trust. Tune by tightening regexes, never by hiding warnings.

---

## State machine

```
planned ──► approved ──► queued ──► starting ──► running ──► completed
   │           │                                    │           │
   │           └─ cancelled                         └─► failed / timeout / cancelled
   └─ cancelled  (rejected before approval)
```

Today the executor goes straight from `approved` to `running` to
`completed` because `/api/sandbox/run` is synchronous. The schema is
designed so adding a streaming `/api/sandbox/execute` endpoint
(future batch) won't change any caller — additional events (`queued`,
`starting`, intermediate `running` stdout chunks) slot in cleanly.

---

## Two consent surfaces (NEVER conflate)

```
┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│ ACTION PLAN                     │   │ EXECUTION PLAN                  │
│ (changes the repo)              │   │ (runs code in sandbox)          │
├─────────────────────────────────┤   ├─────────────────────────────────┤
│ Orange left border              │   │ Green left border               │
│ READ / CREATE / MODIFY / DELETE │   │ RUN_FILE / RUN_INLINE           │
│ Header: "Action Plan"           │   │ Header: "EXECUTION PLAN"        │
│ CTA: "Execute Plan"             │   │ CTA: "Run in Sandbox"           │
│ Source: LLM planner             │   │ Source: deterministic Python    │
└─────────────────────────────────┘   └─────────────────────────────────┘
```

`AssistantMessage` renders the Execution Plan when `plan.execution_plan`
is set, and suppresses the Action Plan render in that case. The two
never appear together.

---

## Adding a new entry point

To add a fifth way to run code (say, a "Run on save" hook):

1. Compute the source classification: `file` for a saved file, or
   `code + language` for an inline buffer.
2. POST to `/api/sandbox/plan` with `{ source: "your_source", … }`.
3. Render the returned plan with `ExecutionPlanCard`.
4. On approve, POST to `/api/sandbox/run` (or dispatch
   `gitpilot:run-file` if it should go through chat history).

That's it. The plan builder, safety rules, and approval UI are shared.

---

## Why approval is mandatory even for trusted users

- The plan is computed in **milliseconds**. The approval click is
  ~250 ms of cognitive load — well worth the audit trail.
- Generated code is often subtly wrong. Approval is the user's
  last chance to spot a `rm -rf` before it runs in a workspace
  bind-mounted from their host.
- The same surface enables one-click **Rerun** with the same
  approved command — no fresh classification, no model variance.

If a future product decision requires opt-out for trusted snippets,
the surface is already there: `Settings → Sandbox → Allow one-click
run for generated snippets` flips `requires_approval=false` in the
plan response and the UI short-circuits the approval step.

---

## Files

| File | Role |
|------|------|
| `gitpilot/sandbox_plan.py` | Deterministic ExecutionPlan builder + safety analyzer |
| `gitpilot/sandbox_api.py` | `POST /api/sandbox/plan` + `POST /api/sandbox/run` |
| `gitpilot/agentic.py` | `try_execute_short_circuit` attaches `execution_plan` to PlanResult; `execute_plan` emits `next_actions` |
| `frontend/components/ExecutionPlanCard.jsx` | Green approval card (full / compact variants) |
| `frontend/components/SandboxStatusWidget.jsx` | Sidebar health pill + one-click recovery |
| `frontend/components/FileTree.jsx` | Sidebar ▶ Run button — dispatches `gitpilot:run-file` |
| `frontend/components/ChatPanel.jsx` | Listens for `gitpilot:run-file`, routes through chat plan |
| `frontend/utils/planKind.js` | Classifies a plan response as executable / informational / empty. `EXECUTE` and a bare `execution_plan` both count as executable — omitting them is what made every sandbox run report "The model returned an empty plan". |
| `frontend/components/AssistantMessage.jsx` | Renders ExecutionPlanCard, ExecutionCard with Rerun, and next_actions |
| `frontend/components/RunnableCodeBlock.jsx` | Code-block ▶ → plan → approve → run |
| `frontend/components/SandboxCanvas.jsx` | Canvas split view, same approval flow |

---

## Tests

| File | Coverage |
|------|----------|
| `tests/test_sandbox_plan.py` | 23 tests — pure-Python builder + endpoint |
| `tests/test_execute_short_circuit_plan.py` | 4 tests — plan attached for RUN_FILE intents |
| `tests/test_plan_kind_classifier.py` | 9 tests — runs `planKind.js` under node against the real backend payload |
| `tests/test_post_create_next_actions.py` | 7 tests — post-CREATE Run buttons |
| `tests/test_sandbox_api.py`, `tests/test_sandbox.py` | Existing — no regressions allowed |
