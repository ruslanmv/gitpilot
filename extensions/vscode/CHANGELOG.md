# Changelog

## [0.2.8] - 2026-08-07

### Added
- **Navigation sidebar.** A new GitPilot view at the top of the sidebar holds a
  one-line status, **New Chat**, recent sessions and quick actions — and
  nothing else. The conversation stays in the workspace panel below it, so the
  three zones each have one job: sidebar to find the task, editor to work on
  the code, chat to work with GitPilot.
  - Sessions are rows, not cards, with the active one marked by an accent bar.
  - Clicking a session opens it in one action rather than select-then-open.
  - The overflow menu appears on hover, so a list of ten reads as a list.
  - Recent sits above Quick Actions: once GitPilot is in daily use, resuming
    work is more frequent than starting a canned action.
  - When the server is down the sidebar offers Start server / Reconnect in
    place, rather than going blank.

### Changed
- **The conversation reads as text, not a stack of cards.** Boxing every turn
  made a long conversation visually exhausting; cards are now reserved for
  things you can act on — a suggested change, an approval, a tool-activity
  group. The role is carried by a 2px rule instead of a border-and-fill.
- **The transcript uses the height it has.** It was capped at a fixed 380px,
  which wasted a tall panel and cramped a short one.
- **Modes read Ask / Plan / Agent**, least to most permission, each saying what
  it will and will not do. This is a relabelling only: the stored value stays
  `auto`, and Ask remains the default.

  All nine of the panel's animations are intact.
- **AI Providers settings page** — configure every provider from inside VS Code.
  An overview lists the server connection, the active provider and the rest;
  clicking one opens its own configuration page. No browser step, no config files.
- **Open WebUI** and a generic **custom OpenAI-compatible endpoint** as first-class
  providers. The custom endpoint carries arbitrary request headers, which gateways
  need for attribution and routing, and discovers models from a published catalogue
  when the endpoint serves one.
- **OllaBridge sign-in** by browser device pairing, alongside API-key and
  self-hosted gateway modes. GitPilot never asks for an account password.
- **Agent topology picker** (Settings → Agent). Presets render as cards showing
  their full agent sequence, and the choice is saved to the GitPilot backend —
  which is what actually routes work.
- **Start local server** from the settings page when the backend is not running.
  Runs `gitpilot serve --no-open` and follows the port it actually binds.
  Configurable via `gitpilot.serverCommand`.
- `GitPilot: Settings` command.

- **MCP Servers settings page** — attach Model Context Protocol servers to give
  the agents extra tools. An overview lists the gateway, what is attached and
  what is on offer; each server has its own page showing every tool, its risk,
  and which agents call it. A newly attached server arrives disabled, and
  enabling a destructive tool asks first.
- **One-click MCP Context Forge install.** When no gateway is reachable the page
  offers a button that checks Docker, starts Forge, waits for it and points
  GitPilot at it — using the project's compose stack in a checkout, or the
  published image otherwise. Configurable via `gitpilot.mcp.forgePort` and
  `gitpilot.mcp.forgeImage`.
- **MCP registry search.** Search a remote registry (MatrixHub by default,
  `GITPILOT_MATRIXHUB_URL` to change it) for servers beyond the bundled four,
  and attach what you find.

### Fixed
- **"New Chat" did not produce a new chat.** The session commands were wired to
  the legacy chat provider, which is no longer registered as a view, so a new
  session was created on the backend while the visible conversation stayed
  exactly as it was. Clicking a saved session did even less — it called a
  method on a webview that does not exist. Both now clear the transcript and
  the task state, then start or resume through the coordinator the panel
  actually reads. The clear happens before the round-trip, so the panel goes
  empty on click rather than after the network.
- **The bundled MCP catalogue was empty on every `pip install`.** It was read
  from `extensions/mcp_plugins/`, which is not packaged; the manifests now ship
  inside the wheel, with the repo directory kept as a developer fallback.
- **"Not connected to GitPilot server"** no longer appears when the server is
  running. The connection was read from a flag refreshed on a 30-second timer;
  it is now re-probed.
- **The provider dropdown had no effect.** The webview sent a provider value that
  the settings handler never read. Provider selection is now an explicit
  *Save and activate* that writes through the backend API.
- **Open Admin Panel** opened nothing — it ran `showServerInfo`. The integrated
  settings page replaces it; the browser admin remains under *Advanced*.
- **Request timeouts.** `/api/status` could take ~16 seconds with no deadline.
  Provider pages no longer call it, and every request now has one (health 3s,
  settings 10s, models 15s, provider test 30s).
- **Model discovery** is lazy, scoped to the provider being configured, and cached
  for 60 seconds. Replies carry request ids so a late response cannot overwrite a
  newer selection.
- **`gitpilot.defaultTopology`** was written to VS Code settings only, where nothing
  read it. Topology selection now persists to the backend.

### Changed
- API keys are stored by the GitPilot backend and never returned to the settings
  page — it receives only a boolean and a masked tail (`••••A7X2`). An empty key
  field means "keep the current key"; clearing one is a separate confirmed action.
- Documentation corrected: GitPilot dispatches to **ten** specialized agents, not
  four, and the default plan-and-execute flow runs three of them
  (Explorer → Planner → Coder). A review or PR stage is added by selecting a
  topology that includes one.

## [0.2.0] - 2026-03-24

### Added
- **Full Chat Webview** — Rich sidebar chat panel with markdown rendering, action plan display, and approve/reject workflow
- **Sessions Tree View** — Browse, create, resume, and delete persistent chat sessions
- **Skills & Plugins Tree View** — Discover, invoke skills and manage installed plugins
- **Security Diagnostics** — AI-powered vulnerability scanning integrated into VS Code Problems panel
- **CodeLens Provider** — Inline "Explain / Review" hints above functions and classes
- **Code Action Provider** — Quick-fix actions for Explain, Review, Fix, and Generate Tests
- **Agent Flow Viewer** — Interactive panel showing multi-agent topology as a node graph
- **Git Operations Suite**:
  - Smart Commit with AI-generated messages
  - Branch Manager (create, merge, compare, delete with safety checks)
  - Stash Manager
  - Merge Conflict Resolver
  - Semantic Commit Search
  - Repository Health Check
  - Impact Analysis
  - Natural Language Git commands
- **Enterprise Configuration** — 10 configurable settings including permission modes, security scanning, and font size
- **Status Bar** — Connection state indicator with auto-reconnect
- **Context Menus** — Right-click Explain, Review, Fix, Scan on selected code
- **Getting Started Walkthrough** — 6-step onboarding guide for new users
- **Safety-First Design** — Confirmations for destructive operations, dry-run previews, plan-before-execute workflow

### Changed
- Bumped version from 0.1.0 to 0.2.0
- Expanded from 7 to 38+ commands
- Full modular TypeScript architecture (api/, views/, tree/, providers/, commands/, panels/, utils/)

## [0.1.0] - Initial Release

### Added
- Basic chat sidebar stub
- Server URL configuration
- Skill invocation via quick pick
- Status bar item
- Context menu for Explain Selection and Review File
