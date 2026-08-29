# GitPilot v4 — The Agentic Runtime Upgrade

**Status:** Design (approved direction, pre-implementation)
**Execution plan:** `docs/upgrade-v4-batches.md` — 48 flag-gated batches with
dependencies, per-batch DoD, and a merge order
**Supersedes:** the execution-engine portions of the v3 phase plan; builds on the shipped Phase 1–4 primitives (`docs/history.md`, `docs/UPGRADES.md`)
**Audience:** GitPilot core contributors

---

## 0. The one-sentence design

> **Topologies describe what an agent is allowed and expected to do. The Agentic
> Execution Engine determines how it dynamically accomplishes the task.**

GitPilot moves from *"a router that dispatches one-shot CrewAI crews"* to *"a single
autonomous `AgentLoop` runtime primitive, with every topology reduced to a policy /
capability configuration over that loop"* — while preserving (and finally **wiring
up**) the approval, sandbox, checkpoint, and observability layers that already exist
in the codebase, and while running the **same loop** on everything from
`qwen2.5:1.5b` on Ollama to Claude Opus with native tool calling.

This is not "add a terminal tool to the existing agents." It is a change of
execution model.

---

## 1. Executive summary

### 1.1 Where we are (verified against the code, not the docs)

| Claim in docs/UI | Reality in code |
|---|---|
| T2 `gitpilot_code` runs a "while(tool_use) ReAct loop with subagents" | **Visualization only.** `agentic.dispatch_request` falls through to the single-task CrewAI path with the comment *"T2's react_loop execution will be wired in a future phase"* (`gitpilot/agentic.py:2216-2219`). No loop module exists anywhere in `gitpilot/`. |
| T9 `tool_augmented_react` wires Phase 1–4 primitives into a ReAct loop | Registry entry + flow graph only (`gitpilot/topology_registry.py:886-911`). Its `agents_used` even lists system primitives (`tool_def_pruner`, `sandbox_runner`, `approval_batcher`) as pseudo-agents. It is the project's own sketch of this design — with no executor. |
| Per-tool approvals with Allow / Allow-for-session / Deny | `ApprovalGate.check()` has **zero production call sites**. `StreamingAgentExecutor` stores the gate in `__init__` and never consults it (`gitpilot/agent_executor.py:59`). The SSE answer path `POST /api/v2/approval/respond` emits a bus event that nothing listens to — only the v2 WebSocket can resolve an approval future (`gitpilot/_api_app.py:4671` vs `:4783`). |
| Checkpoint before every mutating tool call | True only where the gate fires — and the gate never fires on the deployed path, so automatic pre-write checkpoints don't happen during streaming runs. Manual checkpoint/rewind endpoints work. |
| Hooks, modes, skills, slash commands, plugins, MCP tools, AGENTS.md, rules, prompt cache | All **built and tested, none wired into execution**: `HookManager.fire()` has no callers; `modes.activate_mode()` has no callers at all (`doctor.py` only parse-checks `modes.yaml` via `ModeRegistry`); `prompt_cache.build_system_blocks()` has no runtime caller; `mcp_tools_bridge.build_mcp_agent_tools()` and `mcp_client.MCPClient.to_crewai_tools()` never reach an agent; plugin content is inert after install; slash-command parsing has no server-side consumer. |
| Streaming agent output | Simulated. `text_delta` events are a completed batch answer chopped into 80-char chunks with `asyncio.sleep(0.015)` (`gitpilot/agent_executor.py:162-167`). No provider streams tokens anywhere on the server. |

Meanwhile, two artifacts prove the target design is already understood in-repo:

- **`extensions/vscode/src/local/agentLoop.ts`** is a complete, dormant,
  Claude-Code-style agent loop (system prompt → `streamChat` with tool
  definitions → assemble `tool_call` deltas → confirm dangerous tools → execute →
  append `role:"tool"` results → iterate, `MAX_ITERATIONS=25`), with streaming
  OpenAI/Anthropic providers and ten local tools. It is imported by nothing.
- **The AI-coder abstraction** (`docs/AI_CODERS.md`, `gitpilot/inference/`)
  already separates an invariant governance shell from a pluggable patch author
  (`ollabridge` | `claude_code` | `codex` | generic CLI). That is exactly
  "policy over engine" — applied to one subsystem instead of the whole product.

### 1.2 What changes

1. **New core primitive:** `AgentLoop` — a provider-agnostic
   `while(tool_use)` engine that owns execution. Tools do not own execution;
   pipelines do not own execution; the loop does.
2. **`ExecutionStyle` becomes an engine choice, not nine implementations:**
   `agentic_loop` (new), `sequential_pipeline` (legacy `crew_pipeline`,
   retained during migration), `single_task` (legacy, retained during
   migration). Topologies become **policy documents** over these engines.
3. **One loop, three model dialects.** The same `AgentLoop` speaks
   *native tool calling* to frontier APIs, a *structured-text ReAct dialect* to
   mid-size local models, and a *constrained line-protocol ("lite") dialect* to
   sub-7B models. Dialect selection is a per-model capability profile, not a
   separate topology. Today's Lite Mode becomes the smallest dialect of the one
   engine instead of a parallel universe.
4. **CrewAI leaves the hot path.** The loop calls providers through a thin
   client layer (grown from `direct_chat.py` + `gitpilot/inference/`), not
   through `crewai.Agent`/`Crew.kickoff`. CrewAI remains only inside the legacy
   `sequential_pipeline` engine until T3–T7 are migrated, then becomes an
   optional extra.
5. **A canonical `ToolRegistry`** with namespaced tool ids (`fs.read`,
   `terminal.run`, `git.commit`, …), JSON-Schema parameter contracts, risk
   classes, and per-session execution context — replacing module-global
   repo/workspace state and prose display names.
6. **One `PolicyEngine`** that consolidates the four fragmented safety systems
   (`permissions.PermissionManager`, `tool_groups.ToolPolicy`,
   `approval_protocol.ApprovalGate`, `SandboxPolicy` + denylists) into a single
   `authorize(call) -> allow | ask | deny` pipeline with semantic command
   classification — and is actually invoked on every tool call.
7. **Resumable runs.** Every loop iteration journals to disk
   (tool calls, results, TODO state, approvals — never private
   chain-of-thought), integrated with the existing `CheckpointStore` shadow-git
   snapshots, so a run survives VS Code closing, server restarts, and LLM
   timeouts.
8. **The dark-shipped parity libraries get wired in** at the loop's natural
   seams: hooks fire around tool dispatch, modes shape the system prompt and
   capability set, AGENTS.md/rules/prompt-cache assemble the cached system
   prefix, skills/slash-commands expand at message ingestion, plugins feed all
   three at session bootstrap, MCP tools join the registry with real input
   schemas.

### 1.3 What does *not* change

- The topology concept, ids, icons, and the ReactFlow visualization survive.
  Users keep picking T1–T9 (and new ones); the picker UX is untouched.
- The approval UX contract (Allow / Allow for session / Deny; `normal`/`auto`/
  `plan` modes) survives — it finally starts working end-to-end.
- Checkpoint/rewind semantics (`~/.gitpilot/history/<hash>/snapshot` shadow
  repo, `message_index` truncation) survive and gain run-state alongside.
- The event vocabulary (`gitpilot/agent_events.py`) survives and is extended;
  clients keep switching on the JSON `type` field.
- The REST/WS surface stays backward compatible; new behavior arrives behind
  feature flags per the established Phase 1–4 pattern.

---

## 2. Verified baseline

This section is the ground truth the design builds on. Every statement was
checked against the working tree (branch point: `master` @ `05aafb4`).

### 2.1 Execution paths that actually run

| Path | Entry | Mechanism |
|---|---|---|
| Plan → Execute (repo mode) | `POST /api/chat/plan` / `/api/chat/execute` | `agentic.generate_plan` (explorer crew → `explorer_summary.compress_exploration_report` → planner crew with `output_pydantic=PlanResult`) then `agentic.execute_plan` (fresh single-task code-writer crew **per file**, committing via GitHub contents API) |
| Lite Mode (small local models) | same endpoints, auto-selected | `generate_plan_lite` / `execute_plan_lite`: regex intent classification, API-prefetched context, single `tools=[]` LLM call, `ACTION filepath` line protocol, regex parse + fuzzy repair + validation against the real file list |
| Conversational dispatch | `POST /api/chat/message`, `/ws/sessions/{id}` | `agentic.dispatch_request` → `agent_router.route` (regex) → one-shot specialist crew |
| Pipeline topologies T3–T7 | `dispatch_request` with `fixed_sequence` | `_dispatch_pipeline` → one sequential CrewAI crew from `_TOPO_AGENT_MAP` |
| v2 streaming | `POST /api/v2/chat/stream`, `/ws/v2/sessions/{id}` | `StreamingAgentExecutor.execute`: fixed plan → execute → validate(lint+tests) phases delegating to the Lite planners; events on `AgentEventBus` |
| Folder mode | `POST /api/chat/send` | `direct_chat.chat()` — one raw `httpx` completion, fenced-block edit extraction (`_extract_edits_from_answer`) |
| Repair pipeline | `POST /api/v1/gitpilot/runs` | `inference/` coder registry — governance shell + pluggable patch author |

**No path iterates on tool observations.** The model never sees a tool result
and decides what to do next. That is the single capability this whole design
exists to add.

### 2.2 Dark-shipped libraries (built, tested, unwired)

These are assets, not debt — the design wires them instead of rewriting them.

| Library | State | Where it plugs into the loop |
|---|---|---|
| `hooks.py` — 10 lifecycle events incl. `pre_tool_use`/`post_tool_use` with blocking semantics | `HookManager.fire()` has 0 call sites | Around tool dispatch (§5.3 step 6) |
| `modes.py` — persona + `ToolPolicy` + mode-bound MCP servers | `activate_mode()` has zero callers; `doctor.py` only parse-checks the YAML via `ModeRegistry` | Session bootstrap → system prompt block + capability mask (§14.2) |
| `prompt_cache.py` — layered system blocks with Anthropic `cache_control` | no runtime caller | System-prefix assembly in the native dialect (§6.1, §14.4) |
| `tool_groups.ToolPolicy` — categories, edit-guard regex, MCP guards | `allow_tool()` never called in production; `classify()` keys don't match runtime tool names | Becomes the capability layer of the PolicyEngine once tools have canonical snake-case ids (§8.2) |
| `approval_protocol.ApprovalGate` | `check()` never called; SSE resolve path dead | The PolicyEngine's `ask` outcome (§8.4) |
| `permissions.PermissionManager` | storage + toggle only, never enforced | Absorbed into PolicyEngine config (§8.1) |
| `mcp_client.MCPClient` — real JSON-RPC client, captures `inputSchema` | `to_crewai_tools` uncalled (and has a late-binding closure bug) | The MCP transport for registry-joined tools (§14.6) |
| `mcp_tools_bridge` + `tool_def_pruner` | `build_mcp_agent_tools` uncalled | Descriptor plumbing + small-model schema pruning (§6.5, §14.6) |
| `skills.py` / `slash_commands.py` / `plugins.py` | render/parse ready; no chat-path consumer; plugin content inert | Message ingestion + session bootstrap (§14.3, §14.5) |
| `agents_md.py` / `rules.py` | reachable only via the uncalled `prompt_cache` | System-prefix assembly (§14.4) |
| `streaming.py` — named-event SSE, heartbeats, metrics, `ExecutorAdapter` | routes never mounted | Framing layer folded into the unified event stack (§13.2) |
| `context_budget.ContextBudgetManager` | exported, tested, unwired (live path is `auto_compact`) | In-loop context management (§9.4) |
| `agent_teams.py` — worktree-per-subtask scaffold with injectable `executor_fn` | executor never injected | Parallel subagent fan-out, Phase G (§11.4) |
| `smart_model_router.py` | advisory-only (result logged, then ignored) | Model/dialect selection input (§6.4) |
| `test_detection.py` | consumed only by the post-hoc validation phase | Backs the `test.detect` / `test.run` tools (§12) |
| `grep_backend.grep_local` (ripgrep fast path) | zero callers | Backs `fs.grep` for local workspaces (§7.3) |
| extension `src/local/` TS agent loop + providers + tools | not imported by `extension.ts` | Reference implementation; retired once the server loop streams (§16.3) |

### 2.3 Defects to fix during the upgrade (they block correctness regardless)

| # | Defect | Location |
|---|---|---|
| F1 | SSE approval responses can never resolve a pending gate future (`respond` endpoint emits an event; gate has no listener) | `gitpilot/_api_app.py:4652-4671` vs `approval_protocol.py` |
| F2 | `gate.check()` uncalled → approvals and pre-write checkpoints inert on the deployed path | `gitpilot/agent_executor.py` |
| F3 | `mcp_client.to_crewai_tools` late-binding closure — every wrapper invokes the last-discovered tool | `gitpilot/mcp_client.py:215-244` |
| F4 | `mcp_server_tools` stale contracts (`SkillManager.list()`/`agentic.build_plan` don't exist) — the skills tool silently returns an empty list (`{"available": True, "skills": []}`) and the plan tool reports "no plan() entrypoint" | `gitpilot/mcp_server_tools.py:140-145,192-194` |
| F5 | `ToolPolicy.classify()` keys are snake_case but runtime tool names are CrewAI prose display names — policy would misfire even if wired | `gitpilot/tool_groups.py` vs `@tool("Read file content")` |
| F6 | `TerminalExecutor` carries a drifted copy of the sandbox denylist (missing shutdown patterns) and does not strip secrets from env | `gitpilot/terminal.py:77-100` |
| F7 | Sandbox run approval is client-side only — `/api/sandbox/run` takes no `plan_id`/approval token; the server never verifies a plan was approved | `gitpilot/sandbox_api.py`, `docs/sandbox-approval-flow.md` |
| F8 | Legacy `/ws/sessions` calls `dispatch_request` without the GitHub token | `gitpilot/_api_app.py:3815-3819` |
| F9 | Duplicate/dead topology resolution block | `gitpilot/agentic.py:2199-2207` |
| F10 | `_api_core.py` is an orphaned older snapshot of the API module | `gitpilot/_api_core.py` |
| F11 | Permission mode triple-tracked (request body, `PermissionManager` singleton, VS Code setting) with no single source of truth | §8.1 resolves |
| F12 | Event bus is in-process, no replay, ids ignored — no resume across restarts/workers | §13.3 resolves |

### 2.4 Existing low-end-model machinery (to be generalized, not discarded)

- `lean_prompts` flag: compact personas/tasks with **test-pinned character
  budgets** (`PLAN_TASK_CHAR_BUDGET=1400`, …) and a `FORBIDDEN_KEYWORDS` scrub;
  "Known facts" deliberately placed at the prompt tail because small models
  over-weight the final segment (`gitpilot/agent_prompts.py`).
- `explorer_summary.compress_exploration_report`: deterministic compression of
  exploration output to ≤800 tokens without dropping file paths (born from
  llama3:8b's 8k window).
- Lite Mode's `ACTION filepath` line protocol with regex parse, fuzzy-extraction
  fallback, and validation against the real repo file list (hallucinated paths
  silently dropped, no-actions degrades to Q&A).
- `plan_guards`: refusal detection, hallucinated-stock-plan scoring,
  `enrich_plan_with_reads` re-inserting READ steps small models drop.
- `reasoning_normalizer`: `<think>` stripping (incl. truncated-stream orphans),
  prior-message cleaning per DeepSeek guidance, empty-response fallbacks.
- `tool_def_pruner` + `GITPILOT_MCP_BRIDGE_MAX_TOOLS=32` cap.
- `_is_incompatible_model` (`agentic.py:36-64`): the de facto capability probe —
  substring tables for reasoning models and a hardcoded <7B list.
- Out-of-the-box default is `ollabridge` + `qwen2.5:1.5b` (`settings.py`) —
  **the weak-local case is GitPilot's default configuration**, which is why
  low-end compatibility is a first-class requirement of this design, not an
  afterthought.

---

## 3. Design principles (hard rules)

These go into `AGENTS.md` / contributor docs once the upgrade lands.

1. **The loop owns execution.** Tools execute one call and return one result.
   No tool spawns crews, walks plans, or continues on its own.
2. **Topology = policy, engine = mechanism.** A topology may not contain
   imperative orchestration. If a behavior can't be expressed as
   capabilities + limits + prompts + verification policy, it belongs in the
   engine or in a tool.
3. **One tool surface.** Every capability the model can invoke — built-in,
   MCP, delegation, TODO — is a registry entry with a canonical id, a JSON
   Schema, and a risk class. No side doors.
4. **Every tool call passes the PolicyEngine.** Including reads. `allow` is a
   decision, not the absence of a check.
5. **Dialects are renderings, not forks.** The native, react-text, and lite
   dialects consume the same registry, policy, context, and events. A feature
   that only works in one dialect is incomplete.
6. **Persist decisions, not deliberation.** Journal tool calls, arguments,
   results, summaries, TODO state, approvals, errors, events. Never build a
   feature that depends on storing private model chain-of-thought
   (`reasoning_normalizer` already strips it at the boundary).
7. **Additive migration.** New engine behind flags; legacy paths keep working
   until their topology is migrated and benchmarked; no flag-day rewrite.
   (Same discipline as Phase 1–4.)
8. **The UI is a subscriber.** Clients render the event stream; they never
   infer state by parsing answer text. Anything the UI needs is an event.

---

## 4. Target architecture

For contrast, the architecture that actually executes today (§2.1):

```
                 USER
                   │
                   ▼
        Router (regex classify)          ← safety libs float unwired:
                   │                       hooks · modes · ApprovalGate
   ┌───────┬───────┼────────┬─────────┐    prompt_cache · MCP bridges
   ▼       ▼       ▼        ▼         ▼    ToolPolicy · PermissionManager
one-shot  plan→execute   Lite      Streaming
specialist  crews per     planner   executor
 crews      file          (text     (plan→exec→
   │          │           protocol)  validate)
   ▼          ▼              ▼         ▼
 Result     Result         Result    Result      ← no path iterates on
                                                   tool observations
```

The target:

```
                        ┌───────────────────────────┐
                        │           USER            │
                        │  (VS Code / Web / CLI /   │
                        │   headless / MCP peer)    │
                        └────────────┬──────────────┘
                                     │
                        ┌────────────▼──────────────┐
                        │      SESSION RUNTIME      │
                        │ session.py · workspace.py │
                        │ modes · AGENTS.md · rules │
                        │ plugins · skills · slash  │
                        │ trusted_folders · flags   │
                        └────────────┬──────────────┘
                                     │ resolved: TopologyPolicy,
                                     │ ModelProfile, capability mask
                        ┌────────────▼──────────────┐
                        │       TASK ROUTER         │
                        │ topology pick (explicit > │
                        │ saved pref > classifier)  │
                        └────────────┬──────────────┘
                     ┌───────────────┼────────────────┐
                     │               │                │
        engine=agentic_loop  engine=sequential  engine=single_task
                     │        _pipeline (legacy)      (legacy)
                     │               │                │
        ┌────────────▼──────────────┐│                │
        │      AGENTIC EXECUTOR     ││   (CrewAI-based, frozen,
        │  agent/loop.py            ││    removed after Phase G)
        │                           ││
        │  ┌─────────────────────┐  ││
        │  │ ModelAdapter        │  ││
        │  │ (dialect: native /  │  ││
        │  │ react_text / lite)  │  ││
        │  └─────────┬───────────┘  ││
        │            │ AgentTurn    ││
        │  ┌─────────▼───────────┐  ││
        │  │ for call in turn:   │  ││
        │  │  PolicyEngine       │  ││
        │  │   allow/ask/deny    │  ││
        │  │  hooks.pre_tool_use │  ││
        │  │  ToolRegistry.exec  │  ││
        │  │  hooks.post_tool_use│  ││
        │  │  journal + events   │  ││
        │  └─────────┬───────────┘  ││
        │            │ observations ││
        │            └──── loop ────┘│
        └────────────┬──────────────┘
                     │
   ┌───────┬─────────┼─────────┬──────────┬─────────┐
   ▼       ▼         ▼         ▼          ▼         ▼
  fs.*  terminal.* git.*   github.*/   web.*     agent.*
   │       │         │      gitlab.*     │       todo.*
   │   SandboxMgr    │         │         │         │
   │   (off/subpr/   │         │         │     delegation →
   │    matrixlab)   │         │         │     child AgentLoop
   └───────┴─────────┴────┬────┴─────────┴─────────┘
                          │
             ┌────────────▼──────────────┐
             │  RUN JOURNAL + CHECKPOINTS│
             │  runs/<id>.jsonl +        │
             │  CheckpointStore shadow git│
             └────────────┬──────────────┘
                          │
             ┌────────────▼──────────────┐
             │   EVENT BUS (unified)     │
             │  SSE / WS → VS Code, Web  │
             └───────────────────────────┘
```

Component responsibilities:

| Component | Owns | Does not own |
|---|---|---|
| Session Runtime | identity, workspace, mode/rules/AGENTS.md resolution, plugin bootstrap, model profile resolution | execution |
| Task Router | topology selection only (explicit `topology_id` > saved preference > `classify_message`) | agent selection, planning |
| AgentLoop | the iterate-until-done contract: turn generation, tool dispatch, budget/termination, journaling, event emission | provider wire formats (ModelAdapter), authorization (PolicyEngine), tool behavior (ToolRegistry) |
| ModelAdapter | request building + response parsing per dialect; token streaming | deciding *whether* a call is allowed |
| ToolRegistry | tool contracts + execution against a per-run `ToolExecutionContext` | approval, sandboxing decisions (it *uses* the SandboxManager the context provides) |
| PolicyEngine | `authorize()`: capability mask → path policy → command classification → static rules → interactive approval | executing anything |
| Run Journal | append-only iteration log; resume | file snapshots (CheckpointStore's job) |

---

## 5. The core primitive: `AgentLoop`

New module: `gitpilot/agent/loop.py` (package `gitpilot/agent/` — see §17 for
the full layout).

### 5.1 Data contracts

```python
@dataclass
class ToolCall:
    id: str                 # provider call id, or synthesized "call_<n>" in text dialects
    tool: str               # canonical id, e.g. "fs.read"
    arguments: dict[str, Any]

@dataclass
class ToolResult:
    call_id: str
    ok: bool
    content: str            # what the model sees (dialect-rendered)
    data: dict | None       # structured payload for events/journal (diff, exit_code, paths…)
    error: str | None       # machine-readable error class if not ok
    denied: bool = False    # policy denial (distinct from execution failure)

class Finality(Enum):
    CONTINUE = "continue"              # more work expected after this turn
    FINAL = "final"                    # done now (no tool calls in native/react dialects)
    FINAL_AFTER_TOOLS = "final_after_tools"  # done IF this turn's calls apply cleanly
                                             # (LITE act-turns; re-evaluated after dispatch)

@dataclass
class AgentTurn:
    text: str               # assistant prose for the user (may be "")
    tool_calls: list[ToolCall]
    finality: Finality      # dialect-owned; FINAL_AFTER_TOOLS resolved by adapter.conclude()
    usage: TokenUsage | None
```

### 5.2 The loop algorithm

Conceptually (error handling elided; the full state machine is §5.4):

```python
async def run(self, task: AgentTask, ctx: AgentContext) -> AgentResult:
    await self.events.run_started(ctx)
    while True:
        self._enforce_budgets(ctx)                      # iterations, tool calls, tokens, wall clock

        turn = await self.adapter.generate(             # ModelAdapter: dialect-specific
            system=ctx.system_payload,                  # stable prefix (cacheable) + tail
            messages=ctx.messages,
            tools=self.registry.schemas(ctx.capabilities, ctx.model_profile),
        )
        if turn.text:
            await self.events.agent_message(turn.text)  # streamed token-by-token in native dialect

        if turn.finality is Finality.FINAL:
            ctx.journal.finish(status="completed")
            return AgentResult(answer=turn.text, status="completed", context=ctx)

        results = []
        for call in turn.tool_calls:
            decision = await self.policy.authorize(call, ctx)   # allow | ask | deny  (§8)
            ctx.journal.policy_decision(call, decision)         # journaled BEFORE execution

            if decision.verdict == "ask":
                ctx.state = State.AWAITING_APPROVAL             # persisted; §5.4
                approved = await self.approvals.request(call, decision, ctx)  # gate + events
                ctx.state = State.RUNNING
                decision = decision.approved() if approved else decision.denied_by_user()
                ctx.journal.policy_decision(call, decision)     # the resolution, also pre-execution

            if decision.verdict == "deny":
                result = ToolResult.denied_for(call, decision.reason)
            else:  # allow / approved
                if decision.requires_checkpoint:
                    ref = await self.checkpoints.create(call, ctx)   # §9.5 — loop-owned
                    ctx.journal.checkpoint_ref(call, ref)            # also pre-execution
                await self.hooks.fire(PRE_TOOL_USE, call, ctx)       # blocking hooks honored
                result = await self.registry.execute(call, ctx.tool_context)
                await self.hooks.fire(POST_TOOL_USE, call, result, ctx)

            ctx.record(call, result)                    # messages + ledger + journal tool_result
            results.append(result)
            await self.events.tool_completed(call, result)

        if turn.finality is Finality.FINAL_AFTER_TOOLS and \
                self.adapter.conclude(turn, results):   # LITE: actions applied & verified? (§6.4)
            ctx.journal.finish(status="completed")
            return AgentResult(answer=turn.text, status="completed", context=ctx)

        ctx.iteration += 1
```

The crucial properties, in priority order:

1. **The loop owns execution.** `registry.execute` runs exactly one call.
2. **Authorization precedes execution, always** — including the `auto`
   permission mode (which authorizes without prompting but still classifies,
   checkpoints, and journals) — and **every decision, including plain
   `allow`, is journaled before the call executes**, so the
   §19.3 ordering invariants are checkable and a crash between authorize and
   execute leaves a trace.
3. **The `ask` arm is loop-owned**: `authorize()` never blocks internally; the
   loop transitions to `awaiting_approval`, drives the gate, and journals the
   resolution — which is what gives §5.4's state machine an owner.
4. **Every iteration is journaled before the next provider call** (§9.3), so
   the run is resumable at iteration granularity.
5. **Termination is explicit:** `FINAL` (no tool calls in native/react
   dialects), `FINAL_AFTER_TOOLS` resolved by `adapter.conclude()` *after*
   dispatch (LITE act-turns — finality there depends on whether the actions
   actually applied, §6.4), budget exhaustion (returns
   `status="budget_exceeded"` with partial results), cancellation (cooperative
   check between tool calls *and* an `asyncio` cancellation point inside
   provider streaming — fixing today's between-phases-only cancel), or fatal
   provider error after retry.

### 5.3 Tool-dispatch sub-steps (normative order)

For each tool call: (1) resolve tool id (incl. legacy display-name aliases);
(2) validate arguments against the JSON Schema — on failure return a
`ToolResult(ok=False, error="invalid_arguments")` *to the model* with the
validation message so it can self-correct (never crash the loop); (3)
`PolicyEngine.authorize` and **journal the `policy_decision` line** (and its
approval resolution, if any); (4) pre-write checkpoint when the decision
requires it, journaling the `checkpoint_ref` (§9.5) — steps 3–4 are always
journaled *before* execution; (5) `hooks.fire(PRE_TOOL_USE)` — a blocking hook
result converts to a denial-shaped `ToolResult`; (6) execute with the per-tool
timeout; (7) truncate/summarize oversized output per model profile (§6.5); (8)
`hooks.fire(POST_TOOL_USE)`; (9) journal the `tool_result`; (10) emit
`tool_result` (and `file_write` when `data.paths_written` is present — the
event type finally gets an emitter).

### 5.4 Loop state machine

```
created ─► running ─► awaiting_approval ─► running ─► … ─► completed
              │              │                                 ▲
              │              └── denied/timeout ──► running ───┘  (denial fed to model)
              ├─► compacting ─► running                (context ceiling, §9.4)
              ├─► paused  ─► resumed ─► running        (explicit pause / crash recovery, §9.3)
              ├─► cancelled                            (user)
              └─► failed | budget_exceeded
```

`awaiting_approval` is a real state persisted in the journal: if the process
dies while waiting, resume re-emits the pending `approval_needed` event instead
of silently re-executing. (Resume-with-re-prompt lands in Phase F; until then
a restart while pending resolves as a deny — the safe direction. §8.4, §18.)

### 5.5 Defaults and limits

Per-run limits come from the topology policy (§15), with engine hard caps:

| Limit | Default | Hard cap |
|---|---|---|
| `max_iterations` | 40 | 200 |
| `max_tool_calls` | 120 | 600 |
| `max_runtime_seconds` | 1800 | 7200 |
| per-tool timeout | tool-declared (terminal: sandbox policy `timeout_sec`, default 120, clamp ≤600 — unchanged) | — |
| token budget | model-profile context window driven (§9.4) | — |

---

## 6. Model compatibility layer: one loop, three dialects

This is the section that makes "compatible with low-end and high-end LLMs" an
architectural property instead of a hope.

### 6.1 The `ModelProfile`

New module `gitpilot/agent/model_profile.py`. A resolved profile per
(provider, model):

```python
@dataclass(frozen=True)
class ModelProfile:
    provider: str                    # settings.LLMProvider value
    model: str
    dialect: Dialect                 # NATIVE | REACT_TEXT | LITE
    context_window: int              # from context_meter tables / model_catalog
    max_tool_schemas: int            # tool-count budget shown to the model
    schema_verbosity: str            # "full" | "compact" | "names_only"
    supports_parallel_tool_calls: bool
    supports_streaming: bool
    strip_reasoning_tags: bool       # reasoning_normalizer applies
    prompt_style: str                # "standard" | "lean"  (lean_prompts budgets)
    cacheable_prefix: bool           # Anthropic cache_control eligible
```

Resolution order (first match wins):

1. Explicit override in `.gitpilot/models.yaml` (new, optional — lets users pin
   a dialect for a model we've never seen).
2. Built-in table, seeded from what the code already knows:
   `_INCOMPATIBLE_MODEL_PATTERNS` (`agentic.py:36-52`) → `LITE`;
   `REASONING_MODEL_PATTERNS` (`reasoning_normalizer.py`) → `strip_reasoning_tags`;
   `context_meter.resolve_context_window` tables → `context_window`;
   provider families: anthropic/openai/watsonx chat-tool models → `NATIVE`.
3. **Runtime probe with caching** (new): on first use of an unknown
   OpenAI-compatible model, send one cheap tool-call probe (a single trivial
   tool, temperature 0). Parses as a native call → `NATIVE`; follows the ReAct
   grammar → `REACT_TEXT`; otherwise → `LITE`. Result cached in
   `~/.gitpilot/model_profiles.json` keyed `(base_url_host, model)`. Probing is
   flag-gated (`model_probe`, default on) and skippable via `models.yaml`.

`smart_model_router` stops being advisory: its tier/complexity output selects
among *configured* models where the user has provided a model map, and its
selection now actually reaches `ModelAdapter` construction (closing the
"computed then ignored" gap at `agentic.py:2231-2247`).

### 6.2 Dialect A — `NATIVE` (frontier and tool-calling models)

- Covers Anthropic, OpenAI, and every OpenAI-compatible gateway — Ollama
  (tool-capable models), **Open WebUI**, OllaBridge, and custom endpoints all
  resolve to the `openai_compat` provider client; watsonx gets its own client.
- Request: provider-native tool definitions (Anthropic `tools=[…]` /
  OpenAI-compatible `tools=[{"type":"function",…}]`), built directly from
  registry JSON Schemas. System prompt assembled by
  `prompt_cache.build_system_blocks` — its `cache_control: ephemeral` markers
  finally reach a real Anthropic call; the tool-def sha256 digest it already
  computes becomes the cache-busting key when the capability mask changes.
- Response: native `tool_use` / `tool_calls` blocks parsed into `ToolCall`s;
  results returned as native tool-result messages. Parallel calls honored when
  `supports_parallel_tool_calls`.
- Streaming: true token streaming (`stream: true`; assemble tool-call deltas) —
  this replaces the 80-char fake chunking. The dormant TS loop's delta-assembly
  logic is the reference implementation.
- Transport: thin per-provider clients in `gitpilot/agent/providers/`
  (`anthropic.py`, `openai_compat.py`, `watsonx.py`) grown from
  `direct_chat.resolve_endpoint`'s provider mapping and
  `inference/OpenAICompatibleClient`'s defensive parsing. **No CrewAI, no
  litellm import in the loop path** — killing the 10–60s import tax and the
  env-var contamination hazards documented in `llm_provider.py`.

### 6.3 Dialect B — `REACT_TEXT` (capable local models, ~7B–70B, no reliable native tool API)

- Request: no `tools` array. The system prompt carries a compact tool manual
  rendered from the same registry schemas (verbosity per profile: full JSON
  Schema for 30B+, compact `name(arg: type, …) — one-line description` for
  smaller), plus the grammar:

  ```
  Thought: <brief reasoning about the next step>
  Action: <tool id, e.g. fs.read>
  Action Input: <single JSON object>
  ```

  followed by `Final Answer:` for completion. This is intentionally the
  grammar CrewAI trained the ecosystem on — local models have seen it — but
  **parsed by us**, in `gitpilot/agent/dialects/react_text.py`, not inside a
  framework we can't instrument.
- Parsing: tolerant, in this order — strip reasoning tags
  (`reasoning_normalizer`), extract fenced or bare JSON for Action Input
  (reusing `_strip_markdown_fences`, `agentic.py:550`), fuzzy-match tool ids
  (case/underscore/hyphen-insensitive; unknown tool → corrective `ToolResult`
  listing valid ids, never a crash), single-quoted-JSON repair. Malformed turns
  get **one** reformat retry with an error-specific instruction before the turn
  is treated as prose.
- One tool call per turn (serial), matching what these models can do reliably.
- Finality: `FINAL` on presence of `Final Answer:` or a parseable absence of
  any Action after the retry; otherwise `CONTINUE`.

### 6.4 Dialect C — `LITE` (sub-7B models; also the universal fallback)

Lite Mode stops being a separate topology-plus-planner universe and becomes the
smallest dialect of the same loop:

- **Micro-loop, not zero-loop.** The engine still iterates, but each iteration
  is a *constrained, task-shaped* exchange with pre-fetched context — the
  pattern `generate_plan_lite` already proved. The dialect adapter (not the
  model) chooses the iteration template:
  - *investigate*: model asks for files by emitting `READ path` lines (≤3 per
    turn); adapter validates against the file list, injects contents, repeats
    (bounded, default 3 investigate rounds).
  - *act*: model emits the existing `ACTION filepath` protocol (`CREATE` /
    `MODIFY` / `DELETE`) followed by fenced content blocks; adapter parses with
    the existing regex + fuzzy repair + file-list validation.
  - *answer*: plain text = final.
- All existing Lite hardening is retained and relocated into the dialect:
  intent regex, context prefetch (from the workspace or the GitHub tree),
  `plan_guards` refusal/hallucination checks, path validation, lean prompt
  budgets with `FORBIDDEN_KEYWORDS`, tail-positioned known-facts.
- **Policy applies to every path the model touches — reads included.** Parsed
  `ACTION` lines become synthetic `fs.write`/`fs.delete` `ToolCall`s; parsed
  `READ` lines *and every prefetched file* become synthetic `fs.read`
  `ToolCall`s. All of them pass `PolicyEngine.authorize` (so `blocked_paths`
  keeps `.env`/keys out of a small model's context exactly as it does for a
  frontier model) and all of them are journaled as tool calls + results — which
  is also what makes a LITE run resumable, since replay reconstructs the
  injected file contents from the journal. No file content enters
  `ctx.messages` without a journaled `fs.read` (§19.3). Today's Lite Mode
  bypasses the entire safety stack; after this change it cannot.
- Finality: an *answer* turn is `FINAL`; an *act* turn is `FINAL_AFTER_TOOLS` —
  the adapter's `conclude()` confirms the parsed actions actually applied
  (approvals may have denied some) and verification ran where required, before
  the loop returns (§5.2).
- Verification in LITE: the model never emits `test.run` itself — when the
  topology sets `verification: auto|required` and tests exist, the **engine**
  issues `test.run` (through policy, journaled like any call) in the verify
  phase and feeds a compact pass/fail summary into the next micro-turn. This
  keeps rule 5 honest: verification works in every dialect, differing only in
  who initiates it.
- T8 `lite_mode` the topology survives as "force the LITE dialect + tight
  limits" (§15.4) for users who want it explicitly; automatic selection is the
  profile's job.

### 6.5 Small-context survival kit (applies to all dialects, scaled by profile)

- **Tool-schema budget:** `registry.schemas()` takes the profile; beyond
  `max_tool_schemas` it prunes by (topology priority, recency-of-use,
  `tool_def_pruner` policy verdicts) and logs a `tools_pruned` event — never a
  silent cap (the `GITPILOT_MCP_BRIDGE_MAX_TOOLS=32` cap generalizes here).
- **Observation truncation:** tool output above a profile-scaled byte budget is
  head/tail-truncated with an inline `… [N bytes omitted — use fs.read with
  offset/limit]` marker; full output always goes to the journal, never to the
  small model.
- **Context ceiling → in-loop compaction** (§9.4) rather than today's
  endpoint-entry-only compaction.
- **Exploration compression:** `explorer_summary.compress_exploration_report`
  becomes the standard compressor for delegation returns (§11.3).
- **Prompt style:** `lean` profiles route all engine-authored text through the
  `agent_prompts` budgeted templates.

### 6.6 Degradation ladder

If a `NATIVE`-profiled model returns malformed tool calls twice in a run, the
adapter downgrades the *run* to `REACT_TEXT` (event: `dialect_downgraded`,
journaled). If `REACT_TEXT` parsing fails past its retry budget, the run
degrades to `LITE` templates. Upgrades never happen mid-run.

Scope honestly stated: NATIVE→REACT_TEXT preserves the full tool surface — a
misdetected model gets a slower-but-complete session. The last rung is a
**capability reduction**, not just a slowdown: LITE exposes only
`lite_compatible` tools, so terminal- or delegation-heavy work in flight
cannot continue there. On a downgrade to LITE the engine therefore (a) marks
non-lite-compatible pending TODO items `blocked(dialect)`, (b) completes what
the LITE surface can (fs-shaped work, engine-driven verification per §6.4),
and (c) finishes with an explicit `status="degraded"` result that says which
steps were skipped and why — never a silent partial success. The guarantee is:
*every* model produces either a completed run or an honest degraded/failed
one; it is never a hallucinated-path apply or a hung loop.

---

## 7. The canonical Tool API

New package `gitpilot/toolkit/` (name avoids colliding with the existing
`agent_tools.py` during migration).

### 7.1 Contracts

```python
@dataclass(frozen=True)
class ToolSpec:
    id: str                        # "fs.read" — namespaced, snake_case segments
    title: str                     # "Read file" — UI display
    description: str               # model-facing, imperative, ≤2 sentences
    params_schema: dict            # JSON Schema (draft 2020-12)
    capability: str                # capability key, e.g. "fs.read" (§8.2)
    risk: Risk                     # SAFE | APPROVAL | HIGH_RISK
    effects: frozenset[Effect]     # READS_FS, WRITES_FS, EXECUTES, NETWORK, GIT_LOCAL, GIT_REMOTE, FORGE_WRITE
    timeout_s: int | None
    lite_compatible: bool          # renderable in the LITE dialect

class ToolRegistry:
    def register(self, spec: ToolSpec, handler: ToolHandler) -> None: ...
    def schemas(self, capabilities: CapabilityMask, profile: ModelProfile) -> list[dict]: ...
    async def execute(self, call: ToolCall, ctx: ToolExecutionContext) -> ToolResult: ...
```

`ToolExecutionContext` replaces the module-global `_current_repo_context` /
`_current_workspace` pattern: it carries the workspace root (or GitHub repo
binding + token), the `SandboxManager`, the session id, and the event emitter.
Handlers are `async`; sync legacy implementations are wrapped with
`asyncio.to_thread` — ending the per-call `asyncio.new_event_loop()` churn in
`agent_tools.py`.

### 7.2 Namespaces (initial surface)

```
fs.read        fs.write       fs.edit        fs.delete
fs.list        fs.glob        fs.grep

terminal.run   terminal.which terminal.env          (all sandbox-routed)

git.status     git.diff       git.log        git.branch
git.commit     git.push       git.stash

github.search  github.issue.get/create/comment
github.pr.get/create/comment   gitlab.* (later, same shapes)

test.detect    test.run

web.search     web.fetch

todo.write                    (single read-modify-write tool, §10)

agent.delegate                (§11)

mcp.<server>.<tool>           (dynamic, §14.6)
```

Deliberately deferred: a `docker.*` namespace (`docker.ps/logs/exec/compose`).
Until it lands (Phase H, if dogfooding shows demand), docker operations flow
through `terminal.run` and get their own command-classification rows
(`docker ps/logs` → READ_ONLY; `docker run/exec/compose up` → MUTATING;
`docker system prune`, `rm -f` → DESTRUCTIVE) — the semantics arrive before
the namespace does.

### 7.3 Mapping from the existing implementations

The registry wraps what exists; behavior-bearing code is reused, the CrewAI
`@tool` prose-name wrappers are what retires.

| Canonical id | Backing implementation | Notes |
|---|---|---|
| `fs.read/list/glob` | `workspace.py` file ops (local) / `agent_tools.read_file` (backed by `github_api.get_file`) + tree (GitHub mode) | one id, two context-selected backends |
| `fs.grep` | `grep_backend.grep_local` (ripgrep fast path — finally gets a caller) / `grep_repository` remote path with its `FILE_FETCH_CAP=200` | |
| `fs.edit` | `edit_backend.apply_edit` (exact-string, `expected_occurrences` contract) — schema exposes `old_string/new_string/expected_occurrences` | the strict contract is a feature for models: mismatch messages teach self-correction |
| `fs.write/delete` | workspace write / GitHub contents API put/delete | GitHub mode keeps commit-per-write semantics |
| `terminal.run` | `local_tools._run_via_sandbox` → `sandbox.py` backends (off/subprocess/matrixlab), `SandboxPolicy` env-scrub + cwd jail | the legacy `TerminalExecutor` fallback is removed from the tool path (F6); it survives only inside the validation harness until Phase D |
| `git.*` | `workspace.py` (status/diff/log/branch/commit/push porcelain wrappers) | `git.push` is `HIGH_RISK` |
| `github.*` | `github_issues.py` / `github_pulls.py` / `github_search.py` | |
| `test.detect/run` | `test_detection.py` + sandbox-routed execution with `_parse_test_counts` (§12) | |
| `web.search/fetch` | existing search tools | absent providers ⇒ tool absent from registry, not erroring |
| `todo.write` | new, trivial (§10) | |
| `agent.delegate` | new (§11) | |

A **compat alias table** maps legacy CrewAI display names and the VS Code tool
ids (both currently mixed in `approval_protocol.DANGEROUS_TOOLS`) to canonical
ids, so events, saved sessions, and the extension keep working during
migration.

### 7.4 Result rendering

`ToolResult.content` is dialect-rendered by the adapter (native: tool-result
block; react/lite: fenced observation). `ToolResult.data` feeds events and the
journal — e.g. `fs.edit` returns `data={"path", "replacements", "diff"}` so the
UI can show a real diff without re-parsing prose, and `file_write`/`tool_result`
events stop being vestigial. Errors follow the existing convention (stringified,
never raised into the model) but gain a machine-readable `error` class.

---

## 8. The PolicyEngine

New module `gitpilot/agent/policy.py`. One pipeline, invoked by the loop for
**every** tool call:

```
ToolCall
  │ 1. capability mask        (topology ∩ mode ∩ session)      → deny if absent
  │ 2. path policy            (blocked_paths fnmatch; edit-guard fileRegex)
  │ 3. command classification (terminal.run / git.* only)       → risk elevation
  │ 4. static rules           (denylist patterns; plan-mode read-only)
  │ 5. decision               allow | ask | deny  (+ reason, + risk label)
  │ 6. ask → ApprovalGate     (Allow / Allow-for-session / Deny; loop-driven, §5.2)
  ▼
Decision {verdict, reason, risk, requires_checkpoint}   # checkpoint fired by the loop, §9.5
```

### 8.1 Single source of truth for permission state

`PermissionMode` (`normal`/`plan`/`auto`) lives in **one** place: the session
record (persisted). Escalation is asymmetric by design: switching a session to
`auto` happens **only** through the explicit, authenticated user action
(`PUT /api/permissions/mode` or the VS Code toggle that calls it). The
per-request `permission_mode` field survives but may only *restrict* for that
request (`auto` session + `plan` request = plan; `normal` session + `auto`
request = **normal** — the body of a chat request is never an escalation
channel, otherwise the F7 fix would be reopened by this one).
`permissions.PermissionPolicy` (blocked paths, allowed commands, per-action
confirmation) becomes the file/API representation the PolicyEngine loads —
`PermissionManager.check()` finally has a caller, because the PolicyEngine
*is* its caller. This resolves F11.

### 8.2 Capabilities, not agent names

A `CapabilityMask` is a set of granted capability keys with optional
qualifiers, computed at session start as
`topology.capabilities ∩ mode.tool_policy ∩ user/session grants`:

```yaml
capabilities:
  fs.read: true
  fs.grep: true
  fs.write: { paths: "src/**", exclude: ["**/*.lock"] }   # edit-guard regex, from modes.yaml groups
  terminal.run: { network: false }
  git.commit: true
  git.push: false
  github.pr.create: ask                                   # value "ask" forces approval regardless of risk
  agent.delegate: { max_depth: 1 }
```

`tool_groups.ToolPolicy` maps onto this directly (categories → key prefixes;
`edit_guard.file_regex` → `fs.write/edit` qualifiers; MCP guards →
`mcp.*` keys), fixing F5 as a side effect because canonical ids *are*
snake_case.

### 8.3 Command classification (semantic, not name-based)

New `gitpilot/agent/command_class.py`, used for `terminal.run` (and `git.*`
argument inspection):

| Class | Examples | Default verdict (normal mode) |
|---|---|---|
| `READ_ONLY` | ls, cat, grep, find, git status/diff/log, which | allow |
| `TEST` | pytest, npm test, go test, cargo test, make test | allow |
| `BUILD` | make, npm run build, tsc, cargo build | allow |
| `MUTATING` | pip/npm/cargo install, mkdir, mv, sed -i | ask |
| `GIT_MUTATION` | git add/commit/checkout -b/stash | ask |
| `REMOTE_MUTATION` | git push, gh pr create, npm publish | ask (HIGH_RISK label) |
| `NETWORK` | curl, wget, pip download | ask; deny when sandbox `allow_network=false` (consistency with the sandbox, not a prompt) |
| `DESTRUCTIVE` | rm -rf, mkfs, dd of=/dev/*, fork bombs, shutdown | deny (no prompt) |
| `PRIVILEGED` | sudo, su, chown root | deny |

Implementation: argv-level parsing of the first command in each pipeline
segment (shlex + `&&`/`;`/`|` splitting), a curated classification table, and a
conservative default (`MUTATING` → ask) for unknown commands. The existing
substring denylists (`sandbox.BLOCKED_PATTERNS` + the drifted `terminal.py`
copy) collapse into the single `DESTRUCTIVE` table — fixing F6's drift by
deleting the duplicate. Classification is best-effort and **advisory upward
only**: it can escalate (ask→deny) but never de-escalate below the tool's
declared risk class, so a clever command string can't argue its way down; the
sandbox jail remains the real containment layer.

Approval prompts show the classification ("This command installs packages
(MUTATING)"), which is the "operate semantically rather than by tool name"
requirement.

### 8.4 Interactive approvals — reusing and repairing `ApprovalGate`

The gate's design (async future per request, `normal/auto/plan`, session-scope
allowlist, 120s timeout-deny) is right; it keeps its role as the `ask`
executor. Repairs:

- **F2:** the loop's `ask` arm drives the gate (§5.2) — its first real caller.
- **F1:** approvals become resolvable transport-independently: pending
  approvals register in a per-session `ApprovalRegistry`;
  `POST /api/v2/approval/respond` resolves through the registry (the WS path
  keeps calling `gate.resolve` directly). Pending state is journaled (§5.4);
  once `awaiting_approval` persistence lands (Phase F), a restart re-prompts
  instead of hanging or silently executing — until then a restart while
  pending simply denies, which is the safe direction.
- **Checkpointing moves out of the gate.** The gate's `on_checkpoint` hook is
  retired; the loop fires the checkpoint from `Decision.requires_checkpoint`
  (§9.5) — one owner, no double-snapshot on the `ask` path.
- `DANGEROUS_TOOLS` (hardcoded frozenset of mixed naming schemes) is deleted;
  risk now comes from `ToolSpec.risk` + command classification + capability
  qualifiers.
- Timeout behavior stays deny-by-default; the timeout becomes configurable per
  topology (headless runs set `approval.mode: none` and simply receive denials).
- **F7:** `/api/sandbox/run` gains an `approval_token` minted when an
  `ExecutionPlan` is approved, or when the **session-persisted** mode is
  `auto` (never from a request-body mode claim — §8.1); the ExecutionPlan card
  UX is unchanged, the server just stops trusting the client.

### 8.5 Sandbox is policy's enforcement arm for execution

`terminal.run` and `test.run` route exclusively through `sandbox.py`
(`off`/`subprocess`/`matrixlab`, `SandboxPolicy` workspace jail, secret-env
strip, output caps). The PolicyEngine consults sandbox settings for the
NETWORK class, and the approval card keeps showing sandbox facts (workspace,
network on/off, timeout) as today's `ExecutionPlan` checks do.

---

## 9. `AgentContext`, persistence, and resume

### 9.1 `AgentContext` (in-memory)

`gitpilot/agent/context.py`:

```python
class AgentContext:
    run_id: str; session_id: str; task: AgentTask
    topology_id: str; policy: ResolvedPolicy; model_profile: ModelProfile
    capabilities: CapabilityMask           # resolved topology ∩ mode ∩ session (§8.2)
    workspace: WorkspaceBinding            # local root | GitHub repo binding
    tool_context: ToolExecutionContext     # what ToolRegistry.execute receives (§7.1)
    system_payload: SystemPayload          # prompt_cache blocks (stable prefix + tail)
    messages: list[Message]                # provider-shaped, dialect-owned
    ledger: ToolLedger                     # every call+result, structured
    files_read: set[str]; files_modified: set[str]
    commands: list[CommandRecord]; tests: list[TestRecord]
    todos: TodoList
    errors: list[ErrorRecord]
    state: State                           # §5.4 machine
    iteration: int; budgets: BudgetState
    journal: RunJournal
```

### 9.2 What is persisted (and what is not)

Journaled per §3 rule 6: tool calls + arguments, tool results (full, even when
truncated for the model), assistant *user-visible* text, TODO transitions,
**every authorization decision — `allow` included, not just approvals and
denials** (the §19.3 invariants are ordering checks over these lines),
approvals (request, decision, scope), errors, compaction summaries, dialect
changes, budget snapshots. **Not** journaled: reasoning-tag content (stripped
at the adapter boundary), raw provider wire payloads.

### 9.3 `RunJournal` — append-only JSONL

`~/.gitpilot/sessions/<session_id>/runs/<run_id>.jsonl` — one line per event
(`run_started`, `turn`, `tool_call`, `policy_decision`, `checkpoint_ref`,
`tool_result`, `approval`, `todo`, `compaction`, `state_change`), each with a
monotonically increasing `seq`. Written with line-level fsync-on-state-change
(cheap; runs are chatty but small). Two contracts that make replay real:

- **`run_started` embeds the seed**: the initial message set (user task plus a
  session-transcript reference + message index for any prior conversation
  context) — the one thing replay cannot derive from later lines. This is also
  what makes the Phase C "byte-stable replay" criterion testable.
- **Journal → event projection is defined per line type** (a `turn` line
  projects to the `text_delta` stream's final text, `tool_call` +
  `policy_decision` project to `tool_start`/`approval_needed`, `tool_result`
  to `tool_result`/`file_write`/`terminal_output`, etc.). Ephemeral wire
  events (in-flight `text_delta` chunks, keepalives) intentionally have no
  journal representation — on replay they are reconstructed as their
  state-bearing form (the journaled turn text), not verbatim.

**Resume:** `AgentRunner.resume(run_id)` replays the journal into a fresh
`AgentContext` (re-rendering messages for the *current* dialect — which is what
makes resume survive even a model/dialect change; LITE-injected file contents
replay from their journaled `fs.read` results, §6.4), restores
TODO/ledger/budget state, re-arms a pending approval if the last state was
`awaiting_approval`, and continues the loop. Exposed as
`POST /api/v2/agent/resume {run_id}` and surfaced in the session UI for any
run whose journal ends without a terminal state. This finally implements what
the `checkpoints.py` docstring promised ("re-emits the saved transcript so the
conversation can be resumed deterministically") but never had a code path for.

### 9.4 Context management inside the loop

At each iteration start, if projected tokens exceed
`0.70 × (context_window − reserve)` (same thresholds as `auto_compact`), the
loop enters `compacting`: oversized tool observations stub first, then older
turns fold into a pinned summary — i.e. `ContextBudgetManager`'s existing
policy (`condense_at_ratio=0.70`, `keep_recent_turns=6`,
`large_tool_output_tokens=4000`) finally wired, with the ledger/journal keeping
the un-compacted truth. The endpoint-level `auto_compact` hook stays for
legacy paths until they retire.

### 9.5 Checkpoints

Unchanged storage (`CheckpointStore` shadow git + transcript + tool descriptor;
`SessionManager.create_checkpoint/rewind_to_checkpoint`). Changes in *when* and
*what*:

- The pre-mutating-write checkpoint fires **from the loop**, on
  `Decision.requires_checkpoint` — the single owner across all permission
  modes (the gate's `on_checkpoint` hook is retired, §8.4, so the `ask` path
  cannot double-snapshot) — and now actually fires on every path because
  every path is the loop (fixes the F2 consequence).
- `ToolCallDescriptor.arguments` gets populated (today it never is on the auto
  path).
- Checkpoint records gain `run_id` + journal `seq`, and rewind gains an
  optional "also truncate the run journal to this seq" so file-state and
  run-state rewind together.
- `CheckpointStore.prune(keep_last=50)` gets a production caller (post-run,
  best-effort).

---

## 10. First-class TODO state

A single tool, mirroring the pattern proven by Claude Code:

- `todo.write` — the model submits the full list
  `[{id, text, status: pending|in_progress|completed}]`; the engine diffs
  against current state, journals transitions, and emits `todo_updated` (new
  event type).
- TODO is **live state, not the plan**: the system prompt (all dialects)
  instructs multi-step tasks to maintain it, mark items `in_progress` before
  starting and `completed` immediately after, and *rewrite it when
  understanding changes*. The engine never enforces plan-shaped execution
  against it — that's the "agent must be allowed to change its mind"
  principle; rigid enforcement would rebuild the planner we're retiring.
- In the LITE dialect, `todo.write` is not exposed as a tool; the *engine*
  maintains a coarse TODO from the micro-loop phase (investigate → act →
  verify) so the UI renders the same experience for small models.
- Rendering: the VS Code/webview checklist (`✓ / → / ○`) subscribes to
  `todo_updated`; the existing `plan_step` events remain for legacy pipelines
  until they retire.

The old world's `PlanResult` does not disappear: `architect_mode` (T6) and the
plan-approval UX still produce a reviewable plan artifact — but as the
*output of a read-only agentic run*, not as a mandatory pipeline stage. The
planner becomes a capability ("produce a plan document"), not an execution
phase.

---

## 11. Delegation and subagents

### 11.1 The `agent.delegate` tool

```json
{ "agent": "explorer",
  "task": "Map how checkpoint storage works; list key files and entry points",
  "expected": "summary + file list",
  "max_iterations": 10 }
```

Spawns a **child `AgentLoop`** with: the named subagent's prompt profile, a
capability mask = (parent mask ∩ subagent template mask) — a child can never
exceed its parent; `max_depth` enforced from the capability qualifier (default
1); the parent's model profile or the subagent's cheaper override (explorer
defaults to the fast tier from `smart_model_router`'s map); its own journal
under the parent's run (`runs/<run>/sub/<n>.jsonl`); events tagged
`parent_call_id` so the UI nests them.

### 11.2 Bounded contract

Subagents return a **structured result, not their transcript**:

```json
{ "summary": "...", "files": ["gitpilot/checkpoints.py", "…"],
  "findings": ["…"], "recommendations": ["…"], "status": "completed" }
```

The engine enforces the shape (schema per subagent template); an over-budget or
failed child returns a partial result with `status`. The parent sees only this
object — context isolation is the point.

### 11.3 Compression

Child results above the parent's observation budget pass through
`explorer_summary.compress_exploration_report`-style deterministic compression
(path-preserving, budgeted) before entering the parent context. Full child
output lives in the child journal.

### 11.4 Built-in subagent templates and parallelism

Templates (prompt + mask + return schema) replace the T2 flow-graph fictions:
`explorer` (read-only fs/*, grep), `reviewer` (read-only + `git.diff`),
`researcher` (web.*), `test_analyst` (test.*, fs.read). They live in the
topology/policy registry, so custom topologies can define their own.

Parallel delegation (multiple children in worktrees via
`agent_teams.setup_worktrees` + `execute_parallel(executor_fn=child_loop)`) is
deferred to the last phase: the scaffold's task splitting and merge are not
production-grade (generic "Part N" split; overlap-only conflict detection), and
serial delegation covers the Claude-Code-parity experience. When it lands, the
child loop *is* the `executor_fn` — the seam was built for exactly this.

---

## 12. The verification loop

- `test.detect` wraps `test_detection.detect_test_command` (marker files →
  pyproject `[tool.pytest]` → package.json scripts) returning
  `{framework, commands[]}` — the model stops guessing how to test.
- `test.run {target?}` executes through the sandbox with the test-aware
  timeout, parses pass/fail/skip via the existing `_parse_test_counts`, emits
  `test_result` events, records to `ctx.tests`.
- **Prompted, then enforced:** the system prompt (implementation-capable
  topologies) mandates implement → test → diagnose → fix → re-test. The
  topology policy adds enforcement:

  ```yaml
  verification:
    tests: auto        # off | auto (run when tests exist) | required
    max_fix_cycles: 3
  ```

  With `required`, the engine refuses to accept a `FINAL` turn after file
  mutations until a `test.run` has occurred in the run (or `max_fix_cycles`
  exhausted → the result is marked `unverified` and says so). In the LITE
  dialect the engine issues `test.run` itself during the verify phase (§6.4),
  so the policy is enforceable in every dialect. The current post-hoc
  `_run_validation` phase (lint + tests outside the model's view) retires once
  its topologies migrate — verification the model can *see and react to* is
  the entire point of the loop.

---

## 13. Events and observability

### 13.1 One vocabulary

`agent_events.EventType` stays canonical and gains: `run_started`,
`run_resumed`, `iteration`, `todo_updated`, `agent_delegated`,
`agent_delegate_done`, `dialect_downgraded`, `tools_pruned`, `compaction`,
`checkpoint_created`. The never-emitted `TOOL_RESULT` and `FILE_WRITE` get
emitters (§5.3). `plan_step` remains for legacy pipelines only.

### 13.2 One transport stack

`streaming.py`'s superior framing (named `event:` lines, heartbeats,
back-pressure, `StreamMetrics`, disconnect cancellation) merges into the
`AgentEventBus` SSE writer; `register_stream_routes`'s separate vocabulary
retires. The legacy `/ws/sessions` protocol is frozen (still served, marked
deprecated per `docs/API_STABILITY.md` process) — the web app moves to the v2
stream. Three vocabularies become one.

### 13.3 Replay instead of hoping

Because the journal records every state-bearing event with `seq`, SSE
reconnects send `Last-Event-ID: <seq>` and the server replays the journal → 
event projection (§9.3) for lines > seq before attaching to the live bus —
fixing F12 without inventing a second durability mechanism. The guarantee is
**no state-bearing event lost**: in-flight `text_delta` chunks are replaced on
reconnect by the journaled turn text, keepalives are not replayed.
Multi-worker deployments get correctness (journal is the truth), not yet
fan-out scale (a shared bus is out of scope here).

### 13.4 Real streaming

`text_delta` becomes true provider token streaming in the NATIVE dialect
(§6.2); REACT_TEXT streams per-line as the grammar permits; LITE emits
per-phase. The 80-char fake chunker is deleted.

---

## 14. Wiring the parity libraries

Each of these is a small, bounded change *because the library already exists*.

1. **Hooks** (§5.3): `fire(PRE_TOOL_USE/POST_TOOL_USE)` around dispatch;
   `SESSION_START/END`, `USER_MESSAGE` at the session runtime;
   `PRE/POST_EDIT` from `fs.edit/write`; `PRE/POST_COMMIT`, `PRE_PUSH` from
   `git.*`. Blocking verdicts become denial-shaped results. Hook env-var
   context passing is already implemented.
2. **Modes**: session bootstrap calls `activate_mode`; the returned
   `system_prompt_block` joins the system payload, `tool_policy` intersects
   the capability mask, `mcp_server_configs` start/stop with the mode.
3. **Skills & slash commands**: chat ingestion runs
   `SlashCommandRegistry.parse_invocation` (server-side at last; the VS Code
   hardcoded TS list becomes a fallback) and `skills.find_auto_triggers`;
   rendered prompts are injected as the task text. `required_tools` gets
   honored as a capability pre-check.
4. **AGENTS.md / rules / prompt cache**: the system payload is
   `prompt_cache.build_system_blocks(base_system, agents_md, rules, tool_defs,
   session_tail)` — cache markers live on the native-Anthropic path, the
   digest busts on capability-mask change, and the assembly is shared by all
   dialects (dialects differ in the *tool manual* block only).
5. **Plugins**: session bootstrap pipes `load_all_skills/hooks/mcp_configs`
   into the three managers. Install → restart session → active.
6. **MCP**: one transport — `mcp_client.MCPClient` (JSON-RPC; F3 closure bug
   fixed) — feeding `MCPToolDescriptor`s into the registry as
   `mcp.<server>.<tool>` with their **real `inputSchema`** (already captured,
   currently discarded), `classify_risk` mapping to `ToolSpec.risk`
   (`mutation` → APPROVAL), admin toggles and `tool_overrides` respected, and
   `tool_def_pruner` applied via the profile's schema budget. The bespoke
   HTTP-POST bridge in `mcp_tools_bridge` retires; its store/toggle/risk layer
   survives. F4's stale server-side contracts get fixed the same phase.

---

## 15. Topology schema v2

### 15.1 Schema

Topologies become declarative policy documents. Built-ins live in
`gitpilot/topology/defaults/*.yaml`; users add `.gitpilot/topologies.yaml`
(same loader discipline as `modes.yaml`, including the tiny-YAML fallback).

```yaml
id: default
name: Autonomous Engineer
icon: "🧠"
category: system

execution:
  engine: agentic_loop          # agentic_loop | sequential_pipeline | single_task
  dialect: auto                 # auto (per ModelProfile) | native | react_text | lite

agent:
  role: software_engineer       # prompt profile key (agent_prompts)
  subagents: [explorer, reviewer, researcher, test_analyst]

capabilities:
  fs.read: true
  fs.glob: true
  fs.grep: true
  fs.write: true
  fs.edit: true
  terminal.run: { network: false }
  git.read: true
  git.commit: ask
  git.push: false
  github.read: true
  github.pr.create: ask
  web.search: true
  web.fetch: true
  todo.write: true
  agent.delegate: { max_depth: 1 }

approval:
  mode: session_default         # session_default | normal | auto | plan | none(headless)
                                # may only TIGHTEN relative to the session mode: a topology
                                # can force plan/normal, but "auto"/"none" here never overrides
                                # a session in normal mode. Repo-local topology files
                                # (.gitpilot/topologies.yaml) additionally sit behind the
                                # trusted_folders gate — an untrusted clone cannot ship a
                                # topology that weakens approvals.

verification:
  tests: auto
  max_fix_cycles: 3

limits:
  max_iterations: 40
  max_tool_calls: 120
  max_runtime_seconds: 1800

visualization: auto             # generate flow graph from this document; or embed a flow_graph
```

The dataclass `Topology` gains `policy: TopologyPolicy` and keeps
`flow_graph` (auto-generated for v2 topologies: main agent node + one node per
capability namespace + subagent nodes — so the picker and Agent Workflow view
keep working without hand-drawn graphs).

### 15.2 Routing

Unchanged surface: explicit `topology_id` > saved preference >
`classify_message` keyword scoring. The dead duplicate resolution block (F9)
is deleted in passing. `classifier_hints` remain in the schema.

### 15.3 Migration of T1–T9

| Topology | Today | Becomes |
|---|---|---|
| T1 `default` | classify_and_dispatch → one-shot specialist crews | **`agentic_loop`** with the full-safe capability set above. The ten specialist agents stop being routing targets; their useful prompts fold into subagent templates and prompt profiles. |
| T2 `gitpilot_code` | visualization-only ReAct promise | **Merged into `default`** — it *is* the default now. Id retained as an alias for saved preferences; picker hides the duplicate. |
| T3 `feature_builder` | 5-agent CrewAI pipeline | `agentic_loop` policy: full write caps, `github.pr.create: ask`, `verification.tests: required`, reviewer-subagent pass encouraged in the role prompt. |
| T4 `bug_hunter` | 4-agent pipeline | `agentic_loop`: fs+terminal+git.read, `git.commit: forbidden`→`ask` per team choice, `verification.tests: required`, role prompt = reproduce→diagnose→fix→re-test. |
| T5 `code_inspector` | 2-agent read-only pipeline | `agentic_loop`, read-only mask (`fs.read/glob/grep`, `git.read`, `terminal.run: {classes: [READ_ONLY, TEST]}`), `approval.mode: plan`. |
| T6 `architect_mode` | explore→plan pipeline | `agentic_loop`, read-only mask, deliverable = plan document (§10); plan-approval card unchanged. |
| T7 `quick_fix` | edit→commit pipeline | `agentic_loop`, tight limits (`max_iterations: 12`), `git.commit: ask`, no delegation. |
| T8 `lite_mode` | separate planner universe | `agentic_loop` with `dialect: lite` forced + tight limits (§6.4). Auto-activation moves to ModelProfile; the topology remains as the explicit override. |
| T9 `tool_augmented_react` | flow-graph sketch | **Becomes the Phase C pilot** of the real engine (experimental card, opt-in), then retires into `default` when the default flips. |

Legacy engines (`crew_pipeline` as `sequential_pipeline`, `single_task`) keep
running T3–T7/T1 until each migrates (Phase G), then the engines and their
CrewAI dependency path are removed from the default install.

### 15.4 The default flips

`default` switches to the agentic loop only at Phase G, behind the
`agent_loop_default` flag, after the benchmark gate (§18, Phase G exit
criteria). Users can pin the legacy behavior via a `classic` topology alias for
one release cycle.

---

## 16. Client experience

### 16.1 VS Code (primary)

The webview becomes an agent console fed entirely by events (it already
renders most of these — the server finally sends them):

```
┌────────────────────────────────────────────┐
│ Analyze the test architecture              │
│                                            │
│ ✓ fs.read      Makefile                    │
│ ✓ fs.glob      scripts/**                  │
│ ✓ terminal.run pytest --collect-only  (2s) │
│ ✓ fs.read      docker-compose.dev.yml      │
│                                            │
│ TODO                                       │
│ ✓ Project structure                        │
│ → Inspect checkpoint fixtures              │
│ ○ Run checkpoint tests                     │
│                                            │
│ ▸ delegate: explorer — "map fixtures" (12 calls) │
└────────────────────────────────────────────┘

┌────────────────────────────────────────────┐
│ APPROVAL REQUIRED             risk: MEDIUM │
│ terminal.run  ·  MUTATING (installs pkgs)  │
│ $ pip install -e ".[dev]"                  │
│ Workspace: gitpilot   Network: disabled    │
│ [Allow] [Allow for session] [Deny]         │
└────────────────────────────────────────────┘
```

Changes: approval respond works over SSE (F1); `CANCEL_TASK` calls
`POST /api/v2/agent/cancel` (today it only aborts the local fetch); plan
approval invokes `/api/chat/execute` with the stored plan object instead of
re-sending prose; a "Resume run" affordance appears for interrupted runs.

### 16.2 Web app

Moves from the legacy `/ws/sessions` protocol to the v2 stream (the unused
`frontend/utils/sse.js` finally earns its keep). Topology picker, flow view,
ExecutionPlan cards unchanged.

### 16.3 The dormant TS loop

`extensions/vscode/src/local/` is retired (kept one release as a
`gitpilot.localLoop.enabled` escape hatch, then deleted). One loop
implementation, server-side, is a hard rule — two independently drifting loops
is how the current situation happened.

### 16.4 Headless / CLI

`gitpilot run --headless` targets the same engine with
`approval.mode: none` (denials instead of prompts) and `--max-iterations` /
`--budget` flags; emits the event stream as JSONL on stdout, enabling the §19
acceptance tests to be plain CLI assertions.

---

## 17. Module layout

New code is additive; nothing moves until its consumer migrates.

```
gitpilot/
├── agent/                      ← NEW package (the engine)
│   ├── loop.py                 # AgentLoop, budgets, state machine
│   ├── runner.py               # start/resume/cancel API used by endpoints
│   ├── context.py              # AgentContext, ToolLedger, BudgetState
│   ├── journal.py              # RunJournal (JSONL, seq, replay)
│   ├── policy.py               # PolicyEngine, CapabilityMask, Decision
│   ├── command_class.py        # semantic shell classification
│   ├── model_profile.py        # ModelProfile resolution + probe cache
│   ├── adapter.py              # ModelAdapter base + dialect dispatch
│   ├── dialects/
│   │   ├── native.py           # provider tool-calling, token streaming
│   │   ├── react_text.py       # grammar, tolerant parser, retry
│   │   └── lite.py             # micro-loop templates (investigate/act/answer)
│   ├── providers/
│   │   ├── anthropic.py        # thin clients; no CrewAI/litellm
│   │   ├── openai_compat.py    # grown from direct_chat + inference/
│   │   └── watsonx.py
│   ├── delegation.py           # agent.delegate, subagent templates, compression
│   └── prompts.py              # loop system prompts (reuses agent_prompts budgets)
│
├── toolkit/                    ← NEW package (canonical tools)
│   ├── registry.py             # ToolSpec, ToolRegistry, alias table
│   ├── fs.py                   # wraps workspace/agent_tools/edit_backend/grep_backend
│   ├── terminal.py             # wraps sandbox routing (local_tools._run_via_sandbox)
│   ├── git.py                  # wraps workspace git ops
│   ├── forge.py                # github.* / gitlab.* wrappers
│   ├── testing.py              # test.detect / test.run
│   ├── web.py
│   ├── todo.py
│   └── mcp.py                  # registry adapter over mcp_client
│
├── topology/                   ← NEW package (schema v2)
│   ├── schema.py               # TopologyPolicy dataclasses + YAML loader
│   ├── registry.py             # supersedes topology_registry (which re-exports)
│   └── defaults/*.yaml
│
└── (existing modules keep their roles: sandbox.py, checkpoints.py, session.py,
    workspace.py, hooks.py, modes.py, skills.py, slash_commands.py, plugins.py,
    agent_events.py (+ new types), prompt_cache.py, agents_md.py, rules.py,
    permissions.py (config format), approval_protocol.py (gate), flags.py, …)
```

Deletions at the end of migration: CrewAI wrappers in `agent_tools.py` /
`local_tools.py` (implementation functions survive, decorators go), the fake
chunker, `streaming.py`'s parallel vocabulary, `_api_core.py` (F10),
`mcp_tools_bridge`'s bespoke transport, `extensions/vscode/src/local/`
(including its two dormant importers outside that tree —
`src/platform/vscodeAdapter.ts` imports `../local/fileOps` and
`src/agent/agentEventBus.ts` imports a type from
`../local/providers/interface` — or TS compilation breaks), `DANGEROUS_TOOLS`.

---

## 18. Migration plan

Eight phases, each additive, flag-gated, and independently shippable — the
Phase 1–4 discipline. Test counts are gates, not vanity: the suite currently
sits at ~1,850 test functions (static count; the ~1,266 figure in
the Phase 4 record is stale).

Mapping to the original brief's stage order (tool foundation → loop → sandbox
→ observability → checkpoints → default flip → subagents → specialized
topologies): all stages are present, with two deliberate re-orderings.
Sandbox routing needs no dedicated phase (it ships in A because `terminal.run`
wraps the existing sandbox layer), observability is spread across C/E/F
(journal+events, real streaming, replay), and **subagents land in E, before
the default flips in G** — the flip's benchmark gate is only meaningful if
delegation and verification already exist, since those are what the loop is
being benchmarked *with*.

| Phase | Delivers | Flag | Exit criteria |
|---|---|---|---|
| **A. Tool foundation** | `toolkit/` registry + `fs.*`, `terminal.run`, `git.status/diff/log` wrapping existing impls; `ToolExecutionContext`; alias table; schemas rendered for all three verbosities | `tool_registry` | Registry-executed tools byte-identical to legacy outputs on a golden corpus; policy hooks stubbed; +40 tests |
| **B. Model layer** | `agent/providers/*`, `ModelProfile` + probe cache, dialect adapters (native + react_text parse/render; lite templates ported from `generate_plan_lite`) | `model_profiles` | Recorded-fixture parity: same tool-call extraction across Anthropic, OpenAI-compat, Ollama, and Open WebUI fixtures incl. `<think>` models; profile *resolution* yields qwen2.5:1.5b→LITE (built-in table), llama3:8b→REACT_TEXT, claude→NATIVE (provider family); the runtime probe itself is tested separately with model ids absent from every table; +50 tests |
| **C. The loop** | `agent/loop.py` + context + journal + events; **T9 becomes real** (experimental opt-in card); headless JSONL mode; policy hooks still stubbed (permissive) | `agent_loop` | C-scoped trace test: the §19.1 read task run under T9 on NATIVE and REACT_TEXT with a live local model asserting *trajectory categories and journal integrity only* (no policy assertions yet); run journal replays byte-stable from the embedded seed; +60 tests |
| **D. Safety wiring** | PolicyEngine + command classifier; ApprovalGate repairs (F1, F2); loop-owned checkpoint-on-decision; hooks firing; sandbox approval token (F7); permission-state unification (F11); F6 denylist dedup | `agent_policy` | Every mutating call in a recorded run shows `policy_decision`→(approval)→`checkpoint_ref`→execute→`tool_result` in journal order; SSE approval round-trip test; plan mode provably read-only under the loop; +50 tests |
| **E. Claude-Code UX** | `todo.write` + events; `agent.delegate` + subagent templates + compression; `test.detect/run`; verification policy; real token streaming; VS Code console updates | `agent_loop` (same) | Full §19.1 read-task trace test with policy assertions passes; multi-step task shows live TODO + nested delegation in VS Code; `verification: required` blocks unverified finals; streaming p50 first-byte < 1s on Anthropic; +50 tests |
| **F. Resume & context** | `awaiting_approval` persistence (until here, restart-while-pending denies — the safe direction), `POST /api/v2/agent/resume`, SSE replay via `Last-Event-ID` (F12), in-loop compaction | `agent_resume` | Kill -9 during a 20-iteration run → resume completes it; reconnect mid-run loses zero *state-bearing* events (in-flight text deltas replaced by journaled turn text); small-model long run compacts without losing file paths; +40 tests |
| **G. Topology v2 & the flip** | schema v2 + YAML loader + auto flow-graphs; T3–T8 re-expressed as policies; benchmark harness; **`default` → agentic_loop**; `classic` alias; §19.1 write-twin + `code_inspector` variant land here | `agent_loop_default` | Bench gate: v2 topologies **≥ legacy on success rate** (the gate), with wall time and tokens within a ≤1.5× regression budget per model tier (an iterative loop will never beat a single-completion Lite plan on tokens — dominance on all metrics would block the flip forever); legacy engines behind opt-out flag; +60 tests |
| **H. Ecosystem & cleanup** | MCP unification on `mcp_client` (F3, F4) with real inputSchemas; plugins/skills/slash wiring; modes/AGENTS.md/rules/prompt-cache in the payload; web app on v2 stream; deletions (§17); parallel delegation via `agent_teams` (stretch) | per-feature | MCP tool with a schema round-trips through native + react dialects; plugin install → skill/hook/MCP active next session; docs (`agents.md`, topology docs) rewritten to match reality; CHANGELOG debt paid |

Rollback story: every phase's flag defaults off until its exit criteria pass in
CI + one release of dogfooding; the flip (G) is a single flag revert away for
one full cycle.

---

## 19. Acceptance criteria

### 19.1 The trace test (the definition of "behaves like Claude Code")

Integration test, headless, no step hardcoded. (Gating schedule: a C-scoped
variant without policy assertions gates Phase C; the full read task gates
Phase E; the `code_inspector` variant and the write twin gate Phase G — §18.)

> *"Analyze how this repository's testing infrastructure works. Do not modify
> files."* — run against the GitPilot repo itself with `code_inspector`.

Pass = the journal shows: ≥1 `fs.read` of a build/config file (`Makefile` /
`pyproject.toml`), ≥1 `fs.glob`/`fs.grep` over `tests/`, ≥1 READ_ONLY
`terminal.run` **or** `test.detect`, zero mutating calls (and zero `ask`
decisions — the mask is read-only), a final answer naming the real frameworks —
with the trajectory *discovered*, i.e. the test asserts categories and
ordering constraints, never exact steps.

And the write-path twin:

> *"Find why the checkpoint tests fail and fix them"* (against a fixture repo
> with a seeded bug) — pass = failing `test.run`, ≥1 `fs.edit`, passing
> `test.run`, all mutations behind journaled approvals in `normal` mode.

### 19.2 The compatibility matrix (the definition of "low end to high end")

The same two tasks, three profiles, one engine:

| Model | Expected dialect | Gate |
|---|---|---|
| Claude (Anthropic API) | NATIVE | both tasks pass; prompt-cache hit rate observed |
| llama3:8b (Ollama) | REACT_TEXT | read task passes; write task passes with ≤2 parse-retry events |
| qwen2.5:1.5b (Ollama) | LITE | read task passes (micro-loop); write task produces validated `ACTION`s through the policy engine, or degrades to a correct answer — never a hallucinated path applied |

Plus the ladder test: a NATIVE-profiled fake provider that emits garbage tool
calls twice must produce a `dialect_downgraded` event and a completed run.

### 19.3 Safety invariants (property tests, run continuously)

- No `ToolResult` with `data.paths_written`/exit-code side effects exists in
  any journal without a preceding `policy_decision` line whose verdict is
  `allow` or `approved` (checkable because *every* decision is journaled
  pre-execution, §5.2/§9.3).
- No mutating tool executes in `plan` mode, in read-only topologies, or after
  a `deny` — across all dialects, including LITE's synthesized calls.
- No file content enters `ctx.messages` without a journaled `fs.read` —
  including LITE's prefetch and `READ`-line injections (§6.4).
- No journal line ever contains reasoning-tag content.
- `DESTRUCTIVE`/`PRIVILEGED` classifications never reach a prompt (denied
  flat), and secrets-env stripping holds on every `terminal.run` (extend the
  existing sandbox tests to the tool path).

---

## 20. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Small models thrash in an open loop (burn tokens, no progress) | LITE micro-loop bounds each phase; per-topology iteration/tool budgets with honest `budget_exceeded` results; degradation ladder is one-way down within a run |
| Removing CrewAI regresses some provider quirk it silently handled | Providers layer grows from `direct_chat`/`inference/` code already in production for folder mode + repair; recorded-fixture parity suite in Phase B; legacy engines untouched until G |
| Approval fatigue once approvals actually work | `Allow for session` scope already exists; READ_ONLY/TEST auto-allow; T9's "approval batcher" idea lands as batched consecutive-`ask` prompts in Phase E if dogfooding shows fatigue |
| Journal growth | Journals are per-run, text-only, pruned with `CheckpointStore.prune` policy (keep last N runs); full tool outputs cap per line with overflow to side files |
| The flip regresses users who liked deterministic pipelines | Bench gate before the flip; `classic` alias for a cycle; pipelines remain expressible (a policy can force `sequential_pipeline` until H) |
| Two safety systems during migration (legacy paths without the loop) | Legacy paths are frozen, not extended; the flag matrix in CI runs both; F1/F6/F7/F8 fixes apply to legacy paths immediately in Phase D since they're plain bugs |

Open questions (decide in Phase A/B RFCs): exact `.gitpilot/models.yaml`
shape; whether `git.commit` defaults to `ask` or `allow` in `default` (this doc
says `ask`); watsonx native-tool support depth; whether the probe should also
measure parallel-call support.

---

## 21. Appendix

### 21.1 Naming map (legacy → canonical)

| Legacy (CrewAI display name / VS Code id) | Canonical |
|---|---|
| "Read file content", `read_file` | `fs.read` |
| "Write local file" / "Write or update a file in the repository", `write_file` | `fs.write` |
| "Edit a section of a file (exact string replacement)", `edit_file` | `fs.edit` |
| "Run shell command", `run_command` | `terminal.run` |
| "Run code in sandbox", `run_in_sandbox` | `terminal.run` (language-snippet arg form) |
| `git_status` / `git_log` / `git_commit` | `git.status` / `git.log` / `git.commit` |
| `grep_repository` | `fs.grep` |
| `semantic_search` | `fs.semantic_search` (kept behind `rag_retrieval` flag) |
| TodoWrite (flow-graph fiction) | `todo.write` |
| Task(explore/plan/review/research/gitops) (flow-graph fiction) | `agent.delegate {agent: …}` |

### 21.2 Event vocabulary after the upgrade

`run_started`, `run_resumed`, `status_change`, `iteration`, `text_delta`
(real), `tool_start`, `tool_result`, `file_write`, `approval_needed`,
`approval_resolved`, `todo_updated`, `agent_delegated`, `agent_delegate_done`,
`test_result`, `terminal_output`, `terminal_exit`, `diagnostics`,
`checkpoint_created`, `compaction`, `dialect_downgraded`, `tools_pruned`,
`plan_step` (legacy), `done`, `error`.

### 21.3 Relationship to prior plans

- The v3 phase plan promised the modules; they exist. This plan is
  about *connecting* them — its P1 "Integration into Existing Architecture"
  sketch (hooks around tools, checkpoint-before-execution, permission checks
  in dispatch) is implemented here as §5.3/§8/§9.5, but inside a loop instead
  of a dispatcher.
- The Phase 1–4 flag/batch discipline is retained verbatim.
- The AI-coder governance/author split (`docs/AI_CODERS.md`) is the
  philosophical precedent: this design applies the same split product-wide —
  topology (governance) over AgentLoop (author).
- `docs/agents.md` and `docs/vscode/agent-topologies.md` must be rewritten in
  Phase H; both currently document the react loop as if it executes.
