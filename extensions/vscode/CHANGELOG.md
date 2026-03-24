# Changelog

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
