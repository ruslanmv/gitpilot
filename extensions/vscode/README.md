# GitPilot — AI Agent for GitHub

> Enterprise agentic AI assistant for GitHub repositories inside VS Code. Multi-agent orchestration, security scanning, MCP support, and multi-model AI powered by CrewAI.

## Features

### Chat with AI Agents
- Full sidebar chat panel with markdown rendering
- Action plan display with **Approve / Reject** workflow
- Skill invocation via `/command` syntax
- Persistent session management

### Code Intelligence
- **Explain** — Select code and get AI explanations
- **Review** — AI-powered code review with actionable feedback
- **Fix** — Automatic bug detection and fix suggestions
- **Generate Tests** — AI-generated unit tests for selected code
- **CodeLens** — Inline "Explain / Review" hints above functions and classes

### Security Scanner
- AI-powered vulnerability scanning (OWASP Top 10)
- Findings appear in the VS Code **Problems** panel
- Scan on save (optional) or scan on demand
- CWE-mapped findings with severity levels

### Git Operations
- **Smart Commit** — AI-generated conventional commit messages
- **Branch Manager** — Create, merge, compare, delete with safety checks
- **Stash Manager** — Save, pop, and list stashed changes
- **Conflict Resolver** — AI-assisted merge conflict resolution
- **Natural Language Git** — Describe operations in plain English
- **Semantic Commit Search** — Find commits by meaning, not keywords
- **Repository Health Check** — Comprehensive repo analysis
- **Impact Analysis** — Understand cross-file dependencies

### Agent Flow Viewer
- Interactive visualization of multi-agent topologies
- 7 switchable workflows: Feature Builder, Bug Hunter, Code Inspector, and more
- Real-time active node highlighting

### Enterprise Features
- **Permission Modes** — Normal, Plan-Only (read-only), Auto
- **Plugin System** — Install skills, hooks, and MCP servers from Git URLs
- **Multi-LLM Support** — OpenAI, Claude, IBM Watsonx, Ollama
- **Privacy-First** — All processing via your local GitPilot server

## Quick Start

### 1. Install GitPilot Server

```bash
pip install gitcopilot
```

### 2. Start the Server

```bash
gitpilot serve
```

### 3. Open VS Code

The extension auto-connects to `http://127.0.0.1:8000` on startup.

Press `Ctrl+Shift+G` (or `Cmd+Shift+G` on Mac) to open the chat panel.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `gitpilot.serverUrl` | `http://127.0.0.1:8000` | GitPilot API server URL |
| `gitpilot.autoConnect` | `true` | Auto-connect on startup |
| `gitpilot.githubToken` | — | GitHub PAT (or use env vars) |
| `gitpilot.showInlineHints` | `true` | Show CodeLens above functions |
| `gitpilot.permissionMode` | `normal` | Agent permission mode |
| `gitpilot.showSecurityDiagnostics` | `true` | Security findings in Problems panel |
| `gitpilot.scanOnSave` | `false` | Auto-scan on file save |
| `gitpilot.chatFontSize` | `13` | Chat panel font size |

## Commands

Open the Command Palette (`Ctrl+Shift+P`) and type "GitPilot" to see all 38+ commands:

**Chat & Sessions:** Open Chat, Send Message, New Session, Load Session

**Code Intelligence:** Explain Selection, Review File, Fix Selection, Generate Tests

**Security:** Scan File, Scan Workspace, Clear Diagnostics

**Git Operations:** Smart Commit, Branch Manager, Stash Manager, Conflict Resolver, Natural Language Git, Commit Search, Health Check, Impact Analysis, Create PR

**Agent System:** Show Agent Flow, Select Topology, Invoke Skill, Install Plugin

**Configuration:** Set Server URL, Reconnect, Server Info, Permission Mode

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+G` / `Cmd+Shift+G` | Open Chat |
| `Ctrl+Enter` / `Cmd+Enter` | Send Message (when chat focused) |
| `Ctrl+Shift+S` / `Cmd+Shift+S` | Security Scan Current File |

## Requirements

- **GitPilot server** running locally or on a remote host
- Install via: `pip install gitcopilot`
- At least one LLM provider configured (OpenAI, Claude, Watsonx, or Ollama)

## Privacy & Security

GitPilot processes everything through your own server. No code or data is sent to third-party services beyond your configured LLM provider. All Git operations run locally on your machine.

## License

MIT — see [LICENSE](https://github.com/ruslanmv/gitpilot/blob/master/LICENSE) for details.
