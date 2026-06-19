# Changelog

All notable changes to GitPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Coder API** (`POST /repair` + `GET /repair/health`), bearer-token gated (`GITPILOT_API_TOKEN`), mounted into the main app — turns a repair-plan into a dry-run patch preview for SelfRepair / matrix-maintainer over HTTPS.
- **Runtime-aware onboarding** — `/api/status` now reports `workspace.runtime` (`cloud`/`local`); in a cloud workspace the empty state guides you to connect GitHub and pick a repository, while a local install offers Folder / Local Git paths with GitHub optional.
- **Account-first auth across split deployments** — portable `X-GitPilot-Session` token (cross-origin Vercel↔HF), a dedicated email-verification screen, a Settings → Account tab (update name, change password, delete account), and "GitHub not linked" now shows a calm Connect prompt instead of a repo-fetch error.

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
