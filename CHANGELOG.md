# Changelog

All notable changes to GitPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation debt (acknowledged)
This changelog has recorded almost nothing since `0.1.0` while the project
shipped several substantial programs. They are documented in `docs/`, not here,
and this note exists so the gap is visible rather than implied:

- **Topologies and the agent architecture** — nine topologies (T1–T9), the
  execution-style vocabulary, the request router and the ten agents:
  `docs/agents.md`, `docs/vscode/agent-topologies.md`.
- **Claude Code parity plumbing** — AGENTS.md context, @-mentions, context
  budget, tool groups and per-mode policy, custom modes, slash commands,
  checkpoints with shadow-git snapshot and rewind, custom rules, the sandbox
  (`off`/`subprocess`/`matrixlab`), trusted folders: `docs/UPGRADES.md`.
- **Phase 1–4 batch programs** — flags service, coverage gate, strict mypy,
  error envelope, `gitpilot doctor`, prompt cache, lazy MCP tool defs,
  context-pack LRU, SSE streaming, model warmup, init wizard, API stability
  layer, docs site, SBOM/Sigstore: summarised in `docs/history.md`.
- **What is designed, and how much is built** — the agentic runtime is
  specified in `docs/upgrade-plan-v4-agentic-runtime.md` and sequenced in
  `docs/upgrade-v4-batches.md`. Phases 0/A/B/C/D/E/F/G have shipped: the loop runs
  real tasks with every tool call authorized, a crash is resumable, and every
  topology is now a declarative policy document over that one engine. See the
  entries below.

Entries below resume normal per-change logging.

### Added — topologies became policy documents
- **A topology is a YAML document, not a hand-drawn graph.** It states which engine
  runs, which dialect, which capabilities are granted, who approves what, whether
  the run must prove itself, and where its limits are. The flow graph in the Agent
  Workflow view is *generated* from it, so the picture can no longer claim a tool the
  policy withholds — the previous graphs named a "tool-def pruner" and an "approval
  batcher" as though they were agents, and nothing executed any of it. Built-ins live
  in `gitpilot/topology/defaults/`; users add `~/.gitpilot/topologies.yaml`, and a
  project's `.gitpilot/topologies.yaml` is read only from a trusted workspace.
- **`approval.mode` can only tighten.** A document may force `plan` or `normal`; it
  cannot raise a `normal` session to `auto`. A topology is configuration, and
  configuration is not an authenticated escalation channel.
- **`default` declares the Autonomous Engineer**, and `classic` preserves the
  pre-v4 CrewAI routing under its own name for one release cycle. `gitpilot_code`
  and `autonomous_engineer` resolve to `default`, so saved preferences survive.
- **The five pipelines are policies now.** Feature Builder, Bug Hunter, Code
  Inspector, Architect Mode and Quick Fix each became a document — the ordering each
  encoded survives in a role prompt, in one context, rather than in four or five
  separate LLM calls that each had to be told what the previous one found.
- **Lite Mode is the same engine with the dialect pinned**, not a separate planner
  universe: no delegation, twelve iterations, and a suite it can actually run.
- **MCP servers are canonical tools.** `mcp.<server>.<tool>` entries carrying the
  server's real `inputSchema`, which the JSON-RPC client had been capturing and
  discarding for months. Mutating tools ask before they run and are refused outright
  in a read-only run.
- **`modes.yaml` finally does something.** A mode's persona reaches the system
  prompt and its tool policy narrows the run's capability mask. `activate_mode` had
  no caller at all: a user could write a mode, see it listed, select it, and have it
  change nothing.

### Not yet flipped
- The agentic engine serves a topology only when a flag says so —
  `agent_loop` for the experimental pilot, `agent_loop_default` for every migrated
  topology. Both ship **off**, so `default` still runs the CrewAI dispatcher. The
  second turns on after a benchmark gate: matching the legacy path's success rate on
  a frontier model, an 8B local model and a 1.5B local model, with wall time and
  tokens inside a 1.5× budget. The harness is `bench/` (`python -m bench`,
  `python -m bench.gate`), and the gate has not yet been run on all three tiers.

### Fixed — found by running the engine end to end
- **Every shipped topology granted nothing it named.** Twelve tool specs declare a
  grouped capability that is not their id (`git.status` → `git.read`,
  `github.pr.create` → `github.pr.write`), and all eight topology documents name tool
  ids — the design's own example writes `github.pr.create: ask`. Only the group was
  checked, so under the *default* topology every git-read and every GitHub tool was
  silently withheld: not refused at call time, absent from the schema list. A grant may
  now name either, and the policy engine uses the same rule, so a tool the registry
  offered cannot then be refused for the opposite reason.
- **A small model was offered a read-only toolset.** `llama3:8b` has an 8-schema
  budget, and every write tool ranked behind every read tool, so the model tier this
  engine most exists for could not create a file. `fs.write`/`fs.edit` now rank
  alongside `fs.read`/`fs.grep`/`terminal.run`; at a 6-schema budget the agent still
  gets read, write, edit and shell.
- Both were found by `scripts/verify_end_to_end.py`, which asks a model to write
  `hello.py` and then *runs the file* — `make verify-e2e`, or
  `make verify-e2e-real MODEL=…` against a pulled Ollama model.

### Changed
- **Read-only runs can read the repository again.** `git.status`, `git.diff` and
  `git.log` declared an effect meaning "changes local git state", which the
  read-only gate reads — so a read-only review could not look at the code it was
  reviewing. They declare a read now.
- **A read-only run may execute a command the classifier reads as read-only.**
  `cat Makefile` was being refused in the same breath as `rm -rf`; the classifier's
  binary allowlist is the same evidence that makes `fs.read` safe. Running the test
  suite additionally requires the topology to name `TEST` in the capability's
  `classes`.

### Removed
- **The in-extension agent tree.** `extensions/vscode/src/local/` was a second agent
  implementation inside the VS Code extension — its own LLM client, tool engine, agent
  loop, context builder, git wrapper and workspace scanner, 2,149 lines. The extension
  stopped driving it once the backend grew a real engine, but it kept compiling. Six of
  its eight modules had no importer; the two that did were dormant. The event union
  survives in `src/agent/agentEvents.ts`, still re-exported from the event bus, so no
  consumer changed.
- Nine superseded planning documents left `docs/` — four per-phase status pages, the
  v3 phase plan, two completed VS Code migration notes, and two analyses whose
  conclusions have shipped. What was worth keeping is in `docs/history.md`; the
  reasoning behind each decision is in the commit that made it.

### Added — ecosystem
- `docs/matrix-ecosystem-compatibility.md` — an assessment of how matrix-builder,
  matrix-designer and SelfRepair fit together and where they do not, read at specific
  commits with every claim anchored. The finding that matters for GitPilot: all three
  delegate coding to it and no two agree on the transport, which is the one thing
  GitPilot can settle rather than each consumer settling separately.

### Deprecated
- `mcp_tools_bridge.invoke_remote_tool` and `build_mcp_agent_tools` — the bespoke
  `{"method": "tools/call"}` HTTP transport. Superseded by `gitpilot.toolkit.mcp`,
  which reaches servers through the real JSON-RPC client. The store, toggle and risk
  layer above it survives and is what the new module calls into.

### Added — what the agent shows you while it works
- **A task checklist the model maintains.** For work with several steps the model
  keeps a list — marking one item in progress, completing it, and rewriting the
  list when what it finds changes the shape of the work. It is deliberately not a
  plan: nothing enforces that the list be followed, because "produce a plan then
  execute it" is the architecture the agentic engine replaces. A small model gets
  the same panel without the tool: it has no reliable way to maintain structured
  state alongside its actual work, so the engine derives a coarse
  investigate → act → check list from the phase it is already in.
- **Subagents that actually run.** `agent.delegate` hands a self-contained piece of
  work — mapping a subsystem, reviewing a file, diagnosing a test failure — to a
  focused child with its own journal, and gets a structured answer back rather than
  the child's entire narration. Four templates ship: explorer, reviewer,
  researcher, test analyst. Those four have been drawn as `Task(...)` edges in the
  GitPilot Code flow graph for months, spawning nothing; the graph was a picture of
  an intention rendered as though it were behaviour. A child can never do more than
  its parent — the capability mask is the intersection, so a read-only run delegates
  read-only work — and it cannot delegate onward unless the session explicitly
  allows the depth.
- **Verification the model can see.** A topology can require that changes be tested:
  a run that modified files may not finish until the tests have run, and the model
  is told what is missing and left to act on it. This replaces running the tests
  *after* the agent finished and reporting the result to the user — which produced a
  red suite the model never learned about. When the attempts run out the answer says
  the changes are unverified rather than quietly reporting success, and a project
  with no test suite is not blocked on running tests it does not have. In lite mode
  the engine issues the test run itself, because a policy that only holds for
  frontier models is not a policy.
- **A real agent console in VS Code.** Tool activity rows with canonical names and
  timings, the checklist, nested blocks for subagent work, and approval cards that
  say what kind of thing is being approved ("this command changes installed
  packages (MUTATING)") along with the sandbox facts — instead of "run a shell
  command?" and leaving you to read the string.

### Fixed — streaming, and stopping
- **Streaming was a costume.** The executor waited for the whole batch result, then
  sliced the finished text into 80-character pieces and emitted them 15 ms apart. It
  had every visible property of token streaming except the one that matters: the
  first word still arrived after the run had finished. Worse, it made the *absence*
  of real streaming invisible, which is how it survived. The agentic engine streams
  provider tokens as they arrive; the react dialect streams whole lines, because a
  half-written tool call is not something a client can render; lite mode announces
  each phase, because a constrained line protocol streamed verbatim shows you
  protocol rather than progress. The legacy path now sends its answer once and is
  honest about being a batch executor.
- **Cancel did not cancel.** VS Code's stop button aborted the local connection,
  which stopped the extension listening while the run carried on server-side —
  still writing files, still spending tokens, and still there if you reconnected. It
  now tells the server, whose cancellation is cooperative between tool calls and a
  real interruption point inside streaming.
- **Two stream implementations became one.** `streaming.py` had named events,
  15-second heartbeats, back-pressure and timing metrics, and no caller; the event
  bus had every caller and a 25-second heartbeat that left no margin against the
  usual 30-second proxy idle timeout. The bus carries the better framing now.

### Fixed — safety libraries that had never run
- **Approvals could not be answered over SSE.** `POST /api/v2/approval/respond`
  emitted an `approval_resolved` event that nothing consumed and returned
  `{"status": "resolved"}`. The run's pending future then waited out its
  120-second timeout and denied, while the client had been told its answer
  landed. A WebSocket client worked, because it calls the gate directly; every
  other client did not. Approvals now register in a transport-blind registry that
  both paths resolve through, and the endpoint returns 404 when no request is
  waiting — reporting success for a request nobody is listening to is how this
  stayed invisible.
- **No tool had ever been gated.** `ApprovalGate.check()` — the whole approval
  mechanism — had no caller. The streaming executor stored the gate in
  `self._gate` and never touched it again, so the approval card was reachable only
  through the sandbox's own flow. The agentic loop's `ask` arm is its first real
  caller. Its hardcoded `DANGEROUS_TOOLS` set is gone too: it listed
  `write_file` and `Write local file` but not `fs.write`, so a canonically-named
  tool fell straight through as safe.
- **`POST /api/sandbox/run` executed anything it was handed.** The ExecutionPlan
  card, its safety warnings and its Approve button were all client-side; the
  server never checked that a plan had been shown, let alone approved. Approving a
  plan now mints a single-use, short-lived token bound to the session *and to the
  code itself*, so the bytes that execute are the bytes that were approved, and
  the token cannot be replayed. The card's UX is unchanged. A session the user
  themselves switched to `auto` still runs without one — read from the persisted
  session record, never from a request body.
- **Lifecycle hooks never fired.** Ten events were defined, documented, loadable
  from `.gitpilot/hooks.json` and listed by the API. Nothing called
  `HookManager.fire`, so a `pre_commit` hook you wrote would appear configured and
  never run. They fire now — and a blocking one produces an observation the model
  reads ("commits need a ticket number") rather than an exception. The manager also
  built its child environment from `os.environ` unfiltered, which would have handed
  every user-authored hook every API key; credentials are stripped, as they already
  were for the terminal executor.
- **The permission mode was tracked in three places.** A process-global
  `PermissionManager`, a `permission_mode` field on every chat request, and the
  approval gate's own copy — with the request body winning. It now lives on the
  session record and nowhere else. A request may only *restrict* it: an `auto`
  session plus a `plan` request runs in plan mode, and a `normal` session plus an
  `auto` request stays normal, because a chat body is not an authenticated
  channel. Raising a session to `auto` takes `PUT /api/permissions/mode`.
- **`PermissionManager` and `ToolPolicy` had no callers at all.** Blocked paths,
  per-action confirmation, mode tool-groups and edit guards were configuration
  nothing read. The policy engine consults all of them. The name mismatch that
  made `ToolPolicy` unable to match a GitPilot tool disappears rather than being
  patched — canonical tool ids are already the snake_case shape it expected.
- **Snapshots hung off the approval gate.** So no checkpoint was ever taken from
  that path, and had the gate acquired a caller the "ask" route would have
  snapshotted twice. The loop owns it now: one snapshot before every mutating
  call, in every permission mode, recording the arguments (a field every previous
  caller left empty) and where in the run it sits. `CheckpointStore.prune` gets
  its first production caller, so a loop that snapshots before every write no
  longer grows the shadow repository without bound.

### Added
- **Semantic command classification.** A shell command is judged by what it does
  rather than by the name of the tool that runs it, so `pytest` and `rm -rf /`
  reach different verdicts through the same `terminal.run`. Nine classes from
  `READ_ONLY` to `PRIVILEGED`; chains and pipes are judged by their worst
  segment, so `ls && sudo rm -rf /tmp/x` is privileged rather than a directory
  listing; an unrecognised binary asks rather than running. `DESTRUCTIVE` and
  `PRIVILEGED` are refused outright rather than prompted — an approval dialog for
  `rm -rf /` has one safe answer and the unsafe one is a misclick away. Approval
  cards name the class, so "run a shell command?" becomes "this command installs
  packages (MUTATING)".
- **Per-session capabilities.** A session can be restricted to named
  capabilities, with path constraints (`fs.write: {paths: "src/**"}`), a network
  switch, or a blanket `ask`. A capability that is not granted is not offered —
  the model never sees the tool's schema, so it cannot spend a turn calling
  something that would only be refused.
- **A safety property suite in CI.** Six invariants asserted over recorded
  journals from real runs across three dialects and three permission modes: no
  side effect without a preceding allow, no mutation in plan mode, no file content
  in the transcript without a journaled read, no private reasoning persisted
  anywhere, the two refused command classes never reaching a prompt, and no
  credentials in any subprocess environment. They are a ratchet: a future change
  that routes around the policy engine fails them without touching a test that
  knows about it.
- **An agentic execution loop (`agent_loop` flag, off by default).** GitPilot can
  now run a task the way a coding agent does: generate a turn, dispatch the tools
  the model asked for, feed the observations back, and iterate until the model
  says it is done — with every call authorized and journaled before it executes,
  every step streamed as it happens, and a budget that stops a runaway run with
  partial results rather than being killed from outside. Turning the flag on
  affects the **Tool-Augmented ReAct** topology only; every other topology takes
  the existing path unchanged.

  The engine speaks three dialects of the same loop, so it works on a frontier
  API and on a 1.5B model running locally: native provider tool-calling, a
  `Thought:/Action:` grammar parsed in-house, and a constrained line protocol with
  pre-fetched context. It degrades one rung at a time when a model cannot hold up
  its end, and a run that had to drop work finishes `degraded` and names what it
  skipped instead of reporting success.

  Two things improve regardless of the flag's ultimate default. A session with **no
  GitHub repository** — a plain folder or a local checkout — can stream for the
  first time; the existing executor closes the stream for those sessions, which is
  exactly the local-model case. And `gitpilot run --engine loop` drives a run with
  no server at all, printing one JSON object per event with `--headless`, so a
  trajectory is inspectable in CI.

  New: `gitpilot/toolkit/` (one canonical, namespaced tool registry shared by both
  surfaces) and `gitpilot/agent/` (providers, model profiles, dialects, the loop,
  its context and its append-only run journal). Reasoning-tag content is never
  written to the journal, and that is asserted rather than intended.

### Fixed
- **MCP tool wrappers all called the same tool.** `MCPClient.to_crewai_tools`
  defined its wrapper inside the discovery loop, so `conn`/`tool_name` were
  captured by reference to the enclosing scope: every generated tool carried
  the correct name and invoked whichever tool the loop visited last. Binding
  now happens through a factory's parameters (not default arguments, which
  would leak into the signature CrewAI derives its args schema from), and the
  description is set before decoration — assigning `__doc__` afterwards was a
  no-op, since CrewAI reads it when it decorates.
- **GitPilot's own MCP server reported empty or missing capabilities.**
  `gitpilot.list_skills` probed for `SkillManager.list()`/`all()` — the real
  method is `list_skills()` — and returned `{"available": true, "skills": []}`,
  a silently wrong answer rather than an error. `gitpilot.plan` probed for
  `agentic.build_plan`/`plan`, neither of which has ever existed, and always
  answered "no plan() entrypoint"; it now calls `generate_plan` with the
  arguments that function actually takes. `gitpilot.execute` cannot be honoured
  at all (plans are not persisted server-side, so a `plan_id` resolves to
  nothing) and now says so, and what to use instead, instead of surfacing a
  `TypeError` that read like a caller mistake.
- **The terminal executor handed credentials to everything it ran.** It built
  its child environment from `os.environ` unfiltered, so every linter and test
  suite run by the validation phase received `GITHUB_TOKEN` and every provider
  API key. It also carried a shorter copy of the sandbox's command denylist, so
  `shutdown -h` was refused by the sandbox and accepted here. Both guards now
  come from one module, `gitpilot.shell_safety`; explicitly-passed environment
  variables are still honoured, since a caller that passes a token means it.
  The workspace clamp that `execute()` applies is now applied by
  `execute_streaming()` too — the two had drifted, and the streaming path is
  the one that runs test commands.
- **Repo chat over the legacy session WebSocket ignored the caller's identity.**
  `/ws/sessions/{id}` passed no token to the dispatcher, so it fell through to
  whatever `GITHUB_TOKEN` the server process held — the operator's credentials,
  for every connection. It now reads `Authorization` (native clients) or a
  `token` query parameter (browsers cannot set headers on a WebSocket
  handshake), and runs the dispatch inside `execution_context` so the agent's
  tools resolve it, exactly as the HTTP chat routes do.

### Removed
- **`gitpilot/_api_core.py`** — a 2,518-line orphaned snapshot of the API
  module, imported by nothing. A near-duplicate of the live `_api_app.py` is an
  invitation to edit the wrong file.
- A duplicated topology-resolution block in `dispatch_request`, where the first
  copy's result was immediately overwritten by the second after a redundant
  re-read of the saved-preference file.

### Added
- **Coder API** (`POST /repair` + `GET /repair/health`), bearer-token gated (`GITPILOT_API_TOKEN`), mounted into the main app — turns a repair-plan into a dry-run patch preview for SelfRepair / matrix-maintainer over HTTPS.
- **Runtime-aware onboarding** — `/api/status` now reports `workspace.runtime` (`cloud`/`local`); in a cloud workspace the empty state guides you to connect GitHub and pick a repository, while a local install offers Folder / Local Git paths with GitHub optional.
- **Account-first auth across split deployments** — portable `X-GitPilot-Session` token (cross-origin Vercel↔HF), a dedicated email-verification screen, a Settings → Account tab (update name, change password, delete account), and "GitHub not linked" now shows a calm Connect prompt instead of a repo-fetch error.

### Added
- **`make install-cli` and `make check-cli`** — point the `gitpilot` command at
  the current checkout, and report which GitPilot it actually runs. `make
  install` syncs `.venv` (which `make run` uses) but never touched PATH, so on
  any machine that had once run `pip install gitcopilot`, `gitpilot serve` kept
  starting a released wheel from `~/.local/lib/python3.11/site-packages/`
  — a different version, different dependencies, none of your changes, and no
  warning. The only visible difference was a version number inside a banner
  (`v0.2.7` against `v0.2.8`), so a fix that was definitely in the tree could
  appear not to work at all. `make install` now ends by naming the mismatch;
  fixing it stays opt-in, because installing a command outside the project is
  something to ask for rather than have done to you.

### Fixed
- **Reasoning models could not build an agent at all.** Every query from the
  web app failed with `2 validation errors for Agent` — `Agent.llm` is a
  validated pydantic field typed `str | BaseLLM`, and the reasoning wrapper was
  a plain class that is neither. Composition over subclassing was a deliberate
  choice (CrewAI's LLM class changes between versions) and was safe right up
  until that field started validating; after which deepseek-r1, QwQ and every
  other reasoning model failed at agent construction, before a single token was
  generated. The wrapper now subclasses CrewAI's `BaseLLM` — an ABC whose one
  abstract method, `call`, is the method the wrapper existed to intercept — and
  is built lazily so importing it still does not drag CrewAI into every
  process. Verified end to end against Ollama 0.12.9 and deepseek-r1: agent
  builds, crew runs, no `<think>` leakage. Non-reasoning models are returned
  unwrapped exactly as before. This module had no tests, which is how it
  shipped; it has 17 now.
- **GitPilot's own log lines never reached the console.**
  `uvicorn.run(log_level="info")` configures uvicorn's three loggers and
  nothing else; GitPilot's records propagated to a root logger with no handler,
  so Python fell back to `logging.lastResort` at WARNING. Every route decision,
  provider call and timing was written and discarded, while uvicorn's own INFO
  lines made logging look like it was working. GitPilot now logs at INFO by
  default, with `gitpilot serve --log-level` / `GITPILOT_LOG_LEVEL` (the env
  var also carries the choice into a `--reload` child). The root logger is left
  alone — GitPilot is importable as a library and does not own the process's
  logging.
- **Agent runs were silent on exactly the path that needed a trace.** The Lite
  crews — the ones a small local model uses — were built with `verbose=False`,
  so a multi-agent run produced no console output at all. They narrate now;
  `GITPILOT_AGENT_VERBOSE=0` restores the quiet behaviour.
- **The workspace scan hid the project from the model.** The VS Code context
  builder walked the tree depth-first and stopped at 300 entries, so the budget
  went to whichever directory `readdir` returned first. Measured on this
  repository: `extensions/` took 171 entries and `docs/` another 65, leaving
  `pyproject.toml` and every file under `gitpilot/` — the application itself —
  absent from the context. A model handed that listing cannot tell it is seeing
  a fraction of a repository, so "explain this project's architecture" came back
  describing something else entirely, confidently. The walk is breadth-first
  now, files rank ahead of directories at each level, and the ignore list gained
  the cache and build directories it was missing (`__pycache__`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `target`, `vendor`,
  `out` and others — the first two alone were taking 14 of the 300 slots). Same
  repository after the change: `gitpilot/` 118 entries, `tests/` 61,
  `pyproject.toml` and `README.md` present, no cache entries at all.
- **`Fallback to LiteLLM is not available` on a working Ollama.** Planning
  goes through CrewAI, and CrewAI routes a call natively only when the
  provider is in its `SUPPORTED_NATIVE_PROVIDERS` list — everything else
  goes to its optional LiteLLM fallback, which raises outright when LiteLLM
  is not installed. On CrewAI 1.6 that list is `openai, anthropic, claude,
  azure, azure_openai, google, gemini, bedrock, aws`; Ollama joined it
  around 1.10. So `POST /api/chat/plan` returned 500 on a configured,
  reachable Ollama, and neither obvious spelling helped:
  `provider="ollama"` names a provider CrewAI will not route, and
  `model="ollama/llama3:8b"` is validated against CrewAI's model constants,
  which no locally served model satisfies.

  **Ollama, OllaBridge, Open WebUI and custom endpoints** were all affected
  — the last three spelled it `model="openai/<model>"`, which fails the same
  validation. All four now ask for `provider="openai"` explicitly and pass
  the endpoint's own base URL and model id through untouched. This needs no
  LiteLLM and works on old and new CrewAI alike.
- **Advice that pointed at the provider you were already using.** The agent
  runtime hint told whoever hit this to "switch to Ollama, OllaBridge, Open
  WebUI, OpenAI or a custom endpoint" — always the provider they were on. It
  now reads the active provider and, for the endpoints that need no LiteLLM,
  reports that the installed CrewAI is too old and gives the upgrade command.
- **The thinking animation stopped and restarted for a single question.** A
  session with no GitHub repository has nothing for the multi-agent planner to
  plan against, so the SSE stream closes at once and the client falls back to
  `/api/chat/send`. That handover is deliberate — but it was announced as
  `status_change("done")`, and "done" is what the UI reads to stop the spinner.
  A request that had not started was reported finished, the animation cleared,
  and the extension raised it again for the batch call. `agent_done` alone
  terminates the stream now; the status belongs to work that happened.
- **Reasoning models on the direct path.** deepseek-r1 and QwQ think before
  answering. Ollama (checked on 0.12.9 and 0.32.6) puts that in a sibling
  `reasoning` field, while llama.cpp, vLLM and LM Studio inline it as
  `<think>` tags. `build_llm()` has always stripped the tags, but the direct
  provider path does not go through CrewAI and handled neither: inlined
  reasoning was shown as the answer, and a model that spent its whole budget
  thinking — leaving `content` empty with the substance in `reasoning` — was
  reported as "returned an empty response", blaming a provider that had
  answered. Tags are now stripped, `reasoning` is read when `content` is
  empty, and a reply that is *only* reasoning is shown rather than discarded.
- **No way to tell which chat pipeline ran.** The agent path prints CrewAI's
  full verbose trace to the server console and the direct path prints nothing,
  because it has no agents — so comparing the web app (which plans against a
  repo) with VS Code (which often has only a folder) looked like logs being
  suppressed. Both branches now announce themselves, with model and elapsed
  time. The extension's Output channel gained the client half: why a stream
  was abandoned (unreachable / HTTP status / empty), the event tally, and the
  size and duration of the batch call that followed.
- **VS Code timed out on requests the backend was completing.** Chat, plan and
  execute took the deadline meant for an ordinary HTTP request — 20s — while
  a local Ollama answering with repository context routinely needs 20-60s.
  The web app allowed five minutes for the identical call, so the same
  backend appeared to work there and fail in VS Code
  (`POST /api/chat/send took 21.26s (status=200)` against
  `timed out after 20000ms`). A timeout also carries no HTTP status, so it
  escaped the non-retryable-status guard and the default two retries fired:
  three runs of a call the server was still executing, queued behind each
  other, each slower than the last. `/api/chat/send` appends to the session
  before returning, so duplicates that landed wrote the exchange to history
  twice. These calls now take a five-minute deadline matching the web app,
  are never retried, and report a timeout in terms of the model rather than
  the elapsed milliseconds. New setting `gitpilot.llmTimeoutSeconds`
  (default 300, minimum 30) for machines that need longer.
- **VS Code discarded the server's explanation.** `ErrorTranslator` chose its
  text from the HTTP status alone, so a 503 whose body named the provider,
  the missing package and the command surfaced as "circuit breaker active" —
  a breaker that was never involved. The server's `detail` now wins for
  500/502/503/504, and the API client attaches it so there is something to
  win with.

### Changed — `make run` now starts the MCP Context Forge stack by default

**Heads-up for upgraders.**  Until this release, `make run` started only the
GitPilot backend and frontend; the MCP stack was opt-in via `make run-mcp`
or `make run-all`.  As of this release the happy path is:

```bash
make install     # uv + npm + MCP image cache
make run         # MCP Context Forge + GitPilot backend + frontend
```

`make run` now:

* depends on `run-mcp`, which itself depends on `install-mcp`;
* fails loudly when Docker / Docker Compose v2 / the daemon are missing
  (with a clear hint pointing at `make run-bare`);
* polls `http://localhost:${MCP_FORGE_PORT:-4444}/health` after
  `docker compose up -d`, so it only continues once the gateway is
  actually reachable by the GitPilot backend and UI.

**No-Docker escape hatch** — added `make run-bare`, which starts only the
GitPilot backend + frontend.  The MCP Servers tab will show the gateway
as Unreachable, but the rest of the app is fully functional.  Use this
on Hugging Face Spaces, CI smoke runs, and any minimal host.

`make run-all` is preserved as the "force-restart the backend" path
(now equivalent to `stop-soft && run`).  External tooling that called
it keeps working.

### Other build / docs updates

* `make install` is now opinionated as **runtime-only**: dev/test/build
  tooling moves to `make install-dev`; docs tooling to
  `make uv-install-docs`; a `make install-full` superset is available.
  Existing CI that calls `make test` keeps working — the target now
  uses `uv run --extra dev pytest` internally.
* Re-running `make install-mcp` is now incremental: existing clones skip
  network fetch unless `MCP_UPDATE=1`; existing images skip rebuild
  unless `MCP_BUILD=1`.
* Render deploy doc updated: build command is now
  `pip install uv && uv sync --no-dev` (was `uv sync --all-extras`),
  start command is `uv run --no-dev gitpilot serve ...`.  Hosted users
  that relied on dev tooling at runtime should keep the old commands or
  switch to `--extra dev`.
* WSL-friendly `uv` defaults — `UV_LINK_MODE=copy` and
  `UV_CACHE_DIR=.uv-cache` to avoid hardlink fallback warnings on
  `/mnt/c` checkouts.

### Added — MCP Context Forge integration (additive, opt-in)

- **`gitpilot/mcp_plugin/`** — Context Forge plugin (forge_client,
  policies, registry, agent_hooks). 13 tests.
- **`gitpilot/mcp_admin_api.py`** — REST surface for the Settings →
  MCP Servers tab (status, servers, catalog, install/uninstall,
  enable/disable/test, per-tool toggle, sync, agent_tools, forget).
  State persisted at `~/.gitpilot/mcp_servers.json`. 19 tests.
- **`gitpilot/mcp_forge_sync.py`** — pull-based reconcile loop.
  Idempotent, non-destructive (orphan flag, never auto-delete),
  user-state preserving. 18 tests.
- **`gitpilot/mcp_tools_bridge.py`** — every enabled MCP tool surfaces
  as a CrewAI agent tool, so the Coder/Reviewer/Test-Runner can pick
  them mid-conversation like Claude Code uses its built-ins. 10 tests.
- **`gitpilot/mcp_server*.py`** — GitPilot can also run *as* an MCP
  server (`GITPILOT_EXPOSE_MCP_SERVER=true`); 10-tool curated catalog,
  three scopes, recursion guard via `X-Gitpilot-Origin: self`. 22 tests.
- **`docker-compose.mcp.yml`** — Forge + 3 reference servers under
  Compose `mcp` profile, build-from-source via `mcp-stack/` clones
  (HomePilot pattern). Healthcheck-gated `depends_on`.
- **Make targets**: `install-mcp`, `run-mcp`, `run-all`, `stop-mcp`,
  `logs-mcp`, `sync-mcp`, `uninstall-mcp`, `install-mcp-workflows`,
  `smoke-mcp`, `fix-line-endings`. `install:` now chains `install-mcp`
  (skip-safe without Docker).
- **Frontend — Settings → MCP Servers tab**: GatewayHeader with Sync
  button + counters, three sub-tabs (Installed/Catalog/Custom),
  per-tool risk badges + `used by` chips, SyncReport banner with diff
  counts, orphan badge + Forget action.
- **`@homepilot/gitpilot-connect`** wizard (HomePilotAI/personas):
  seven-step setup, resumable via localStorage, tokens never
  persisted, headless API + React component. 20 vitest tests.
- **CI publish workflows** — `docker-publish.yml` shipped to all four
  repos (gitpilot + 3 MCP servers); multi-arch, semver/sha/latest tag
  matrix, OCI labels, GHA cache, smoke step per service.
- **Docs**: `INSTALL_MCP.md` (dev install), `PRODUCTION_MCP.md`
  (operator runbook), `extensions/mcp_workflows/README.md`.

### Changed
- `Makefile` `install:` chains `install-mcp` (skip-safe; baseline flow
  unaffected when Docker is absent).
- `.gitignore` adds `.mcp.env` and `mcp-stack/`.
- `.gitattributes` (new) pins `*.sh`, `Makefile`, `*.yml` to LF for
  Windows checkouts.

### Test count
- Pre-MCP: 855 passing.
- Post-MCP integration: **937 passing** (+82 new).

## [0.1.0] - 2024-11-14

### Added
- **Admin / Settings Console**
  - Full LLM provider management (OpenAI, Claude, Watsonx, Ollama)
  - Provider-specific configuration forms
  - Persistent settings storage (~/.gitpilot/settings.json)
  - API key management with secure storage
  - Model selection for each provider

- **Agent Flow Viewer**
  - Interactive workflow visualization using ReactFlow
  - Visual representation of multi-agent system
  - Node-based diagram showing agent collaboration
  - Animated edges displaying data flow
  - Color-coded agents vs. tools
  - Mini-map and zoom controls

- **Three-Tab Navigation**
  - Workspace tab for repository browsing and AI chat
  - Agent Flow tab for workflow visualization
  - Admin/Settings tab for LLM configuration

- **Multi-LLM Provider Support**
  - OpenAI (GPT-4o, GPT-4o-mini, GPT-4-turbo)
  - Claude (Claude 3.5 Sonnet, Claude 3 Opus)
  - IBM Watsonx.ai (Llama, Granite models)
  - Ollama (local models: Llama3, Mistral, CodeLlama, Phi3)

- **Core Features**
  - GitHub repository browsing and file tree navigation
  - AI-powered plan generation using CrewAI
  - Step-by-step execution plans with risk assessment
  - Repository file content viewing
  - Chat interface for natural language interactions

- **API Endpoints**
  - `GET /api/settings` - Get current LLM settings
  - `PUT /api/settings/llm` - Update provider configurations
  - `POST /api/settings/provider` - Change active provider
  - `GET /api/flow/current` - Get agent workflow graph
  - `GET /api/repos` - List user repositories
  - `GET /api/repos/{owner}/{repo}/tree` - Get repository file tree
  - `GET /api/repos/{owner}/{repo}/file` - Get file contents
  - `POST /api/chat/plan` - Generate execution plan
  - `POST /api/chat/execute` - Execute approved plan

- **Documentation**
  - Comprehensive README with installation and usage guide
  - Complete frontend code reference
  - Architecture documentation
  - API endpoint reference
  - Development guide

### Technical Details
- Built with FastAPI for backend
- React + ReactFlow for frontend
- CrewAI for multi-agent orchestration
- Production-ready build with optimized bundles
- Type hints and py.typed marker for type checking
- Ruff for linting and formatting
- Comprehensive error handling and loading states

### Notes
- Plan execution is currently stubbed for safety
- Full execution capabilities planned for v0.2.0
- Requires Python 3.11
- GitHub token with `repo` scope required

[0.1.0]: https://github.com/ruslanmv/gitpilot/releases/tag/v0.1.0
