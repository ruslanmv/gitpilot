# GitPilot VS Code Extension — Complete Documentation

**Extension:** GitPilot — AI Agent for GitHub
**Version:** 0.2.0
**Publisher:** ruslanmv
**Marketplace:** [gitpilot-vscode](https://marketplace.visualstudio.com/items?itemName=ruslanmv.gitpilot-vscode)
**VS Code Minimum:** 1.85.0

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Installation & Setup](#installation--setup)
4. [Configuration Settings](#configuration-settings)
5. [Features](#features)
   - [Chat Panel](#1-chat-panel)
   - [Code Intelligence](#2-code-intelligence)
   - [Security Scanning](#3-security-scanning)
   - [Git Operations](#4-git-operations)
   - [Agent Flow Viewer](#5-agent-flow-viewer)
   - [Sessions Management](#6-sessions-management)
   - [Skills & Plugins](#7-skills--plugins)
6. [Commands Reference](#commands-reference)
7. [Keyboard Shortcuts](#keyboard-shortcuts)
8. [Context Menus](#context-menus)
9. [Views & UI Components](#views--ui-components)
10. [API Client & Backend Communication](#api-client--backend-communication)
11. [Source Code Structure](#source-code-structure)
12. [Extension Lifecycle](#extension-lifecycle)
13. [Troubleshooting](#troubleshooting)

---

## Overview

The GitPilot VS Code extension brings AI-powered development assistance directly into your editor. It connects to a local GitPilot backend server to provide:

- **AI Chat** with plan generation and execution for repository modifications
- **Code Intelligence** via inline CodeLens hints, quick-fix actions, and explanations
- **Security Scanning** integrated into the VS Code Problems panel
- **11 Advanced Git Operations** including smart commits, PR creation, and conflict resolution
- **Agent Flow Visualization** showing multi-agent topology graphs
- **Session Persistence** for tracking conversations across coding sessions
- **Plugin System** for extensibility via skills and hooks

All code processing happens through your local GitPilot server — no code is sent to third-party services unless you configure an external LLM provider.

---

## Architecture

```
VS Code Extension
├── API Client (api/client.ts)
│   └── HTTP communication with GitPilot backend
│       (health checks, retry logic, auth tokens)
│
├── Views
│   └── chatViewProvider.ts — Webview sidebar chat panel
│
├── Tree Providers
│   ├── sessionsTreeProvider.ts — Session management sidebar
│   └── skillsTreeProvider.ts — Skills & plugins sidebar
│
├── Code Providers
│   ├── codeLensProvider.ts — Inline "Explain" / "Review" above functions
│   ├── codeActionProvider.ts — Quick-fix actions on selected code
│   └── securityDiagnostics.ts — Security findings in Problems panel
│
├── Panels
│   └── agentFlowPanel.ts — Interactive agent topology viewer
│
├── Commands (commands/)
│   ├── chat.ts — Chat & session commands
│   ├── review.ts — Code review & analysis
│   ├── security.ts — Security scanning
│   ├── skills.ts — Skill & plugin management
│   ├── server.ts — Server configuration & topology
│   └── git.ts — Git operations (11 commands)
│
└── Utilities (utils/)
    ├── config.ts — Configuration management
    ├── context.ts — Workspace/Git detection
    └── statusBar.ts — Connection status indicator
```

---

## Installation & Setup

### Prerequisites

- **VS Code** 1.85.0 or later
- **GitPilot backend server** running locally or on a remote host
- **GitHub Personal Access Token** (optional, for repository operations)

### Install from Marketplace

1. Open VS Code
2. Go to Extensions (`Ctrl+Shift+X`)
3. Search for **"GitPilot"**
4. Click **Install**

### Install from VSIX

```bash
code --install-extension gitpilot-vscode-0.2.0.vsix
```

### Start the Backend Server

```bash
# Install GitPilot
pip install gitcopilot

# Start the server
gitpilot
# Server starts at http://127.0.0.1:8000
```

### Connect the Extension

The extension auto-connects to `http://127.0.0.1:8000` by default. To change:

1. Open Command Palette (`Ctrl+Shift+P`)
2. Run **"GitPilot: Set Server URL"**
3. Enter your server URL

Or set it in `settings.json`:

```json
{
  "gitpilot.serverUrl": "http://127.0.0.1:8000"
}
```

---

## Configuration Settings

All settings are under the `gitpilot.*` namespace.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `gitpilot.serverUrl` | string | `http://127.0.0.1:8000` | GitPilot backend server URL |
| `gitpilot.autoConnect` | boolean | `true` | Auto-connect to server on startup |
| `gitpilot.githubToken` | string | `""` | GitHub PAT for repository access |
| `gitpilot.showInlineHints` | boolean | `true` | Show CodeLens hints above functions |
| `gitpilot.permissionMode` | enum | `normal` | Agent permission mode: `normal`, `plan`, or `auto` |
| `gitpilot.defaultTopology` | string | `default` | Default agent topology to use |
| `gitpilot.showSecurityDiagnostics` | boolean | `true` | Show security findings in Problems panel |
| `gitpilot.scanOnSave` | boolean | `false` | Auto-scan files for security issues on save |
| `gitpilot.chatFontSize` | number | `13` | Chat panel font size (10-24px) |
| `gitpilot.maxChatHistory` | number | `100` | Max messages to keep in chat (10-1000) |

### Permission Modes

| Mode | Behavior |
|------|----------|
| `normal` | Agent asks before making changes (recommended) |
| `plan` | Read-only — agent can only generate plans, not execute |
| `auto` | Agent executes without confirmation (use with caution) |

### GitHub Token Resolution

The extension resolves the GitHub token in this order:
1. `gitpilot.githubToken` setting
2. `GITPILOT_GITHUB_TOKEN` environment variable
3. `GITHUB_TOKEN` environment variable

---

## Features

### 1. Chat Panel

The main interface for interacting with GitPilot's AI agents.

**Location:** GitPilot sidebar (click the rocket icon in the Activity Bar)

**How it works:**
1. Type a message describing what you want to do
2. GitPilot generates a structured **Action Plan** with steps
3. Review the plan and click **"Approve & Execute"** to apply changes
4. Or continue chatting to refine the plan

**Skill invocation:** Type `/skillname` to invoke a registered skill directly.

**Topology selector:** Switch between different agent configurations (e.g., "Feature Builder", "Bug Hunter") from the dropdown in the chat header.

**Message shortcuts:**
- `Enter` — New line
- `Ctrl+Enter` / `Cmd+Enter` — Send message
- File references in responses are clickable and open in the editor

### 2. Code Intelligence

#### CodeLens (Inline Hints)

When `gitpilot.showInlineHints` is enabled, you'll see clickable hints above every function and class:

```
$(rocket) Explain  |  $(shield) Review
function calculateTotal(items) {
    ...
}
```

**Supported languages:** JavaScript, TypeScript, Python, Rust, Go, Java

#### Quick-Fix Actions (Context Menu)

Select any code, right-click, and choose:

| Action | What it does |
|--------|-------------|
| **Explain this code** | Sends selected code to AI for a detailed explanation |
| **Review this code** | Gets an AI code review with suggestions |
| **Fix this code** | Requests AI-generated fixes for issues |
| **Generate tests** | Creates unit tests for the selected code |

All responses appear in the Chat panel.

### 3. Security Scanning

AI-powered security analysis integrated into VS Code's Problems panel.

**Scan a single file:**
- `Ctrl+Shift+S` / `Cmd+Shift+S`, or
- Right-click in editor > "GitPilot: Security Scan Current File"

**Scan entire workspace:**
- Command Palette > "GitPilot: Security Scan Workspace"

**Scan git diff (unstaged changes):**
- Automatically available through the API

**Severity mapping in Problems panel:**

| AI Severity | VS Code Level | Color |
|------------|---------------|-------|
| Critical / High | Error | Red |
| Medium | Warning | Yellow |
| Low | Information | Blue |
| Info | Hint | Gray |

**Auto-scan on save:** Enable `gitpilot.scanOnSave: true` to automatically scan files when you save them.

Each finding includes:
- File path and line number
- Severity and category
- Description of the vulnerability
- CWE reference (when applicable)

### 4. Git Operations

11 AI-powered Git commands accessible from the Command Palette:

| Command | Description |
|---------|-------------|
| **Git Status** | AI-enhanced status with context about changes |
| **Analyze Git Diff** | Deep analysis of current changes with suggestions |
| **Smart Commit** | AI generates a meaningful commit message from your diff |
| **Create Pull Request** | AI-assisted PR creation with title and description |
| **Branch Manager** | Create, switch, and manage branches with AI guidance |
| **Resolve Merge Conflicts** | AI helps understand and resolve conflicts |
| **Stash Manager** | Smart stash operations with descriptions |
| **Repository Health Check** | Analyzes repo for issues (large files, stale branches, etc.) |
| **Semantic Commit Search** | Search commit history using natural language |
| **Impact Analysis** | Analyze the impact of changes across the codebase |
| **Natural Language Git** | Describe what you want in plain English, get git commands |

### 5. Agent Flow Viewer

Interactive visualization of GitPilot's multi-agent architecture.

**Open:** Command Palette > "GitPilot: Show Agent Flow Viewer"

**Features:**
- SVG graph showing agent nodes and connections
- Topology selector dropdown to switch configurations
- Active node highlighting (green border)
- Refresh button for live updates

**Agent topologies** represent different workflows:
- **Feature Builder** — agents collaborate to build new features
- **Bug Hunter** — agents focused on finding and fixing bugs
- **Code Reviewer** — agents specialized in code review
- Custom topologies via server configuration

### 6. Sessions Management

Track and restore chat conversations.

**Location:** GitPilot sidebar > "Sessions" section

**Features:**
- View all active, paused, and completed sessions
- Each session shows: repository name, message count, status, last updated
- Click a session to restore its conversation
- Delete sessions with confirmation dialog

**Commands:**
- **New Chat Session** — Creates a new session for the current workspace repository
- **Load Session** — Click any session in the tree to restore it
- **Delete Session** — Right-click a session > Delete

### 7. Skills & Plugins

Extend GitPilot's capabilities with skills and plugins.

**Location:** GitPilot sidebar > "Skills & Plugins" section

#### Skills
Skills are named actions you can invoke via `/skillname` in the chat or from the tree view.

- Click any skill in the tree to invoke it
- Some skills auto-trigger on certain events

#### Plugins
Plugins bundle multiple skills and hooks together.

**Install a plugin:**
1. Command Palette > "GitPilot: Install Plugin"
2. Enter a Git URL or local path
3. Plugin's skills become available immediately

**Uninstall:** Right-click a plugin in the tree > Uninstall

---

## Commands Reference

### Chat & Sessions

| Command | ID | Description |
|---------|----|-------------|
| Open Chat | `gitpilot.openChat` | Focus the chat panel |
| Send Message | `gitpilot.sendMessage` | Prompt and send a message |
| New Chat Session | `gitpilot.newSession` | Create a new chat session |
| Load Session | `gitpilot.loadSession` | Restore a saved session |
| Delete Session | `gitpilot.deleteSession` | Delete a session |

### Code Intelligence

| Command | ID | Description |
|---------|----|-------------|
| Review Current File | `gitpilot.reviewFile` | AI review of the active file |
| Explain Selection | `gitpilot.explainSelection` | Explain selected code |
| Review Selection | `gitpilot.reviewSelection` | Review selected code |
| Fix Selection | `gitpilot.fixSelection` | Fix issues in selected code |
| Generate Tests | `gitpilot.testSelection` | Generate tests for selection |
| Explain Symbol | `gitpilot.explainSymbol` | Explain via CodeLens (hidden) |
| Review Symbol | `gitpilot.reviewSymbol` | Review via CodeLens (hidden) |

### Security

| Command | ID | Description |
|---------|----|-------------|
| Security Scan File | `gitpilot.scanFile` | Scan the current file |
| Security Scan Workspace | `gitpilot.scanWorkspace` | Scan entire workspace |
| Clear Diagnostics | `gitpilot.clearSecurityDiagnostics` | Clear all findings |

### Git Operations

| Command | ID | Description |
|---------|----|-------------|
| Git Status | `gitpilot.gitStatus` | AI-enhanced git status |
| Analyze Git Diff | `gitpilot.gitDiffAnalysis` | Deep diff analysis |
| Smart Commit | `gitpilot.smartCommit` | AI-generated commit message |
| Create Pull Request | `gitpilot.createPR` | AI-assisted PR creation |
| Branch Manager | `gitpilot.branchManager` | Branch management |
| Resolve Conflicts | `gitpilot.conflictResolver` | AI conflict resolution |
| Stash Manager | `gitpilot.stashManager` | Smart stash operations |
| Repo Health Check | `gitpilot.repoHealthCheck` | Repository analysis |
| Commit Search | `gitpilot.commitSearch` | Semantic commit search |
| Impact Analysis | `gitpilot.impactAnalysis` | Change impact analysis |
| Natural Language Git | `gitpilot.naturalLanguageGit` | Plain English git commands |

### Server & Agents

| Command | ID | Description |
|---------|----|-------------|
| Set Server URL | `gitpilot.setServer` | Configure backend URL |
| Reconnect | `gitpilot.reconnect` | Reconnect to server |
| Show Server Info | `gitpilot.showServerInfo` | Display server status |
| Show Agent Flow | `gitpilot.showAgentFlow` | Open topology viewer |
| Select Topology | `gitpilot.selectTopology` | Switch agent topology |
| Set Permission Mode | `gitpilot.setPermissionMode` | Change permission level |

### Skills & Plugins

| Command | ID | Description |
|---------|----|-------------|
| Invoke Skill | `gitpilot.invokeSkill` | Run a skill by name |
| Install Plugin | `gitpilot.installPlugin` | Install from URL/path |
| Uninstall Plugin | `gitpilot.uninstallPlugin` | Remove a plugin |

### Utility

| Command | ID | Description |
|---------|----|-------------|
| Refresh Sessions | `gitpilot.refreshSessions` | Reload sessions list |
| Refresh Skills | `gitpilot.refreshSkills` | Reload skills list |
| Run Command | `gitpilot.runCommand` | Execute a terminal command |

---

## Keyboard Shortcuts

| Shortcut | Action | Condition |
|----------|--------|-----------|
| `Ctrl+Shift+G` / `Cmd+Shift+G` | Open Chat Panel | Always |
| `Ctrl+Enter` / `Cmd+Enter` | Send Message | Chat view focused |
| `Ctrl+Shift+S` / `Cmd+Shift+S` | Security Scan Current File | Editor focused |

---

## Context Menus

### Editor Right-Click Menu

When right-clicking in the editor (group: `gitpilot`):

| Menu Item | Condition |
|-----------|-----------|
| Explain Selection | Text selected |
| Review Selection | Text selected |
| Fix Selection | Text selected |
| Review File | Always visible |
| Security Scan File | Always visible |

### Editor Title Bar (Top-Right Icons)

| Icon/Action | Condition |
|-------------|-----------|
| Review File | Editor has focus |
| Security Scan | Editor has focus |

### Sidebar Tree Views

**Sessions panel title bar:**
- New Session button
- Refresh button

**Sessions tree item:**
- Delete button (inline, with confirmation)

**Skills panel title bar:**
- Refresh button
- Install Plugin button

**Plugin tree item:**
- Uninstall button (inline, with confirmation)

---

## Views & UI Components

### Activity Bar

The extension adds a **GitPilot** icon to the Activity Bar (left sidebar) containing three views:

| View | Type | Description |
|------|------|-------------|
| **Chat** | Webview | Full chat interface with markdown, plans, and topology selector |
| **Sessions** | Tree View | List of saved chat sessions with status indicators |
| **Skills & Plugins** | Tree View | Two-level hierarchy of available skills and installed plugins |

### Status Bar

A status indicator appears on the left side of the status bar:

| State | Icon | Appearance |
|-------|------|-----------|
| Connected | `$(rocket) GitPilot` | Normal text |
| Disconnected | `$(circle-slash) GitPilot` | Yellow warning background |
| Connecting | `$(sync~spin) GitPilot` | Animated spinner |
| Error | `$(error) GitPilot` | Red error background |

Click the status bar item to open the Chat panel.

### Agent Flow Panel

Opens as a full editor panel (beside the active editor) showing an interactive SVG graph of the current agent topology with nodes, edges, and an active-node indicator.

---

## API Client & Backend Communication

The extension communicates with the GitPilot backend via HTTP REST APIs.

### Connection Management

- **Auto-connect:** Enabled by default (`gitpilot.autoConnect: true`)
- **Health checks:** Periodic polling (every 30 seconds) to detect server state
- **Retry logic:** Automatic retry with exponential backoff (up to 2 retries, 1s/2s delays)
- **Authentication:** Bearer token from GitHub PAT setting or environment variables

### Connection States

```
connected ←→ disconnected
    ↕              ↕
connecting ←→ error
```

State changes propagate to:
- Status bar indicator
- Chat panel connection banner
- Tree view empty states

### API Endpoints

The extension calls the following backend endpoints:

**Chat & Planning:**
```
POST /api/chat/plan        — Generate action plan
POST /api/chat/execute     — Execute approved plan
POST /api/chat/message     — Send simple message
POST /api/chat/route       — Route to topology
```

**Sessions:**
```
GET    /api/sessions       — List all sessions
POST   /api/sessions       — Create new session
GET    /api/sessions/{id}  — Get session with history
DELETE /api/sessions/{id}  — Delete session
```

**Repository:**
```
GET /api/repos                         — List repositories
GET /api/repos/{owner}/{repo}/tree     — Get file tree
GET /api/repos/{owner}/{repo}/file     — Get file content
```

**Security:**
```
POST /api/security/scan-file       — Scan single file
POST /api/security/scan-directory  — Scan directory
POST /api/security/scan-diff       — Scan git diff
```

**Skills & Plugins:**
```
GET    /api/skills              — List skills
POST   /api/skills/invoke       — Execute skill
GET    /api/plugins             — List plugins
POST   /api/plugins/install     — Install plugin
DELETE /api/plugins/{name}      — Uninstall plugin
```

**Agent Flow:**
```
GET  /api/flow/topologies       — List topologies
GET  /api/flow/current          — Get current topology
POST /api/flow/topology/{id}    — Switch topology
```

**Settings & Permissions:**
```
GET /api/settings               — Server configuration
GET /api/settings/models        — Available LLM models
GET /api/permissions            — Current permission mode
PUT /api/permissions/mode       — Update permission mode
```

**Predictions & Hooks:**
```
POST /api/predictions/suggest   — AI suggestions for events
GET  /api/hooks                 — Registered hooks
```

---

## Source Code Structure

```
extensions/vscode/
├── src/
│   ├── extension.ts                 # Main entry point (activation/deactivation)
│   ├── api/
│   │   └── client.ts               # HTTP client with retry, auth, health checks
│   ├── views/
│   │   └── chatViewProvider.ts      # Sidebar chat webview (HTML/CSS/JS generation)
│   ├── tree/
│   │   ├── sessionsTreeProvider.ts  # Sessions tree data provider
│   │   └── skillsTreeProvider.ts    # Skills & plugins tree data provider
│   ├── providers/
│   │   ├── codeLensProvider.ts      # CodeLens for functions/classes
│   │   ├── codeActionProvider.ts    # Quick-fix code actions
│   │   └── securityDiagnostics.ts   # Security findings → Problems panel
│   ├── panels/
│   │   └── agentFlowPanel.ts        # Agent topology SVG viewer
│   ├── commands/
│   │   ├── chat.ts                  # Chat & session commands
│   │   ├── review.ts               # Code review & explanation commands
│   │   ├── security.ts             # Security scan commands
│   │   ├── skills.ts               # Skill & plugin management
│   │   ├── server.ts               # Server config & topology commands
│   │   └── git.ts                  # 11 git operation commands
│   └── utils/
│       ├── config.ts               # Settings reader (token chain, URLs)
│       ├── context.ts              # Git repo detection (owner, repo, branch)
│       └── statusBar.ts            # Status bar state management
├── resources/
│   ├── icon.svg                    # Activity bar icon
│   └── icon.png                    # Marketplace icon
├── package.json                    # Extension manifest (commands, settings, views)
├── tsconfig.json                   # TypeScript configuration
├── CHANGELOG.md                    # Version history
├── README.md                       # Marketplace README
├── LICENSE                         # MIT License
├── .vscodeignore                   # Files excluded from package
└── .gitignore                      # Files excluded from git
```

### Key File Sizes

| File | Lines | Role |
|------|-------|------|
| `chatViewProvider.ts` | ~558 | Largest — full webview with HTML/CSS/JS |
| `client.ts` | ~372 | API client with all endpoint methods |
| `agentFlowPanel.ts` | ~297 | SVG graph rendering |
| `git.ts` | ~158 | 11 git command implementations |
| `extension.ts` | ~142 | Activation and registration |
| `server.ts` | ~139 | Server & topology management |
| `securityDiagnostics.ts` | ~107 | Security → diagnostics mapping |
| `review.ts` | ~95 | Code review command handlers |
| `skills.ts` | ~90 | Skill & plugin commands |

---

## Extension Lifecycle

### Activation

The extension activates on `onStartupFinished` (after VS Code is fully loaded).

**Initialization sequence:**
1. Load configuration from VS Code settings
2. Create `GitPilotApiClient` instance with server URL and token
3. Create `StatusBarManager` (shows connection state)
4. Register `ChatViewProvider` as webview sidebar
5. Register `SessionsTreeProvider` and `SkillsTreeProvider`
6. Register `CodeLensProvider` for all file types
7. Register `CodeActionProvider` for all file types
8. Register all 40+ commands from each command module
9. Set up `onDidChangeConfiguration` listener for live setting updates
10. Set up `onDidSaveTextDocument` listener (if `scanOnSave` enabled)
11. Auto-connect to server (if `autoConnect` enabled)

### Deactivation

The `deactivate()` function performs no special cleanup — all disposables are registered via `context.subscriptions` and cleaned up automatically by VS Code.

---

## Troubleshooting

### Extension Not Connecting

1. **Verify the server is running:**
   ```bash
   curl http://127.0.0.1:8000/api/health
   ```

2. **Check the server URL setting:**
   ```json
   "gitpilot.serverUrl": "http://127.0.0.1:8000"
   ```

3. **Try manual reconnect:**
   Command Palette > "GitPilot: Reconnect to Server"

4. **Check server info:**
   Command Palette > "GitPilot: Show Server Info"

### CodeLens Not Showing

- Ensure `gitpilot.showInlineHints` is `true`
- CodeLens only appears on function/class definitions
- Supported: JavaScript, TypeScript, Python, Rust, Go, Java

### Security Findings Not Appearing

- Ensure `gitpilot.showSecurityDiagnostics` is `true`
- Run a scan manually first: `Ctrl+Shift+S`
- Check the Problems panel (`Ctrl+Shift+M`)

### Chat Not Responding

- Check the status bar indicator for connection state
- Verify the server is healthy (green rocket icon)
- Try creating a new session: Command Palette > "GitPilot: New Chat Session"

### GitHub Token Issues

Set your token via one of:
```json
// settings.json
"gitpilot.githubToken": "ghp_your_token_here"
```
Or environment variable:
```bash
export GITPILOT_GITHUB_TOKEN="ghp_your_token_here"
```

---

## Built-in Walkthrough

The extension includes a **"Get Started with GitPilot"** walkthrough accessible from the VS Code Welcome tab:

1. **Start the GitPilot Server** — Install and launch the backend
2. **Connect to Server** — Configure the server URL
3. **Open Chat Panel** — Launch chat with `Ctrl+Shift+G`
4. **Try Code Review** — Select code and explain it
5. **Run a Security Scan** — Scan your workspace
6. **Explore Agent Topologies** — View the agent flow graph

---

## License

MIT License. See [LICENSE](./LICENSE) for details.
