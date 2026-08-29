# GitPilot v4 — Batch Execution Plan

**Companion to:** `docs/upgrade-plan-v4-agentic-runtime.md` (the design; section
references below like §5.2 point there)
**Status:** Phases 0, A, B, C, D, E, F and G shipped, plus H1, H2 and H5's docs half — the engine runs a real task,
nothing it does executes unauthorized, the console shows what it is doing, a crash
is recoverable, and every topology is now a declarative policy document over that
one engine. All twelve registered defects are closed. `default` carries the agentic
document; the flag that executes it (`agent_loop_default`) stays off until the §18
benchmark gate is demonstrated, which needs real models. Phase H is in progress:
H1 (MCP unification) and H2 (the live system payload) have shipped and the docs
cleanup is done; H3–H6 remain.
**Convention:** follows the shipped Phase 1–4 batch discipline
(summarised in `docs/history.md`) — small additive batches, each behind a
feature flag, each revertible in one commit.

---

## 0. How this program runs

### 0.1 Batch rules

1. **One batch = one PR.** If a batch's diff passes ~900 LOC of new code, split
   it and append a letter (`V4-A3a`, `V4-A3b`).
2. **Flag off = byte-identical behavior.** Every batch that touches a live code
   path must prove a no-op with its flag off. That proof is a test, not a claim.
3. **No batch merges red.** `make test` green, `make typecheck` clean for the
   files the batch adds (strict mypy list grows with the program),
   coverage gate not regressed.
4. **Batches never edit legacy execution paths** except the Phase 0 defect
   fixes and the explicit deprecations in H4/H5. `agentic.py`'s existing
   functions stay frozen until their topology migrates in G.
5. **Every batch updates this file's status table** in the same PR.

### 0.2 Branch and PR naming

```
branch:    v4/<batch-id>-<slug>          e.g. v4/a1-registry-core
PR title:  V4-A1 · Tool registry core
PR body:   Deliverable / Files / Tests / DoD checklist / flag-off proof
```

### 0.3 Flags introduced by this program

| Flag | Default | Introduced | Gates |
|---|---|---|---|
| `tool_registry` | off | V4-A1 | canonical `toolkit/` registry + tools |
| `model_profiles` | off | V4-B2 | profile resolution, probe, dialect adapters |
| `agent_loop` | off | V4-C2 | the AgentLoop engine (T9 pilot, then more) |
| `agent_policy` | off | V4-D1 | PolicyEngine, live approvals, hooks |
| `agent_resume` | off | V4-F1 | journal resume + SSE replay |
| `agent_loop_default` | off | V4-G5 | `default` topology uses the loop |
| `model_probe` | on | V4-B2 | runtime dialect probe (sub-flag; off = tables only) |

Flags are read through `gitpilot.flags.is_on(name, default)` and settable via
`GITPILOT_FLAGS=...` or `.gitpilot/flags.json`, exactly as in Phase 2.

### 0.4 Definition of Done (every batch)

- [ ] Deliverable exists and is importable from `gitpilot.public_api` if public
- [ ] Tests added at the stated count, all asserting behavior (not smoke)
- [ ] Flag-off no-op test (where a live path is touched)
- [ ] `make test` green · `make typecheck` clean for new files
- [ ] Status table row updated in `docs/upgrade-v4-batches.md`
- [ ] New public symbols documented (docstring + `docs/` mention if user-facing)

### 0.5 Baseline

Suite at program start: **2,181 collected** (`pytest --collect-only -q`; the
"1,266" in the Phase 4 record is that phase's historical count, not a current
figure — it stays as written). Per-batch test budgets below are *minimums* for
new test functions; parametrisation means collected counts run higher, as
Phase 0 already showed (+37 against a +15 budget).

Program budget: **+437 minimum**, distributed as
0: +15 · A: +44 · B: +50 · C: +60 · D: +58 · E: +50 · F: +40 · G: +60 · H: +60.

---

## 1. Master status table

Legend — Size: S ≈ <400 LOC · M ≈ 400–900 · L ≈ 900–1,800 (split if larger).

### Phase 0 — Truth & hygiene (no flag; plain fixes)

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-0A · Dead code & counts | ✅ | S | — | +5 | F9, F10 resolved; test count refreshed; CHANGELOG debt entry |
| V4-0B · MCP correctness | ✅ | S | — | +8 | F3 closure bug, F4 stale contracts |
| V4-0C · Shell-safety dedup | ✅ | S | — | +14 | F6: one denylist source, env scrub everywhere |
| V4-0D · Auth propagation | ✅ | S | — | +8 | F8: token reaches `dispatch_request` on legacy WS |
| V4-0E · Doc truth | ✅ | S | — | +2 | `react_loop` documented as not-yet-executing; v4 plan linked |

**Phase 0 shipped.** 37 tests added (budget was +15 — the parametrised
denylist and header-form cases account for the difference). New module:
`gitpilot/shell_safety.py`. New test files: `tests/test_shell_safety.py`,
`tests/test_mcp_correctness.py`, `tests/test_v4_phase0.py`,
`tests/test_doc_truth.py`. Two beyond-scope fixes went in because leaving them
would have contradicted the batch they sit in: `TerminalExecutor.execute_streaming`
gained the workspace clamp its sibling `execute()` already had, and one existing
test (`test_ws_user_message_triggers_agent_flow`) was updated because it
asserted the token-less dispatch signature — it encoded the defect F8 removes.

### Phase A — Tool foundation · flag `tool_registry`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-A1 · Registry core | ✅ | M | — | +59 | `ToolSpec`, `ToolRegistry`, `ToolExecutionContext`, alias table, schema rendering |
| V4-A2 · Parity harness | ✅ | M | A1 | +20 | recorder + differ, local **and** fake-GitHub fixtures, `make test-parity`, committed corpus |
| V4-A3 · `fs.*` | ✅ | L | A1, A2 | +48 | read/list/glob/grep/write/edit/delete, both backends |
| V4-A4 · `terminal.*` | ✅ | M | A1, A2, 0C | +50 | sandbox-routed run/run_snippet/which/env |
| V4-A5 · `git.*` | ✅ | M | A1, A2 | +20 | status/diff/log/branch/commit/push/stash |
| V4-A6 · `forge.* web.* test.*` | ✅ | M | A1, A2 | +43 | github issues/PRs/search, `test.detect`/`test.run`, web+gitlab deliberately empty |

**Phase A shipped.** 240 tests added (budget +44). New package
`gitpilot/toolkit/` (`registry`, `fs`, `terminal`, `git`, `forge`, `testing`,
`web`, `builtins`); two more CrewAI-free extractions so both tool surfaces
share one implementation — `gitpilot/glob_match.py` (from `agent_tools`) and
`gitpilot/sandbox_routing.py` (from `local_tools`); `parse_test_counts` moved
into `test_detection` so `test.run` and the validation phase cannot report
different numbers. 25 parity cases pass byte-identical across both backends.
Exported from `gitpilot.public_api`; `mypy --strict` clean over 46 files;
coverage 88.92%.

Deviations worth knowing:

- **`fs.grep` local backend keeps `git grep`, not `grep_backend.grep_local`.**
  The batch spec wanted the ripgrep fast path to finally get a caller, but its
  output format differs from `search_in_files`, and parity is the gate this
  phase is built on. `grep_local` gets its caller when a tool needs untracked
  files or ripgrep's speed on a large tree — with a parity case of its own.
- **`web.*` and `gitlab.*` register nothing.** No provider exists for either
  (`WebSearch`/`WebFetch` are strings in four flow graphs and nothing more), and
  the rule is that an absent provider means an absent tool. Both modules carry
  the seam plus a test asserting the emptiness, so the day one lands the
  flow-graph nodes get revisited.
- **Out-of-range arguments are refused, not clamped.** A `timeout` of 99999 is
  a mistake the model should learn about; `coerce_timeout` still protects
  internal callers.
- **A1 defines `SchemaProfile` and `CapabilitySet` locally.** They are the
  structural subsets of B2's `ModelProfile` and D1's `CapabilityMask` that
  schema rendering needs, so A1 ships without waiting on either.
- **Schema-budget pruning ranks by `(priority, registration order)`.**
  Recency-of-use needs the loop's ledger and arrives with it.

### Phase B — Model layer · flag `model_profiles`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-B1 · Provider clients | ✅ | L | — | +42 | `anthropic`, `openai_compat`, `watsonx` clients + record/replay fixtures |
| V4-B2 · ModelProfile | ✅ | M | B1 | +37 | tables, resolution order, runtime probe, `~/.gitpilot/model_profiles.json` |
| V4-B3 · NATIVE dialect | ✅ | M | B1, B2 | +17 | native tool defs, tool-call parsing, streaming deltas |
| V4-B4 · REACT_TEXT dialect | ✅ | L | B2 | +34 | grammar, tolerant parser, repair, one-shot reformat retry |
| V4-B5 · LITE dialect | ✅ | L | B2 | +25 | investigate/act/answer micro-templates ported from `generate_plan_lite` |

**Phase B shipped.** 155 tests added (budget +50). New package
`gitpilot/agent/` — `contracts`, `model_profile`, `adapter`, `providers/`
(`base`, `openai_compat`, `anthropic`, `watsonx`), `dialects/` (`native`,
`react_text`, `lite`). 15 captured response fixtures replayed through
`httpx.MockTransport`, so the real body assembly, headers, error mapping and
parsing all execute with no network. Exported from `gitpilot.public_api`;
`mypy --strict` clean over 49 files; coverage 89.72%.

The dialect a model gets, end to end:

| Model | Resolves to | Via |
|---|---|---|
| `claude-sonnet-4-5` | NATIVE, 200k window, 64 schemas, cacheable prefix | provider family |
| `gpt-4o-mini` | NATIVE, 128k, 64 schemas | provider family |
| `o1-preview` | LITE — the reasoning list beats the family rule | `_INCOMPATIBLE_MODEL_PATTERNS` |
| `llama3:8b` | REACT_TEXT, 16 schemas, compact manual | fallback (no table claims it) |
| `qwen2.5:1.5b` | LITE, 6 schemas, names-only | `_INCOMPATIBLE_MODEL_PATTERNS` |
| anything unknown | REACT_TEXT, then the probe refines it | fallback → probe |

Deviations worth knowing:

- **watsonx reports `supports_native_tools = False`.** Its chat endpoint does
  document tools, but there is no account here to verify the request shape,
  streaming event names, or which hosted models honour them. Claiming support
  that turns out to be wrong is worse than not claiming it — the profile would
  route watsonx to NATIVE and every call would come back malformed until the
  ladder noticed. It goes to REACT_TEXT, which works on anything that can follow
  a grammar. Flip it when someone can test against a live project.
- **watsonx streaming yields one chunk** rather than guessing event names.
- **Three more shared extractions**, same reason as Phase A:
  `reasoning_normalizer.visible_answer` (from `direct_chat._without_reasoning`),
  and the provider factory reuses `direct_chat.resolve_endpoint` rather than
  restating five providers' env-var fallbacks and base-URL suffix rules.
- **`_longest_prefix_window` fixes a real bug found while wiring this up**:
  `context_meter`'s OpenAI and Claude window lookups were exact-match, so
  `o1-preview` and `claude-3-5-sonnet-20241022` silently resolved to the 8k
  default — which would have made the loop compact a 200k conversation.
- **The no-CrewAI assertion runs in a subprocess.** It is a property of this
  import graph, and other suite tests legitimately import CrewAI, so asserting
  on the shared `sys.modules` would pass or fail by test order.

### Phase C — The loop · flag `agent_loop`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-C1 · Context & journal | ✅ | M | A1 | +41 | `AgentContext`, `RunJournal` (JSONL, seq, seed, projection spec) |
| V4-C2 · AgentLoop core | ✅ | L | A1, B3, C1 | +39 | the §5.2 algorithm, `Finality`, budgets, state machine, events |
| V4-C3 · Runner & T9 pilot | ✅ | M | C2 | +45 | `AgentRunner`, engine selection, T9 opt-in through `/api/v2/chat/stream` |
| V4-C4 · Headless & trace | ✅ | M | C3 | +23 | JSONL headless mode, C-scoped trace test, replay stability test |

**Phase C shipped — the engine runs.** 148 tests added (budget +60). New modules
in `gitpilot/agent/`: `journal`, `context`, `loop`, `prompts`, `runner`,
`headless`; 11 new `EventType`s; engine selection in `gitpilot/_api_app.py`;
`gitpilot run --engine loop` in the CLI. Exported from `gitpilot.public_api`
(the permission stubs deliberately *not* — D1/D3/D5 replace their bodies);
`mypy --strict` clean over 55 files; coverage 90.58%.

The trace test is the acceptance criterion for this phase: the read-only task
*"Analyze how this repository's testing infrastructure works. Do not modify
files."* runs against a real fixture checkout with **no steps scripted**. The
provider double reacts to whatever the loop sends rather than replaying a fixed
sequence, so the trajectory is discovered: a config-file read, a glob over
`tests/`, a read-only command, zero mutating calls, and an answer naming the
frameworks it actually found.

Deviations worth knowing:

- **`_finish` emits a terminal `run_finished` event, which the batch spec did not
  list.** Without it the SSE stream never closes: `bus.stream()` runs until the
  subscriber goes away and the endpoint's loop breaks only on `done`/`error`, so
  a completed run left the client on 25-second keepalives until it timed out.
  Found by the first endpoint-level test written for C3, which hung.
- **The `ask` arm is tested in C, not deferred to D3.** The permissive stub never
  returns `ask`, so the path is unreachable in normal operation — it is driven
  directly with an asking policy and a stub approver. The point is that
  `awaiting_approval` is a *loop-owned* journaled transition now, so D3 supplies
  an approver rather than retrofitting state handling into dispatch.
- **The loop path does not register in `_active_executors`.** It is held by the
  runner instead, and `/api/v2/agent/cancel` tries the executor dict first and
  then falls through. Sharing the dict would mean a loop pretending to be a
  `_StreamingExecutor`.
- **Degradation on `parse_error` happens before finality is honoured.** The first
  draft counted two malformed turns before dropping a rung, which made the ladder
  unreachable: a parse-error turn arrives as `FINAL` (the dialect fell back to
  prose), so the run ended before a second one could be counted. A parse error now
  downgrades at once — the dialect has already spent its own reformat retry — and
  the 2-strike counter applies only to provider-level `MalformedResponse`, where a
  gateway hiccup is plausible and a retry is cheap.
- **V4-0E's doc caveat is gone, as designed.** The `react_loop` ratchet fired the
  moment `gitpilot/agent/loop.py` existed. `docs/agents.md` now documents what
  actually happens, and the ratchet was rewritten to derive its assertions from
  code: every topology the doc calls single-pass must really be one (so Phase G
  cannot land without editing the sentence), and the documented flag default must
  match the flag.
- **`BudgetState.started_at` is `Optional[float]`, not `0.0`.** A falsy sentinel
  made `if self.started_at` skip the wall-clock check whenever `time.monotonic`
  read zero, which it can do shortly after boot.
- **`evt.test_result` was being called without `framework`.** The bridge's broad
  `except` turned that TypeError into a silent downgrade to `status_change`; the
  strict-typing gate caught it, and factory failures now log at *warning* with a
  traceback so the class of bug is visible rather than swallowed.

### Phase D — Safety wiring · flag `agent_policy`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-D1 · PolicyEngine | ✅ | L | A1, C2 | +62 | `authorize()`, `CapabilityMask`, `Decision`, F11 mode unification |
| V4-D2 · Command classifier | ✅ | M | D1, 0C | +108 | argv-level classes READ_ONLY…PRIVILEGED, escalate-only rule |
| V4-D3 · Approvals live | ✅ | M | D1, C2 | +17 | F1 `ApprovalRegistry`, F2 loop `ask` arm, SSE respond path |
| V4-D4 · Loop-owned checkpoints | ✅ | M | D1, D3 | +19 | checkpoint on `requires_checkpoint`, gate hook retired, prune caller |
| V4-D5 · Hooks firing | ✅ | M | C2, D1 | +15 | all 10 `HookEvent`s fire; blocking verdicts honored |
| V4-D6 · Sandbox approval token | ✅ | S | D1 | +20 | F7: `/api/sandbox/run` verifies approval server-side |
| V4-D7 · Safety invariants | ✅ | M | D1–D6 | +23 | §19.3 property suite in CI |

**Phase D shipped — nothing executes unauthorized.** 269 tests added (budget
+58). New modules: `gitpilot/agent/policy.py`, `command_class.py`,
`approvals.py`, `checkpointing.py`, `hook_bridge.py`, and
`gitpilot/sandbox_tokens.py`. Exported from `gitpilot.public_api`;
`mypy --strict` clean over 61 files; coverage 91.22%.

**Four libraries got their first caller ever.** `PermissionManager` was
constructed once at module scope and never asked a question. `ToolPolicy` had no
caller outside its own tests. `ApprovalGate.check()` was complete, correct and
unreachable — the executor stored the gate in `self._gate` and never touched it
again, which is also why its `on_checkpoint` hook had never fired. `HookManager`
defined ten lifecycle events, loaded them from `.gitpilot/hooks.json`, listed
them over the API, and fired none. All four now run.

Defects closed: **F1** (SSE approvals unresolvable), **F2** (`gate.check`
uncalled), **F5** (ToolPolicy name mismatch), **F7** (client-side sandbox
approval), **F11** (permission mode triple-tracked), and the second half of
**F6** (the substring denylist becomes the `DESTRUCTIVE` table).

Deviations worth knowing:

- **F1 was worse than the ledger recorded.** The entry says approvals were
  unresolvable; in fact `POST /api/v2/approval/respond` emitted an
  `approval_resolved` *event* that nothing consumed and then returned
  `{"status": "resolved"}`. The pending future waited out its 120-second timeout
  and denied while the client had been told its answer landed — a silently wrong
  answer rather than an error. The endpoint now resolves through the registry and
  **404s when no request is waiting**, because reporting success for a request
  nobody is listening to is how this stayed invisible.
- **`fs.write` was allowed without asking, and the tests caught it.** The first
  draft used `PermissionManager.needs_confirmation` as a *downgrade*: a tool whose
  declared risk is `APPROVAL` was waved through when its `Action` was absent from
  `require_confirmation` — and the inherited default lists `DELETE_FILE`,
  `GIT_PUSH`, `MERGE_PR` and `RUN_COMMAND`, not `WRITE_FILE`. The action list is
  now an escalation channel only: it can add an approval, never remove one, which
  is the same escalate-only rule §8.3 states for classification.
- **`{fs.*: true, fs.delete: false}` granted `fs.delete`.** Wildcard resolution
  ran before the explicit-denial check. Denials are checked first now.
- **D6 ships unflagged, with an env opt-out.** Phase D's flag is `agent_policy`,
  but F7 is a defect of the class Phase 0 shipped plainly — an unauthenticated
  RCE surface — and gating a security fix behind an off-by-default flag would
  leave it open until the Phase G flip. The three frontend Run buttons were
  updated in the same change (one shared `approveAndBuildRunBody` helper, so a
  future call site cannot forget the token), and
  `GITPILOT_SANDBOX_REQUIRE_APPROVAL=0` covers a scripted local install. Five
  existing tests were rewritten to go through plan → approve → run; they had been
  passing by sending only the third request.
- **The token is bound to the code, not just the plan id.** A token bound to an
  id alone would let a client approve `print("hi")` and run something else under
  it. The digest covers language + content, and a file-run plan (which reads at
  execute time) digests its command instead.
- **The agent's own snippet path mints a real token.** `terminal.run_snippet`
  reaches `/api/sandbox/run` through `sandbox_routing`; it is already authorized
  by the policy engine, but an internal-caller exemption would be the same "trust
  whoever asks" defect, so the approval is recorded properly. The store is
  in-process, so it costs a function call rather than two HTTP round-trips.
- **`HookManager` handed every hook every credential.** It built its child
  environment from `os.environ` unfiltered — Batch V4-0C's defect in a second
  place, invisible because nothing fired. It uses `strip_secret_env` now, and
  invariant 6 covers hooks alongside `terminal.run`.
- **`rm -rf <anything>` is DESTRUCTIVE, so `rm -rf build` is denied outright.**
  That is §8.3's table read literally, and it is a real usability cost: cleaning a
  build directory has to go through `fs.delete` (which asks) rather than a shell.
  The alternative — distinguishing "recursive force inside the workspace" from
  outside it — puts a path-safety judgement in the classifier, where a symlink or
  a `..` would defeat it. The sandbox jail is the layer that reasons about paths.
- **`echo "rm -rf /"` is also refused.** The 0C substring denylist matches
  anywhere in the line and `SandboxPolicy.validate` refuses on the same rule, so
  the sandbox would decline to run it regardless. Classifying it as safe would
  produce an approval prompt for something that then fails.
- **Five gate-checkpoint tests moved rather than being deleted.** They drove
  `ApprovalGate.on_checkpoint`, so they proved correct a behaviour that had never
  run. Their intents are asserted against the loop-owned checkpointer in
  `tests/agent/test_checkpointing_hooks.py`, and a guard in the old file fails if
  the gate regrows the hook.
- **The `ask` arm's approval flows through one future in two maps.** The
  registry holds it and the gate's `_pending` points at the same object, so a
  WebSocket client calling `gate.resolve` and an HTTP client calling
  `/approval/respond` resolve the same request instead of one of two.

### Phase E — Claude-Code UX · flag `agent_loop`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-E1 · TODO state | ✅ | M | C2 | +32 | `todo.write`, `todo_updated`, engine-side LITE todo |
| V4-E2 · Delegation | ✅ | L | C2, D1 | +39 | `agent.delegate`, subagent templates, bounded returns, compression |
| V4-E3 · Verification policy | ✅ | M | A6, C2, D1 | +21 | `tests: auto/required`, `max_fix_cycles`, engine-issued `test.run` in LITE |
| V4-E4 · Real streaming | ✅ | M | B3, C2 | +14 | provider token streaming end-to-end; fake chunker deleted |
| V4-E5 · VS Code console | ✅ | L | E1, E2, E4, D3 | +25 | tool rows, TODO checklist, nested delegation, SSE approvals, real cancel |
| V4-E6 · Full trace test | ✅ | M | E1–E5, D7 | +23 | §19.1 read task with policy assertions |

**Phase E shipped — the console demo is real.** 154 tests added (budget +50), 129
Python and 25 in the VS Code extension's own suite. New modules:
`gitpilot/toolkit/todo.py`, `gitpilot/agent/delegation.py`, and
`extensions/vscode/src/agent/eventRouter.ts`. Exported from
`gitpilot.public_api`; `mypy --strict` clean over 63 files; coverage 91.45%;
`npm test` green over 13 extension suites.

The Phase E gate is `tests/agent/test_trace_full.py`: the §19.1 read task with
everything C4 had to defer now attached — the read-only mask *enforced* rather than
requested (the recorded trajectory attempts a write and an `rm -rf`, and both are
refused), zero `ask` decisions, journal ordering per call, a TODO list that
finishes coherent, and a real subagent whose child journal proves it could not
exceed its parent. Its last test asserts that every event the console renders has a
real event behind it, which is what "the demo is real" has to mean.

Deviations worth knowing:

- **`lite_compatible` had never been consumed.** The flag has been on `ToolSpec`
  since Batch V4-A1 and nothing read it — the loop used it for the degradation
  ladder's skip notes and the schema render ignored it entirely. `todo.write` is
  the first tool that genuinely needs it, so `registry.render(lite_only=True)` now
  honours it and the LITE adapter passes it.
- **Effect-less tools were denied in every read-only run — found by the trace
  test.** The read-only gate asked "are this tool's effects a subset of the
  read-only set?", and `todo.write` and `agent.delegate` declare *no* effects at
  all (they change the run's own state, not the world), so both failed the subset
  test and were refused. The gate asks "is this mutating?" instead, so "no
  effects" reads as harmless rather than as unknown-therefore-refused. Without the
  E6 trace test this would have shipped as "the checklist and subagents silently
  do not work in read-only mode".
- **The unverified caveat was unreachable in the case it exists for.** `_finish`
  appended it only for `COMPLETED`, but giving up on verification calls
  `note_skipped`, which turns the state into `DEGRADED` first. The check runs
  before the downgrade and covers both states now.
- **A subagent inherits its parent's profile.** The first draft passed
  `profile=None` to the child's build, which re-resolved from settings — so a
  parent on a frontier model could spawn a LITE child and get a phase machine
  where it expected tool calls.
- **A subagent is always policy-enforced, regardless of the `agent_policy` flag.**
  The flag exists to make Phase D revertible for paths that predate it; a child
  born in E2 has no such history, and a capability mask that were merely advisory
  would make `agent.delegate` exactly the escalation channel the `parent ∩
  template` intersection prevents.
- **"Path-preserving compression" has to be checked, not assumed.**
  `compress_exploration_report` is written for the explorer's *sectioned* report
  format; on free-form text it can return a tidy summary with every path stripped
  out. The compressed form is only accepted when it still names every file.
- **Event tags are merged after the factory, not before.** The first draft tagged
  the payload with `parent_call_id`, and the `agent_events` factories take named
  arguments and drop anything they were not written to expect — so nesting
  produced no nested events at all. The bridge merges into the built event's
  `data` now.
- **The fake chunker is gone, and so is the expectation it set.** The legacy
  executor sliced a finished answer into 80-character pieces 15 ms apart and
  emitted them as `text_delta`. Every visible property of streaming was there
  except the one that matters, and it made the absence of real streaming
  invisible. That path now sends its answer once and says so in a comment; the
  engine streams provider tokens for real, REACT_TEXT streams whole lines (a
  half-written `Action Input:` is not renderable), and LITE announces phases (a
  line protocol streamed verbatim shows protocol, not progress).
- **`streaming.py`'s framing moved into the bus rather than being reimplemented.**
  It had named events, 15-second heartbeats, back-pressure and `StreamMetrics` —
  and no caller. The bus had every caller and a 25-second heartbeat, which leaves
  no margin against the usual 30-second proxy idle timeout. The bus now carries
  both, and `StreamMetrics` is exported once rather than under two names.
- **The extension's event mapping was extracted before it was extended.** It was
  ~120 lines of `else if` inside a `for await` inside a `try` inside a command
  handler, so testing "does a tool row show a duration?" needed a live server and
  a webview. It needed neither: `agent/eventRouter.ts` is a pure function, and the
  25 new tests drive a recorded stream through every render path.
- **`CANCEL_TASK` reached the server.** It aborted the local `fetch`, which stopped
  the extension *listening* while the run carried on writing files and spending
  tokens. The endpoint has existed since Batch V4-C3.
- **The TODO tool is deliberately not a plan.** The whole list is submitted every
  time and the engine diffs it, because a patch protocol would quietly make the
  first list authoritative — and "rewrite it when you learn something" is the
  instruction that keeps this from rebuilding the planner this programme retires.

### Phase F — Resume & context · flag `agent_resume`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-F1 · Resume | ✅ | M | C1, D3 | +34 | `awaiting_approval` persistence, `AgentRunner.resume`, `/api/v2/agent/resume` |
| V4-F2 · SSE replay | ✅ | M | C1, E4 | +37 | `Last-Event-ID`, journal→event projection implemented |
| V4-F3 · In-loop compaction | ✅ | M | C2 | +32 | `ContextBudgetManager` wired at iteration boundaries |
| V4-F4 · Crash & reconnect suite | ✅ | M | F1–F3 | +32 | kill -9 resume, mid-run reconnect, long small-model run |

**Phase F shipped — a crash is recoverable.** 135 tests added (budget +40). New
modules: `gitpilot/agent/resume.py`, `replay_events.py`, `compaction.py`, plus
`POST /api/v2/agent/resume` and `GET /api/v2/agent/runs`. Exported from
`gitpilot.public_api`; `mypy --strict` clean over 66 files; coverage 91.29%.
This closes **F12**, the last open defect in the register.

The gate is `tests/agent/test_crash_recovery.py`, and it uses a real `SIGKILL`.
The child runs in its own process and the parent signals it at ten seeded points
inside a twenty-iteration run; each journal is then resumed to completion in
process. That distinction turned out to matter — see the deviations.

Deviations worth knowing:

- **`CancelledError` is not a crash, and the first draft of the suite tested the
  wrong thing.** The loop *catches* cancellation and writes
  `finish("cancelled")` — correct behaviour for a user pressing stop, and the
  exact opposite of what a `kill -9` leaves. An in-process cancellation therefore
  produced a *terminal* journal that resume correctly refused, so all ten kill
  points "failed" by being unresumable. The suite runs a real child process and
  sends a real signal now, which also means it exercises the fsync policy rather
  than assuming it.
- **Resume replays facts, not messages.** The journal holds calls and results; the
  provider-shaped transcript belongs to the dialect. Rebuilding through the
  adapter that will actually run is what makes a resume survive the operator
  swapping the model between the crash and the recovery — a stored transcript
  would have made that a merge conflict. A LITE run gets its injected file bodies
  back the same way, because they were journaled as `fs.read` results.
- **Budgets are restored, not reset.** A resume that handed back a full iteration
  budget would make "crash and resume" a way to run forever.
- **A pending approval re-prompts.** Batch V4-D3 documented "a restart while
  pending resolves as a deny" as its stopgap; this is the promise it stood in for.
  The interrupted call is fed back as an observation rather than re-issued: the
  model chose it in a context this process no longer has.
- **The LITE dialect cannot overflow its window, and that is why one F4 assertion
  had to change.** It truncates every injected file body to its snippet budget and
  puts only the `READ <path>` lines in the transcript, so a verbose small model
  does not grow it. The compaction-fires assertion belongs to a tool-calling
  dialect, which puts whole observations in the transcript; both are now tested,
  and the LITE property is pinned so a future change to it is visible.
- **`qwen2.5:1.5b` has a 32k window, not 8k.** The first draft asserted 8k from
  memory. The profile table is the authority, and a test asserting a number the
  table does not hold is testing the test.
- **The compaction threshold is over `window − reserve`.** Measured against the
  raw window it would only fire once the request was already too large to send.
  A floor stops a misconfigured tiny window from compacting on every iteration.
- **Paths are carried into the summary, verified rather than hoped for.** A small
  model's context is mostly the file list, and a summary that loses
  `src/parser.py` produces a model confidently editing a file it can no longer
  name.
- **Compaction edits the transcript, never the record.** The ledger and journal
  keep the uncompacted truth, which is also what lets a compacted run still be
  resumed — the two features compose because neither reads the other's output.

### Phase G — Topology v2 & the flip · flag `agent_loop_default`

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-G1 · Schema v2 | ✅ | L | D1 | +136 | `topology/` package, YAML loader, auto flow-graph generation |
| V4-G2 · Default & lite policies | ✅ | M | G1 | +82 | the Autonomous Engineer, T2 alias, T8 as forced-LITE policy |
| V4-G3 · Pipeline topologies as policies | ✅ | M | G1, E2, E3 | +75 | T3–T7 re-expressed; legacy engines opt-out |
| V4-G4 · Benchmark harness | ✅ | L | G2, G3 | +99 | task suite, 3-model matrix, success/time/token reporting |
| V4-G5 · The flip | ✅ | S | G4 | +36 | `default` → `agentic_loop`, `classic` alias |

**Phase G shipped — every topology is a policy document.** 428 tests added (budget
+60). New: `gitpilot/topology/{schema,legacy,registry}.py` with eight documents in
`defaults/`, `gitpilot/yaml_lite.py` (the tiny-YAML loader, extracted from
`modes.py` when the topology loader became its second consumer), and `bench/` — the
legacy-vs-loop harness that gates the flip. `topology_registry.py` is a
re-exporting shim. Coverage 91.5%; `mypy --strict` clean over 78 files.

The registry after G: `default` (the Autonomous Engineer), `classic` (T1's CrewAI
routing, one release cycle), the five pipelines, `lite_mode`, and the T9 pilot —
nine ids, eight of them documents. `gitpilot_code` and `autonomous_engineer` are
aliases of `default`.

**The flip is authored but not taken.** `AGENT_LOOP_DEFAULT_ON = False` in
`gitpilot/agent/runner.py` is one line with the gate cited beside it. A document
declares policy; that constant decides who executes it. Producing the evidence
needs the three model tiers — `python -m bench --out results.json` then `python -m
bench.gate results.json` — and neither Ollama nor an API key is available in the
environment these batches were written in, so the gate has **not** been
demonstrated and the default still runs the legacy engine. That is the honest state
of it: the mechanism, the harness and the gate are shipped; the measurement is not.

Deviations worth knowing:

- **A migrated pipeline keeps its `sequence`.** `gitpilot.agentic` selects a CrewAI
  pipeline on `strategy is fixed_sequence` and nothing else, so a document that
  dropped its sequence would have stopped the topology running *the moment it
  landed* — with the flag still off, in violation of batch rule 2. One document now
  drives both paths: the legacy dispatcher until the flip, the engine after it. The
  dispatcher reports the engine that actually ran (`execution_style`) alongside the
  declared one (`declared_execution_style`), because a response describing a run
  that just happened must not claim the loop ran when CrewAI did.
- **`git.status`/`git.diff`/`git.log` declared `Effect.GIT_LOCAL`.** They report
  local git state; they do not change it. The effect set is what the read-only gate
  reads, so a read-only topology could not look at the repository it was reviewing.
  Found by running the §19.1 trace against the shipped `code_inspector` document
  rather than against a mask built in the test.
- **The read-only gate refused every executing tool on declared effects alone**, so
  `cat Makefile` was denied in the same breath as `rm -rf`. It now judges on the
  class the classifier already computed: `READ_ONLY` passes on the same evidence
  that makes `fs.read` safe, and `TEST` passes only where a document names it in
  `classes: [...]` — never a class this module does not already consider
  read-only-safe, so `classes` cannot become an escalation channel. Two Phase D
  tests encoded the old blanket refusal and now state the narrower rule; one safety
  invariant was made *more* precise rather than relaxed, consulting the
  classification the journal recorded, with a companion test stating the exemption
  positively.
- **`Qualifier` had no `classes` field**, so §15.3's
  `terminal.run: {classes: [READ_ONLY, TEST]}` would have parsed and done nothing.
- **YAML 1.1 reads a bare `off` as `False`**, and §15.1 spells
  `verification.tests: off`. Two of the shipped documents were silently rejected
  until the schema accepted both spellings; it refuses a bare `on` rather than
  guessing which of three values it meant.
- **The benchmark measured a loop with no policy engine.** `agent_policy` is off by
  default, so `AgentRunner` fell back to Phase C's permissive stub and the harness
  scored a write that the shipped `code_inspector` policy forbids. `LoopEngine`
  forces on the flags it is measuring and restores them after — a benchmark
  deciding whether to flip the default has to measure the engine as it will be
  *after* the flip.
- **`_stream_agent_loop` hardcoded the pilot topology.** Once the flag was on, a
  `default` request would have run under T9's policy — a policy nobody selected.
- **The gate refuses partial evidence.** A missing tier, a tier where legacy never
  ran, an empty matrix, or a report scored with a looser budget than the gate
  requires all fail. The failure mode: run two tiers, see green, flip the default
  for the third.
- **Every benchmark task is checked twice** — against the fixture as built (must
  fail) and against a reference solution (must pass). A task that passes untouched,
  or that nobody can pass, measures nothing. The edit checkers execute the result
  rather than matching strings, so a plausible-looking function with a syntax error
  scores zero.

### Phase H — Ecosystem & cleanup

| Batch | Done | Size | Depends | Tests | Deliverable |
|---|---|---|---|---|---|
| V4-H1 · MCP unification | ✅ | L | A1, D1, 0B | +68 | one transport, real `inputSchema`s, risk mapping, pruning |
| V4-H2 · System payload | ✅ | M | C2, D1 | +25 | modes + AGENTS.md + rules + prompt cache into the live prefix |
| V4-H3 · Skills, slash, plugins | ☐ | M | H2 | +12 | message-ingestion expansion, plugin bootstrap |
| V4-H4 · Web app on v2 | ☐ | M | E4, F2 | +8 | React app off the legacy WS; WS deprecated |
| V4-H5 · Deletions & docs | 🟡 | M | G5, H1–H4 | +31 | docs done; the VS Code tree deleted; the CrewAI/MCP-transport deletions remain |
| V4-H6 · Stretch: parallel & docker | ☐ | L | E2 | +8 | worktree fan-out via `agent_teams`, `docker.*` namespace |

**H1 and H2 shipped.** 93 tests added. New: `gitpilot/toolkit/mcp.py` and
`gitpilot/agent/system_payload.py`, plus `Effect.EXTERNAL_WRITE` — MCP mutations
needed an effect in `MUTATING_EFFECTS`, and "wrote a file" and "ran git" are both
wrong about a Postgres UPDATE.

Both batches are the same shape, and it is the shape most of Phase H is: code that
was written, tested and never called. `mcp_client` captured every tool's
`inputSchema` and threw it away; `prompt_cache.build_system_blocks` composed a
complete cacheable payload for nothing that talks to a model;
`modes.activate_mode` had no caller at all, so a user could write a mode, see it
listed, select it, and have it change nothing. `tool_def_pruner` had one caller —
the MCP bridge H1 retires — and had never seen a model's actual schema budget.

Deviations worth knowing:

- **The bespoke MCP transport is deprecated, not deleted.** Its store, toggle and
  risk layer is the part worth keeping, and `toolkit.mcp` calls into exactly that.
  `invoke_remote_tool` and `build_mcp_agent_tools` warn once per process; H5 removes
  them.
- **"The user disabled every server" and "there is no admin store" were the same
  code path**, so a disabled server's tools were registered anyway. `store_overrides`
  distinguishes no-opinion from an empty answer now.
- **H2 does not touch how tools are described.** The dialects already differ exactly
  there — REACT_TEXT and LITE append their rendered manual, NATIVE passes schemas out
  of band — so the payload carries the project and each dialect carries its own tool
  vocabulary. The batch's "dialects differ only in the tool-manual block" was already
  true; nothing needed moving.
- **The tool-def digest is computed over the masked render**, not the whole registry,
  which is what stops a cached prefix describing tools the run cannot use.

**H5 is partly done.** The docs half shipped (see the docs commits), the VS Code
`src/local/` tree is deleted, and an end-to-end verification exists. What remains of
H5 is the *Python* deletions: the CrewAI `@tool` wrappers, `streaming.py`'s duplicate
framing vocabulary, and the MCP transport H1 deprecated. Those three are deliberately
left: `streaming.py` still has a live importer in `agent_executor.py`, the CrewAI
wrappers still serve the legacy dispatcher that `default` runs while
`agent_loop_default` is off, and batch rule 4 says a batch does not edit a legacy
execution path. They come out with the flag, not before it.

**End-to-end verification.** `scripts/verify_end_to_end.py` asks a model to write
`hello.py` and then *runs the file*: settings → provider → real HTTP → dialect → loop
→ policy → registry → `fs.write` → journal → `python hello.py`. `--real` drives a
pulled Ollama model; the default stands a real HTTP server in for the model, so the
transport, dialect, policy and file write are real and only the weights are not.
`make verify-e2e` / `make verify-e2e-real`.

It found two defects that three phases of tests had passed over, both about what the
model is actually *offered*:

- **Every shipped topology document granted nothing it named.** Twelve specs declare a
  grouped capability that is not their id; all eight documents name tool ids, as
  §15.1's own example does. Only the group was checked, so `default` silently withheld
  every git-read and every GitHub tool — absent from the schema list, not refused at
  call time, which is why no test caught it. A grant may name either now, id first, and
  the policy engine matches so a tool the registry offered cannot be refused for the
  opposite reason.
- **Every write tool ranked behind every read tool.** `llama3:8b` has an 8-schema
  budget; the first eight by priority were reads and tests, so the tier this engine
  exists for was offered no way to write a file. Fixed by priority, and pinned by a
  test that asserts the *property* — at every real budget the agent can read, change
  and run — rather than a list of tool names.

**H3, H4 and H6 are not started.** H3 (server-side slash commands, skills
auto-triggers, plugin bootstrap), H4 (the React app on the v2 stream) and H6 (parallel
delegation via worktrees, `docker.*`) remain.

**Ollama was not installable here.** `ollama.com`, `registry.ollama.ai` and
`huggingface.co` are all refused at CONNECT by this environment's network policy, so
no real model was pulled and the §18 benchmark gate is still unmeasured. That is why
the verification ships as a command with a `--real` mode rather than as a result.

---

## 2. Sequencing

### 2.1 Dependency shape

```
Phase 0 (independent, ship first)
   │
   ├──────────────── LANE 1 (tools) ────────────────┐
   │   A1 ─► A2 ─► A3 ─► A4 ─► A5 ─► A6            │
   │                                                │
   ├──────────────── LANE 2 (models) ───────────────┤   both lanes run
   │   B1 ─► B2 ─► B3 ─► B4 ─► B5                  │   fully in parallel
   │                                                │
   └────────────────────┬───────────────────────────┘
                        ▼
              C1 ─► C2 ─► C3 ─► C4          ← the engine exists (T9 pilot)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   D1 ─► D2        E1  E4          H2 ─► H3     ← D and E overlap;
   D1 ─► D3 ─► D4                  H1           ← H1/H2 need only D1
   D1 ─► D5, D6
   D1–D6 ─► D7
        │               │
        └──── E2, E3, E5 ─► E6 ────┐
                        │          │
                  F1, F2, F3 ─► F4 │
                        │          │
                        └──► G1 ─► G2, G3 ─► G4 ─► G5 ─► H4, H5 ─► H6
```

### 2.2 Critical path

`A1 → C1 → C2 → C3 → D1 → D3 → E2/E3 → G1 → G3 → G4 → G5`

Everything else can be scheduled around it. `B1→B2→B3` must land before `C2`
but runs concurrently with the whole A lane.

### 2.3 Recommended merge order

**Solo developer** (strictly linear, always shippable):

```
0A 0B 0C 0D 0E │ A1 A2 A3 A4 A5 A6 │ B1 B2 B3 B4 B5 │ C1 C2 C3 C4 │
D1 D2 D3 D4 D5 D6 D7 │ E1 E4 E2 E3 E5 E6 │ F1 F2 F3 F4 │
G1 G2 G3 G4 G5 │ H1 H2 H3 H4 H5 (H6)
```

**Two developers:**

| | Dev 1 | Dev 2 |
|---|---|---|
| Sprint 1 | 0A–0E, A1, A2 | B1 |
| Sprint 2 | A3, A4 | B2, B3 |
| Sprint 3 | A5, A6 | B4, B5 |
| Sprint 4 | C1, C2 | H1 (needs A1 only) |
| Sprint 5 | C3, C4 | H2 |
| Sprint 6 | D1, D2, D3 | E1, E4 |
| Sprint 7 | D4, D5, D6, D7 | E2, E3 |
| Sprint 8 | F1, F2, F3, F4 | E5, E6, H3 |
| Sprint 9 | G1, G2, G3 | G4 |
| Sprint 10 | G5, H4 | H5, H6 |

**Three developers:** give Dev 3 the H lane from Sprint 4 (H1, H2, H3, then
H4) and the benchmark harness G4 from Sprint 8 — it is the longest
independent piece of work in the program.

### 2.4 Demoable milestones

| After | What a stakeholder can see |
|---|---|
| A6 | every tool callable through one registry with schemas, parity-proven |
| C4 | **the loop actually iterates**: a headless run discovering a trajectory (T9, flag on) |
| D7 | approvals and checkpoints genuinely gating a live run |
| E6 | the full Claude-Code experience in VS Code: TODO, delegation, streaming, verification |
| F4 | kill the server mid-run, resume, finish |
| G5 | `default` is autonomous for everyone |

---

## Phase 0 — Truth & hygiene

Plain bug fixes and honesty. No flags, no new architecture, nothing to revert
later. Ships in days and de-risks everything after it.

Deliberately **not** here: F1 (dead SSE approval resolve), F2 (uncalled gate),
F7 (client-side sandbox approval), F11 (permission triple-tracking). All four
sit on code paths that are currently inert; fixing them standalone produces
untestable churn. They land in D3/D6/D1 where they become live and provable.

### V4-0A · Dead code & counts

- **Files:** `gitpilot/agentic.py` (delete the duplicate topology-resolution
  block at ~L2199-2207), `gitpilot/_api_core.py` (delete — nothing imports it;
  if the team prefers to keep it, add a module-level deprecation per
  `docs/API_STABILITY.md` instead), `CHANGELOG.md`, `README.md` badge.
- **Also:** refresh the test-count claims in `docs/history.md` and the README
  badge from a real `pytest --collect-only -q` run.
- **Tests:** +3 — no behavior change on `dispatch_request` for each routing
  strategy; a guard test asserting `_api_core` is gone (or deprecation-warns).
- **DoD:** `grep -rn "_api_core"` returns only the changelog entry; CHANGELOG
  has a "Documentation debt" entry naming the shipped-but-unlogged
  react_loop/sandbox/checkpoints/Phase 1–4 work.

### V4-0B · MCP correctness

- **Files:** `gitpilot/mcp_client.py` (bind `_name`/`_desc`/`_conn` per
  iteration via default args or `functools.partial` — the late-binding closure
  at ~L229-238 makes every wrapper call the last-discovered tool),
  `gitpilot/mcp_server_tools.py` (`SkillManager.list_skills()` is the real
  method; `agentic.generate_plan` is the real entrypoint).
- **Tests:** +5 — two discovered tools produce two distinct callables;
  `list_skills` returns registered skills; the plan tool reaches
  `generate_plan`; unavailable-dependency paths still degrade politely.
- **DoD:** the skills tool no longer silently returns `{"skills": []}` when
  skills exist.

### V4-0C · Shell-safety dedup

- **Files:** new `gitpilot/shell_safety.py` (single `BLOCKED_PATTERNS` source
  + `strip_secret_env()`), `gitpilot/sandbox.py` and `gitpilot/terminal.py`
  both import it.
- **Why:** `terminal.py`'s copy is missing the `shutdown -h/-r` entries and its
  `execute()` passes `{**os.environ, ...}` unscrubbed — and it is still the
  executor for the lint/test validation phase.
- **Tests:** +4 — both executors reject the full pattern set; neither leaks
  `GITHUB_TOKEN`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` into a child process.
- **DoD:** exactly one denylist literal in the codebase. This becomes the
  `DESTRUCTIVE` table's home in D2.

### V4-0D · Auth propagation

- **Files:** `gitpilot/_api_app.py` (~L3815-3819) — pass the session's GitHub
  token into `dispatch_request` on the legacy `/ws/sessions` path.
- **Tests:** +2 — the WS path forwards a token; absent token still works
  read-only.

### V4-0E · Doc truth

- **Files:** `docs/agents.md` (the `react_loop` execution-style section must
  say it currently executes as `single_task`), `docs/vscode/agent-topologies.md`,
  and a pointer from both to `docs/upgrade-plan-v4-agentic-runtime.md`.
- **Tests:** +1 — extend the existing docs link checker to cover the new files.
- **DoD:** no doc claims a running ReAct loop until C3 ships it.

---

## Phase A — Tool foundation · `tool_registry`

Goal: one registry, canonical ids, JSON Schemas, proven-identical behavior.
Nothing consumes it yet — the loop arrives in C.

### V4-A1 · Registry core

- **New:** `gitpilot/toolkit/__init__.py`, `registry.py`.
- **Contract:** `ToolSpec` (id, title, description, `params_schema`,
  capability, `risk`, `effects`, `timeout_s`, `lite_compatible`),
  `ToolRegistry.register/schemas/execute`, `ToolExecutionContext` (workspace or
  repo binding + token, `SandboxManager`, session id, event emitter),
  `LEGACY_ALIASES` mapping CrewAI display names and VS Code tool ids →
  canonical ids (§21.1).
- **Behavior:** `schemas(capabilities, profile)` renders `full` / `compact` /
  `names_only`, applies the profile's `max_tool_schemas` budget by
  (topology priority, recency, prune verdict) and reports what it dropped.
  `execute()` validates arguments against the schema and returns
  `ToolResult(ok=False, error="invalid_arguments")` with the validator message
  rather than raising — a model must be able to self-correct.
- **Tests:** +12 — id validation (namespaced snake_case), duplicate
  registration rejected, alias resolution both directions, all three
  verbosities, budget pruning is reported not silent, arg-validation failure
  shape, sync handler wrapped via `asyncio.to_thread`.
- **DoD:** registry importable from `gitpilot.public_api`; zero production
  callers yet.

### V4-A2 · Parity harness

- **New:** `tests/toolkit/parity/` — a recorder that captures legacy tool
  outputs (`agent_tools`, `local_tools`, `search_tools`, `pr_tools`,
  `issue_tools`) against a fixture repo + a fixture GitHub tree, and a differ
  that replays the same inputs through the registry.
- **Why first:** every tool batch's exit criterion is "byte-identical to
  legacy". The harness has to exist before the tools do.
- **Tests:** +6 — recorder determinism, differ catches an injected 1-byte
  divergence, fixture repo bootstraps offline (no network in CI).
- **DoD:** `make test-parity` records-and-compares; corpus committed.

### V4-A3 · `fs.*`

- **New:** `gitpilot/toolkit/fs.py`.
- **Wraps:** local — `workspace.py` file ops (`_safe_resolve` traversal guard
  preserved); GitHub — `agent_tools.read_file` (via `github_api.get_file`) +
  tree listing, `put_file` for writes (commit-per-write semantics kept);
  `fs.grep` — `grep_backend.grep_local` ripgrep fast path locally (its first
  caller) and the remote path with `FILE_FETCH_CAP=200`; `fs.edit` —
  `edit_backend.apply_edit` with the `expected_occurrences` contract surfaced
  in the schema; `fs.glob` — the existing `_glob_to_regex` translator.
- **Result shape:** `data` carries `path`, `replacements`, `diff`,
  `paths_written` so events and the journal never re-parse prose (§7.4).
- **Tests:** +10 — parity for read/list/glob/grep/write/edit/delete on both
  backends; edit mismatch message teaches self-correction; traversal blocked;
  blocked-path fnmatch still enforced by the legacy layer.

### V4-A4 · `terminal.*`

- **New:** `gitpilot/toolkit/terminal.py`.
- **Wraps:** `local_tools._run_via_sandbox` → `sandbox.py` backends
  (`off`/`subprocess`/`matrixlab`), `SandboxPolicy` cwd jail + secret-env strip
  + output caps, timeout clamp [1,600].
- **Removes:** the legacy `TerminalExecutor` fallback *from the tool path*
  (it survives only inside the validation harness until E3). A sandbox import
  failure is now an honest error, not a silent downgrade to a weaker executor.
- **Tests:** +6 — each backend dispatches correctly; matrixlab routes as a
  bash snippet through the internal endpoint; secrets stripped; workspace jail;
  timeout kill; oversized output truncated with the byte-count marker.

### V4-A5 · `git.*`

- **New:** `gitpilot/toolkit/git.py`. Wraps `workspace.py`'s porcelain
  helpers. `git.push` is `HIGH_RISK`; `git.commit` `APPROVAL`.
- **Tests:** +4 — status/diff/log parity; branch creation; commit returns sha;
  push refuses without an explicit capability (asserted again in D1).

### V4-A6 · `forge.* web.* test.*`

- **New:** `gitpilot/toolkit/forge.py`, `web.py`, `testing.py`.
- **Wraps:** `github_issues.py`, `github_pulls.py`, `github_search.py`;
  existing web search/fetch (absent provider ⇒ tool absent from the registry,
  never a tool that always errors); `test_detection.detect_test_command` for
  `test.detect` and sandbox-routed execution with `_parse_test_counts` for
  `test.run`.
- **Refines §18:** the design put `test.*` in Phase E. The *tools* are plain
  registry entries and ship here; the *verification policy* that consumes them
  stays in E3. This lets C4's trace test call `test.detect` without pulling E
  forward.
- **Tests:** +6 — `test.detect` on pytest/jest/cargo/go fixtures; `test.run`
  parses counts and emits `test_result`; gitlab shapes stubbed and marked
  unimplemented rather than half-wired.

---

## Phase B — Model layer · `model_profiles`

Goal: GitPilot talks to providers directly, knows what each model can do, and
can render one loop three ways. Runs fully parallel to Phase A.

### V4-B1 · Provider clients

- **New:** `gitpilot/agent/providers/{__init__,base,anthropic,openai_compat,watsonx}.py`.
- **Grown from:** `direct_chat.resolve_endpoint` (provider→endpoint mapping,
  defensive `_extract` for list-content / `reasoning_content` / legacy `text`)
  and `inference/OpenAICompatibleClient` (whitelisted opts, soft-fail).
- **Covers:** Anthropic, OpenAI, Ollama, **Open WebUI**, OllaBridge, custom
  OpenAI-compatible endpoints (all via `openai_compat`), watsonx.
- **Must not:** import CrewAI or litellm, or write process-wide `os.environ`
  (the contamination hazard documented in `llm_provider.py`).
- **Also:** a record/replay fixture harness — real responses captured once,
  replayed in CI.
- **Tests:** +14 — per-provider request shape, streaming assembly, tool-call
  extraction, `<think>`-model handling via `reasoning_normalizer`, 404/quota/
  malformed-response taxonomy, no-env-mutation assertion.

### V4-B2 · ModelProfile

- **New:** `gitpilot/agent/model_profile.py`.
- **Resolution order:** `.gitpilot/models.yaml` override → built-in tables
  (seeded from `_INCOMPATIBLE_MODEL_PATTERNS` → LITE,
  `REASONING_MODEL_PATTERNS` → `strip_reasoning_tags`,
  `context_meter.resolve_context_window` → window, provider families →
  NATIVE) → runtime probe (flag `model_probe`, cached in
  `~/.gitpilot/model_profiles.json` keyed `(base_url_host, model)`).
- **Also:** `smart_model_router` selection stops being advisory — its output
  now reaches adapter construction where a model map is configured.
- **Tests:** +12 — *resolution* yields `qwen2.5:1.5b`→LITE (table),
  `llama3:8b`→REACT_TEXT, claude→NATIVE (family); the **probe is tested
  separately with model ids absent from every table** (a probe test on
  table-covered ids would test nothing, since step 2 wins); probe result
  cached and invalidated by host change; `models.yaml` override beats
  everything; unknown provider degrades to REACT_TEXT.

### V4-B3 · NATIVE dialect

- **New:** `gitpilot/agent/dialects/native.py`, `gitpilot/agent/adapter.py`
  (base + dispatch).
- **Delivers:** registry schemas → provider tool defs; `tool_use`/`tool_calls`
  → `ToolCall`s; results → native tool-result messages; parallel calls when
  supported; true token streaming with tool-call delta assembly; system prefix
  via `prompt_cache.build_system_blocks` with `cache_control` markers on the
  Anthropic path.
- **Tests:** +8 — schema translation both providers, parallel-call fan-out,
  delta assembly across chunk boundaries, cache markers present only for
  Anthropic + flag on, `Finality.FINAL` when no calls returned.

### V4-B4 · REACT_TEXT dialect

- **New:** `gitpilot/agent/dialects/react_text.py`.
- **Delivers:** the `Thought:/Action:/Action Input:` grammar with a rendered
  tool manual at the profile's verbosity; tolerant parsing — strip reasoning
  tags, fenced-or-bare JSON extraction (`_strip_markdown_fences`), fuzzy tool-id
  match, single-quote repair; unknown tool → corrective `ToolResult` listing
  valid ids; **one** reformat retry with an error-specific instruction; one
  tool call per turn; `Finality.FINAL` on `Final Answer:`.
- **Tests:** +10 — a corpus of real malformed local-model outputs (collected
  from Ollama fixtures) each parse or repair correctly; retry fires once and
  only once; unknown tool never crashes the turn; manual respects the schema
  budget.

### V4-B5 · LITE dialect

- **New:** `gitpilot/agent/dialects/lite.py`.
- **Ports from `agentic.py`:** intent regex classification, context prefetch
  (workspace or GitHub tree, 1500-char snippets), the `ACTION filepath`
  protocol with regex parse + fuzzy-extraction fallback + file-list validation,
  `plan_guards` refusal/hallucination checks, lean prompt budgets with
  `FORBIDDEN_KEYWORDS` and tail-positioned known-facts.
- **Adds:** the `READ path` investigate template (≤3 paths/turn, bounded
  rounds), and **synthesized `ToolCall`s for every path the model touches** —
  `fs.read` for prefetch and READ lines, `fs.write`/`fs.delete` for ACTIONs.
  In this batch they execute through the registry directly; D1 makes them pass
  the PolicyEngine. `Finality.FINAL_AFTER_TOOLS` for act-turns, with
  `conclude()` confirming the actions applied.
- **Tests:** +6 — hallucinated path dropped; no-valid-actions degrades to Q&A;
  prefetch and READ lines both produce journalable `fs.read` calls; investigate
  round budget enforced; `conclude()` returns False when an action failed.

---

## Phase C — The loop · `agent_loop`

Goal: the engine exists and runs a real task. This is the batch group the whole
program is for.

### V4-C1 · Context & journal

- **New:** `gitpilot/agent/context.py`, `journal.py`.
- **`AgentContext`** per §9.1 — including `capabilities: CapabilityMask` and
  `tool_context: ToolExecutionContext` (a permissive stub mask until D1) and
  `state: State`.
- **`RunJournal`** — `~/.gitpilot/sessions/<sid>/runs/<rid>.jsonl`, one line
  per event with monotonic `seq`, line types `run_started`, `turn`,
  `tool_call`, `policy_decision`, `checkpoint_ref`, `tool_result`, `approval`,
  `todo`, `compaction`, `state_change`. `run_started` **embeds the seed
  message set** (task + session-transcript ref/index) — the one thing replay
  cannot derive. Ships with the journal→event projection table as data, so F2
  implements it rather than inventing it.
- **Never journaled:** reasoning-tag content, raw wire payloads (asserted).
- **Tests:** +14 — append/seq monotonicity, fsync on state change, seed
  round-trip, projection table covers every wire event type in §21.2 or
  explicitly marks it ephemeral, reasoning-content rejection, oversized tool
  output spills to a side file with a reference.

### V4-C2 · AgentLoop core

- **New:** `gitpilot/agent/loop.py`, `events.py` additions,
  `gitpilot/agent/prompts.py`.
- **Delivers the §5.2 algorithm exactly:** budgets → `adapter.generate` →
  stream text → `FINAL` check → per-call
  `authorize` (permissive stub) + **journal the decision before executing** →
  hooks (stub) → `registry.execute` → journal result → emit →
  `FINAL_AFTER_TOOLS` via `adapter.conclude` → next iteration.
- **State machine** per §5.4 with `awaiting_approval` as a real journaled
  state (D3 drives it; here it is unreachable).
- **Cancellation:** cooperative between calls **and** an `asyncio` cancel point
  inside provider streaming — the between-phases-only cancel of today is the
  bug this replaces.
- **Events added:** `run_started`, `run_resumed`, `iteration`,
  `dialect_downgraded`, `tools_pruned`, `compaction`, `checkpoint_created`;
  `tool_result` and `file_write` get their first emitters.
- **Degradation ladder** (§6.6): two malformed native turns → REACT_TEXT;
  react parse budget exhausted → LITE, marking non-lite-compatible pending
  work `blocked(dialect)` and finishing `status="degraded"` rather than
  claiming success.
- **Tests:** +20 — termination on each `Finality` value; budget exhaustion
  returns partial `budget_exceeded`; decision journaled before execution
  (ordering assertion); denial fed back to the model as an observation;
  invalid args self-correct; cancel mid-stream; ladder downgrade emits the
  event and completes; `degraded` status names skipped steps.

### V4-C3 · Runner & T9 pilot

- **New:** `gitpilot/agent/runner.py`; wiring in `gitpilot/_api_app.py`.
- **Engine selection:** `POST /api/v2/chat/stream` routes to the loop when
  (`agent_loop` on) **and** the resolved topology is `tool_augmented_react`;
  every other combination takes today's path untouched. T9 is the pilot
  because it is already the project's own sketch of this engine.
- **Also:** folder-only/local sessions get streaming for the first time (today
  the v2 stream closes immediately for them) — the exact local/Ollama case the
  program targets.
- **Tests:** +14 — flag off ⇒ byte-identical legacy response; T9 + flag on ⇒
  loop events; other topologies unaffected; folder session streams; run
  registered in `_active_executors` and cancellable; assistant summary
  persisted; bus removed on completion.

### V4-C4 · Headless & trace

- **New:** headless JSONL mode (`gitpilot run --headless --engine loop
  --max-iterations N`), `tests/agent/test_trace_c_scope.py`.
- **C-scoped trace test** (§19.1 gating schedule): the read-only task
  *"Analyze how this repository's testing infrastructure works. Do not modify
  files."* asserting **trajectory categories and journal integrity only** —
  ≥1 config-file `fs.read`, ≥1 `fs.glob`/`fs.grep` over `tests/`, ≥1
  READ_ONLY `terminal.run` or `test.detect`, zero mutating calls, a final
  answer naming the real frameworks. No policy assertions (the PolicyEngine
  arrives in D) and no exact steps — the trajectory must be *discovered*.
- **Replay stability:** the same run replayed from its journal reproduces the
  identical message sequence, including after a forced dialect change.
- **Tests:** +12 — trace test on NATIVE (recorded fixtures) and REACT_TEXT
  (live local model, skipped when absent), replay stability, headless JSONL
  schema.

---

## Phase D — Safety wiring · `agent_policy`

Goal: nothing executes unauthorized, and the safety libraries that have never
run start running.

### V4-D1 · PolicyEngine

- **New:** `gitpilot/agent/policy.py`.
- **Delivers:** `authorize(call, ctx) → Decision{verdict, reason, risk,
  requires_checkpoint}` through the §8 pipeline (capability mask → path policy
  → command classification hook → static rules → verdict); `CapabilityMask`
  with qualifiers (`{paths, exclude}`, `{network}`, `{max_depth}`, the literal
  `ask`); resolution `topology ∩ mode ∩ session`.
- **Absorbs:** `permissions.PermissionManager` (its first caller ever) and
  `tool_groups.ToolPolicy` (categories → key prefixes, `edit_guard.file_regex`
  → `fs.write/edit` qualifiers, MCP guards → `mcp.*`) — the snake-case
  mismatch of F5 disappears because canonical ids *are* snake_case.
- **F11:** `PermissionMode` lives only on the session record. Escalation to
  `auto` **only** via the authenticated `PUT /api/permissions/mode`; the
  per-request `permission_mode` field may only restrict.
- **Loop change:** `authorize` is called for **every** call including reads,
  and LITE's synthesized `fs.read`s go through it — so `blocked_paths` protects
  a 1.5B-model session exactly as it protects a frontier one.
- **Tests:** +14 — read allowed / write asked / push denied under the default
  mask; plan mode blocks every mutating tool in all three dialects; request
  body cannot escalate `normal`→`auto`; path qualifier honored; capability
  absent ⇒ tool not even in `schemas()`; LITE prefetch authorized.

### V4-D2 · Command classifier

- **New:** `gitpilot/agent/command_class.py`, consuming `shell_safety` (0C).
- **Delivers:** shlex + `&&`/`;`/`|` segmentation, argv[0] classification into
  READ_ONLY / TEST / BUILD / MUTATING / GIT_MUTATION / REMOTE_MUTATION /
  NETWORK / DESTRUCTIVE / PRIVILEGED, unknown ⇒ MUTATING (ask).
  **Escalate-only:** classification may raise the verdict, never lower it
  below `ToolSpec.risk` — a crafted command string cannot argue its way down.
  NETWORK is denied outright when the sandbox has `allow_network=false`
  (consistency, not a prompt). Docker commands get rows here (§7.2 deferral).
- **Tests:** +10 — a table-driven corpus incl. chained/piped commands,
  `sudo` anywhere in a chain ⇒ PRIVILEGED, obfuscated `rm -rf` variants,
  escalate-only property, approval prompt text names the class.

### V4-D3 · Approvals live

- **Files:** `gitpilot/approval_protocol.py`, new `ApprovalRegistry`,
  `gitpilot/_api_app.py`, `gitpilot/agent/loop.py`.
- **F2:** the loop's `ask` arm drives the gate — its first real caller.
  `authorize()` never blocks internally; the loop transitions to
  `awaiting_approval`, journals the request and the resolution, and resumes.
- **F1:** pending approvals register per session; `POST /api/v2/approval/respond`
  resolves through the registry (the WS path keeps calling `gate.resolve`).
- **Removes:** `DANGEROUS_TOOLS` — risk now comes from `ToolSpec.risk` +
  classification + qualifiers.
- **Until F1 lands:** a restart while pending resolves as a deny (the safe
  direction), documented in the approval card's timeout copy.
- **Tests:** +10 — SSE approve/deny round-trip (the path that could never work
  before), WS round-trip, `Allow for session` scope, 120s timeout denies,
  denial reaches the model as an observation, `auto` mode approves without
  prompting but still journals.

### V4-D4 · Loop-owned checkpoints

- **Files:** `gitpilot/agent/loop.py`, `gitpilot/approval_protocol.py`
  (retire `on_checkpoint`), `gitpilot/session.py`, `gitpilot/checkpoints.py`.
- **Delivers:** the checkpoint fires from `Decision.requires_checkpoint` before
  the mutating call — one owner, so the `ask` path cannot double-snapshot;
  `ToolCallDescriptor.arguments` populated (never was on the auto path);
  records gain `run_id` + journal `seq`; rewind can optionally truncate the run
  journal to that seq so file state and run state move together;
  `CheckpointStore.prune(keep_last=50)` gets its first production caller.
- **Tests:** +6 — snapshot precedes every mutating call in a recorded run;
  exactly one snapshot on the ask path; rewind restores files + truncates the
  journal; oversized workspace degrades to transcript-only without blocking.

### V4-D5 · Hooks firing

- **Files:** `gitpilot/hooks.py` (unchanged API), loop + tool wiring.
- **Delivers:** `HookManager.fire()`'s first callers —
  `PRE_TOOL_USE`/`POST_TOOL_USE` around dispatch (blocking verdict ⇒
  denial-shaped `ToolResult`), `PRE_EDIT`/`POST_EDIT` from `fs.write/edit`,
  `PRE_COMMIT`/`POST_COMMIT`/`PRE_PUSH` from `git.*`,
  `SESSION_START`/`SESSION_END`/`USER_MESSAGE` from the session runtime.
- **Tests:** +6 — each event fires with the documented env context; a blocking
  pre-hook prevents execution and the model sees why; a hook exception is
  logged and non-fatal; hook latency budgeted.

### V4-D6 · Sandbox approval token

- **Files:** `gitpilot/sandbox_api.py`, `gitpilot/sandbox_plan.py`, frontend
  call sites.
- **F7:** approving an `ExecutionPlan` mints a short-lived token bound to
  (plan_id, session); `/api/sandbox/run` requires it, or a **session-persisted**
  `auto` mode — never a request-body mode claim (§8.1). Card UX unchanged.
- **Tests:** +4 — run without token rejected; token bound to its plan; replay
  of a used token rejected; `auto` session runs without a card.

### V4-D7 · Safety invariants

- **New:** `tests/agent/test_safety_invariants.py` — property tests over
  recorded journals, run in CI on every PR:
  1. no side-effecting `ToolResult` without a preceding `policy_decision` of
     `allow`/`approved`;
  2. no mutating tool in plan mode / read-only topology / after a deny, in any
     dialect including LITE's synthesized calls;
  3. no file content in `ctx.messages` without a journaled `fs.read`;
  4. no reasoning-tag content in any journal line;
  5. DESTRUCTIVE/PRIVILEGED never reach a prompt;
  6. secret-env stripping holds on every `terminal.run`.
- **Tests:** +8. **DoD:** the suite fails loudly if a future batch regresses
  any invariant — this is the program's safety ratchet.

---

## Phase E — Claude-Code UX · `agent_loop`

### V4-E1 · TODO state

- **New:** `gitpilot/toolkit/todo.py`; loop + event wiring.
- **Delivers:** `todo.write` (model submits the full list; engine diffs,
  journals transitions, emits `todo_updated`); system-prompt guidance to mark
  `in_progress` before starting, `completed` immediately after, and **rewrite
  the list when understanding changes** — never enforced as a plan, which
  would rebuild the planner this program retires. In LITE the tool is not
  exposed; the engine maintains a coarse investigate→act→verify TODO so the UI
  is identical for small models.
- **Tests:** +8 — diff/transition journaling, ill-formed list rejected with a
  corrective result, LITE engine-maintained TODO, event payload shape.

### V4-E2 · Delegation

- **New:** `gitpilot/agent/delegation.py`; subagent templates in the topology
  registry.
- **Delivers:** `agent.delegate {agent, task, expected, max_iterations}` →
  child `AgentLoop` with mask = parent ∩ template (**a child can never exceed
  its parent**), `max_depth` from the qualifier (default 1), own journal at
  `runs/<run>/sub/<n>.jsonl`, events tagged `parent_call_id` for UI nesting.
  Structured return only (`{summary, files, findings, recommendations,
  status}`), schema-enforced; over-budget children return partial with status.
  Returns above the parent's observation budget pass through
  `explorer_summary.compress_exploration_report`-style path-preserving
  compression.
- **Templates:** `explorer`, `reviewer`, `researcher`, `test_analyst` — these
  replace T2's flow-graph fictions with real behavior.
- **Tests:** +14 — mask intersection enforced (child cannot write when parent
  cannot), depth limit, structured-return validation, compression preserves
  paths, child failure doesn't kill the parent, nesting events, child journal
  isolation.

### V4-E3 · Verification policy

- **Files:** `gitpilot/agent/loop.py`, policy schema, `toolkit/testing.py`.
- **Delivers:** `verification: {tests: off|auto|required, max_fix_cycles: N}`.
  With `required`, a `FINAL` turn after file mutations is refused until a
  `test.run` has occurred (or cycles exhausted ⇒ result marked `unverified`
  and says so). **In LITE the engine issues `test.run` itself** during the
  verify phase, so the policy is enforceable in every dialect (design rule 5).
- **Retires:** the post-hoc `_run_validation` phase for migrated topologies —
  verification the model can see and react to is the point.
- **Tests:** +10 — required blocks an unverified final; auto skips when no
  framework detected; fix-cycle cap; LITE engine-issued run authorized and
  journaled; `unverified` surfaces in the answer.

### V4-E4 · Real streaming

- **Files:** `gitpilot/agent/dialects/native.py`, `agent_executor.py`
  (delete the 80-char/15ms chunker), SSE writer.
- **Delivers:** provider token streaming end to end; REACT_TEXT streams
  per-line as the grammar permits; LITE emits per-phase. `streaming.py`'s
  better framing (named events, 15s heartbeats, back-pressure, `StreamMetrics`,
  disconnect cancellation) merges into the `AgentEventBus` writer; its parallel
  vocabulary is retired.
- **Tests:** +6 — first-byte latency recorded, heartbeat under a slow model,
  disconnect cancels the run, no fake-chunk timing signature remains.

### V4-E5 · VS Code console

- **Files:** `extensions/vscode/src/extension.ts`, webview template.
- **Delivers:** tool-activity rows with canonical ids and timings, the TODO
  checklist from `todo_updated`, nested delegation blocks, approval cards
  showing the command class and sandbox facts, `CANCEL_TASK` calling
  `POST /api/v2/agent/cancel` (today it only aborts the local fetch), plan
  approval invoking `/api/chat/execute` with the stored plan object instead of
  re-sending prose, and a **Resume run** affordance for interrupted runs
  (active once F1 lands).
- **Tests:** +6 — extension unit tests over a recorded event stream for each
  render path; approval round-trip; cancel reaches the server.

### V4-E6 · Full trace test

- **New:** the §19.1 read task **with policy assertions** (read-only mask
  enforced, zero `ask` decisions, journal ordering) plus the delegation and
  TODO expectations now that E1–E3 exist.
- **Tests:** +6. **DoD:** the Phase E gate — the console demo is real.

---

## Phase F — Resume & context · `agent_resume`

### V4-F1 · Resume

- **Files:** `gitpilot/agent/runner.py`, `journal.py`, `_api_app.py`.
- **Delivers:** `awaiting_approval` persisted; `AgentRunner.resume(run_id)`
  replays the journal into a fresh context, **re-rendering messages for the
  current dialect** (which is why resume survives a model change), restores
  TODO/ledger/budget, re-arms a pending approval; `POST /api/v2/agent/resume`;
  session UI lists resumable runs (journal ends without a terminal state).
  LITE runs replay their injected file contents from journaled `fs.read`
  results.
- **Tests:** +12 — resume after kill during each state; pending approval
  re-prompts rather than silently executing or hanging; dialect-changed resume;
  terminal-state runs are not resumable; double-resume is rejected.

### V4-F2 · SSE replay

- **Files:** SSE writer, `journal.py` projection.
- **Delivers:** `Last-Event-ID: <seq>` replays the journal→event projection
  for lines > seq before attaching to the live bus. Guarantee: **no
  state-bearing event lost**; in-flight `text_delta` chunks are replaced by the
  journaled turn text, keepalives are not replayed (F12).
- **Tests:** +10 — reconnect at every state, no duplicate side-effect events,
  projection covers every state-bearing type, multi-worker read of the journal.

### V4-F3 · In-loop compaction

- **Files:** `gitpilot/agent/loop.py`, `context_budget.py` (first runtime
  caller).
- **Delivers:** at iteration start, projected tokens past
  `0.70 × (window − reserve)` ⇒ `compacting`: stub oversized observations
  first, then fold older turns into a pinned summary
  (`keep_recent_turns=6`, `large_tool_output_tokens=4000`), journal the
  `compaction`, keep the un-compacted truth in the ledger/journal. The
  endpoint-entry `auto_compact` hook stays for legacy paths.
- **Tests:** +10 — threshold math per model window, idempotence (pinned
  summaries not re-folded), file paths survive compaction (the small-model
  failure mode), a 40-iteration 8k-window run completes.

### V4-F4 · Crash & reconnect suite

- **Tests:** +8 — `kill -9` at 10 randomized points in a 20-iteration run,
  each resumes to completion; mid-run reconnect loses no state-bearing event;
  a long `qwen2.5:1.5b` run compacts and finishes. **DoD:** Phase F gate.

---

## Phase G — Topology v2 & the flip · `agent_loop_default`

### V4-G1 · Schema v2

- **New:** `gitpilot/topology/{schema,registry}.py`, `defaults/*.yaml`;
  `topology_registry.py` becomes a re-exporting shim (API stability).
- **Delivers:** the §15.1 document (execution, agent+subagents, capabilities,
  approval, verification, limits, visualization) with the same tiny-YAML
  fallback loader as `modes.yaml`; user topologies from
  `.gitpilot/topologies.yaml`; **auto flow-graph generation** (main agent +
  one node per capability namespace + subagent nodes) so the picker and Agent
  Workflow view keep working without hand-drawn graphs; `approval.mode` may
  only **tighten** relative to the session mode, and repo-local topology files
  sit behind the `trusted_folders` gate — an untrusted clone cannot ship a
  topology that weakens approvals.
- **Tests:** +16 — schema validation, capability parsing incl. qualifiers,
  auto-graph shape, tighten-only approval rule, untrusted repo topology
  ignored, unknown id ⇒ 400 (unchanged), all nine legacy ids still resolve.

### V4-G2 · Default & lite policies

- **New:** `defaults/autonomous_engineer.yaml`, `lite_mode.yaml`.
- **Delivers:** the §15.1 default policy (behind the flag, not yet the
  default); `gitpilot_code` becomes an alias so saved preferences keep working;
  T8 becomes `dialect: lite` + tight limits, with automatic activation now the
  ModelProfile's job.
- **Tests:** +10 — alias resolution, forced-LITE topology, capability set
  matches the document, saved preference migration.

### V4-G3 · Pipelines as policies

- **New:** `feature_builder.yaml`, `bug_hunter.yaml`, `code_inspector.yaml`,
  `architect_mode.yaml`, `quick_fix.yaml`.
- **Delivers:** T3–T7 re-expressed per §15.3 (masks, verification, limits,
  role prompts); `code_inspector` becomes the §19.1 trace test's host;
  `architect_mode` produces the plan document as the output of a read-only
  agentic run (§10) with the plan-approval card unchanged. Legacy
  `sequential_pipeline`/`single_task` engines move behind an opt-out flag.
- **Tests:** +12 — each migrated topology's mask and verification behavior;
  read-only topologies provably read-only; the write topologies still branch to
  `gitpilot-<topology>-<slug>-<ts>`.

### V4-G4 · Benchmark harness

- **New:** `bench/` — a task suite (10–15 tasks: read-only analysis, small fix,
  multi-file feature, failing-test repair, PR flow) × the 3-model matrix
  (Claude / llama3:8b / qwen2.5:1.5b) × {legacy engine, agentic loop},
  reporting success rate, wall time, tokens, approval count.
- **Tests:** +14 — harness determinism with recorded fixtures, report schema,
  a seeded regression detected.
- **Note:** the single longest independent work item — start it early if a
  third developer is available.

### V4-G5 · The flip

- **Delivers:** `default` → `agentic_loop` behind `agent_loop_default`;
  `classic` alias pinning legacy behavior for one release cycle.
- **Gate:** **success rate ≥ legacy** across the matrix, with wall time and
  tokens inside a **≤1.5× regression budget per model tier**. Dominance on all
  three metrics is not the gate — an iterative loop cannot beat a
  single-completion Lite plan on tokens, and demanding it would block the flip
  forever.
- **Tests:** +8 — default resolves to the loop with the flag on, to legacy
  with it off; `classic` works; one full release cycle of dogfooding recorded
  in the PR before the flag default changes.

---

## Phase H — Ecosystem & cleanup

### V4-H1 · MCP unification

- **Delivers:** one transport (`mcp_client.MCPClient`, JSON-RPC, F3 already
  fixed in 0B) feeding `mcp.<server>.<tool>` registry entries with their
  **real `inputSchema`** (captured today, discarded today);
  `classify_risk` → `ToolSpec.risk` (`mutation` ⇒ APPROVAL); admin toggles and
  `tool_overrides` respected; `tool_def_pruner` applied through the profile's
  schema budget. The bespoke `{method:'tools/call'}` HTTP bridge retires; its
  store/toggle/risk layer survives.
- **Tests:** +14 — schema round-trips through NATIVE and REACT_TEXT; disabled
  server absent from `schemas()`; mutation tool asks; budget pruning reported;
  stdio and HTTP transports.

### V4-H2 · System payload

- **Delivers:** `prompt_cache.build_system_blocks(base, agents_md, rules,
  tool_defs, session_tail)` becomes the live system prefix for all dialects
  (dialects differ only in the tool-manual block); `modes.activate_mode()`
  gets its first caller — `system_prompt_block` into the payload, `tool_policy`
  intersected into the mask, `mcp_server_configs` started/stopped with the
  mode; the tool-def digest busts the cache when the capability mask changes.
- **Tests:** +12 — payload order and cache markers, digest changes with the
  mask, AGENTS.md overlay + `@./include` caps, rules budget trimming, mode
  switch changes both prompt and mask.

### V4-H3 · Skills, slash commands, plugins

- **Delivers:** message ingestion runs `SlashCommandRegistry.parse_invocation`
  server-side (the VS Code hardcoded TS list becomes a fallback) and
  `skills.find_auto_triggers`; rendered prompts become the task text;
  `required_tools` enforced as a capability pre-check; session bootstrap pipes
  `PluginManager.load_all_skills/hooks/mcp_configs` into the three managers, so
  an installed plugin is finally active.
- **Tests:** +12 — `/cmd $1..$9/$ARGS` expansion, auto-trigger, missing
  required tool refuses with a clear message, plugin skill/hook/MCP active
  after install.

### V4-H4 · Web app on v2

- **Delivers:** the React app moves to the v2 stream (`frontend/utils/sse.js`
  finally used); legacy `/ws/sessions` frozen and marked deprecated per
  `docs/API_STABILITY.md`.
- **Tests:** +8 — parity of rendered state vs the legacy protocol, deprecation
  warning emitted once.

### V4-H5 · Deletions & docs

- **Deletes:** CrewAI `@tool` wrappers (implementations survive),
  `streaming.py`'s parallel vocabulary, `mcp_tools_bridge`'s transport,
  `extensions/vscode/src/local/` — **including its two dormant importers
  outside that tree** (`src/platform/vscodeAdapter.ts` imports
  `../local/fileOps`; `src/agent/agentEventBus.ts` imports a type from
  `../local/providers/interface`) or TypeScript compilation breaks.
- **Docs:** rewrite `docs/agents.md` and `docs/vscode/agent-topologies.md` for
  the new execution model; add `docs/PHASE5.md`-style shipped summary; pay the
  CHANGELOG debt.
- **Tests:** +6 — no import of deleted modules, TS build green, docs link
  check.

### V4-H6 · Stretch: parallel delegation & docker

- **Delivers:** `agent_teams.setup_worktrees` + `execute_parallel(executor_fn=
  child_loop)` — the seam was built for exactly this, but its task splitting
  ("Part N of task") and overlap-only merge need real work first: an
  LLM-driven splitter and `git merge-tree`-based conflict resolution.
  Plus the `docker.*` namespace if dogfooding shows demand.
- **Tests:** +8.

---

## 3. Program exit criteria

- [ ] §19.1 trace test green on all three dialects (read task + write twin)
- [ ] §19.2 matrix green: Claude/NATIVE, llama3:8b/REACT_TEXT,
      qwen2.5:1.5b/LITE, plus the ladder test (garbage native calls ⇒
      `dialect_downgraded` ⇒ completed run)
- [ ] §19.3 invariants running in CI on every PR
- [ ] `default` is agentic for new sessions; `classic` available one cycle
- [ ] F1–F12 all closed (ledger below)
- [ ] Suite ≈ 2,285 tests; coverage gate not regressed; strict-mypy list grown
      by the new packages
- [ ] `docs/agents.md`, VS Code docs, and CHANGELOG describe what the code does

## 4. Defect-fix ledger

| Defect | Fixed in |
|---|---|
| F1 SSE approval unresolvable | V4-D3 ✅ |
| F2 `gate.check()` uncalled | V4-D3 ✅ |
| F3 MCP closure late-binding | V4-0B ✅ |
| F4 stale MCP server contracts | V4-0B ✅ |
| F5 ToolPolicy name mismatch | V4-D1 ✅ (canonical ids) |
| F6 denylist drift + unscrubbed env | V4-0C ✅, completed in V4-D2 ✅ |
| F7 client-side sandbox approval | V4-D6 ✅ |
| F8 token not propagated on legacy WS | V4-0D ✅ |
| F9 dead duplicate topology block | V4-0A ✅ |
| F10 orphaned `_api_core.py` | V4-0A ✅ |
| F11 permission mode triple-tracked | V4-D1 ✅ |
| F12 no event replay/resume | V4-F1 ✅, V4-F2 ✅ |

All twelve are closed.

## 5. Deviations from design §18 (deliberate)

1. **Phase 0 added.** The design folded the standalone defect fixes into
   Phase D. Five of them (F3, F4, F6, F8, F9/F10) are independent of the
   engine; shipping them first is free risk reduction. The four that live on
   inert paths stay in D.
2. **`test.*` tools ship in A6, not E.** They are plain registry entries; only
   the *verification policy* needs the loop. This lets C4's trace test call
   `test.detect`.
3. **Parity harness (A2) precedes the tool batches.** The design listed
   byte-identical parity as Phase A's exit criterion without saying who builds
   the comparator.
4. **Trace test split across C/E/G** per the design's own gating note — the
   full §19.1 assertions need D (policy) and E (test tools), so Phase C gets a
   trajectory-and-journal-only variant.
5. **Subagents (E2) precede the flip (G5).** The benchmark gate is only
   meaningful if delegation and verification exist, since they are what the
   loop is benchmarked with. (Recorded in the design's §18 note.)
6. **H1/H2 can start right after D1**, before E/F complete — they depend only
   on the registry and the policy engine, and they are what makes the loop
   feel like a product.
