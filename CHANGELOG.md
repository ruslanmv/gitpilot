# Changelog

All notable changes to GitPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
