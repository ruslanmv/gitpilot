# GitPilot VS Code Extension — Full Documentation

> Version 0.1.5

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [First Use](#first-use)
- [The Sidebar Panel](#the-sidebar-panel)
- [Chat](#chat)
- [Quick Actions](#quick-actions)
- [Code Intelligence](#code-intelligence)
- [Git Features](#git-features)
- [AI Providers](#ai-providers)
- [Architecture](#architecture)
- [Commands Reference](#commands-reference)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Debugging the Extension](#debugging-the-extension)
- [FAQ](#faq)

---

## Overview

GitPilot adds an AI-powered sidebar to VS Code. You can:

- Ask questions about your code in plain English
- Get AI-generated plans before any changes are made
- Review proposed edits in VS Code's native diff viewer
- Apply or revert changes with one click
- Run security scans across your workspace
- Generate tests, fix bugs, and review code

GitPilot works with 5 AI providers: OpenAI, Claude, Ollama, Watsonx, and OllaBridge. You can switch between them at any time.

---

## Installation

### From VS Code Marketplace

1. Open VS Code
2. Press `Ctrl+Shift+X` (Extensions panel)
3. Search for "GitPilot Workspace"
4. Click Install

### From VSIX file

```bash
cd extensions/vscode
make package
code --install-extension gitpilot-vscode-0.1.5.vsix
```

### From Source (for developers)

```bash
cd extensions/vscode
npm install
make compile
# Press F5 in VS Code to launch the Extension Development Host
```

---

## First Use

After installing, you'll see a GitPilot icon in the left sidebar.

1. **Click it** to open the GitPilot panel
2. **Set up a provider** — click "Provider" in the panel header
3. **Choose your AI** — Ollama (free, local) or OllaBridge (free, cloud) are the easiest
4. **Type a message** — try "Explain this project"
5. **Press Enter** or click Send

The AI reads your project files and responds in the chat.

---

## The Sidebar Panel

The panel has these sections (top to bottom):

### Header
- **Provider status**: shows which AI is connected and the model name
- **Repo info**: current repository and branch
- **Status pills**: workflow mode, connection state, active task

### Task Status
- Current task title and progress bar
- Step counter (Step 2/5)
- Summary of what GitPilot is doing

### Project Overview
- **Context chips**: detected languages, manifests, indexed files
- **Advanced Tools**: collapsible section with quick action buttons

### Chat
- Message history with role avatars (You / GitPilot / System)
- Thinking indicator with elapsed timer
- Code blocks with Copy button
- Textarea for your messages

### Plan / Scope / Changes (shown during tasks)
- **Execution Plan**: step-by-step plan with status markers
- **Files in Scope**: files relevant to the current task
- **Proposed Changes**: list of file modifications with Diff buttons

---

## Chat

### Sending messages

Type in the text area and press **Enter** to send. **Shift+Enter** for a new line.

### Suggestion chips

When the chat is empty, you'll see quick suggestion buttons:
- Explain project
- Review file
- Find bugs
- Write tests

Click any chip to send that prompt automatically.

### What happens when you send a message

1. Your message appears immediately
2. GitPilot shows a thinking indicator with phase labels:
   - "Analyzing your request..." (planning)
   - "Writing response..." (generating)
   - "Reviewing changes..." (reviewing)
   - "Applying changes..." (applying)
3. The response appears with a smooth animation
4. If the task produced a plan, it shows in the Plan section
5. If files were changed, they appear in Proposed Changes

### Stop button

While GitPilot is working, the Send button changes to a red **Stop** button. Click it to cancel the current task.

### Code blocks

Code in responses has a **Copy** button that appears on hover. Click it to copy the code to your clipboard.

---

## Quick Actions

Available from the "Advanced Tools" section or the command palette:

| Action | Command | What it does |
|---|---|---|
| Explain Project | `gitpilot.explain_project` | Summarizes architecture, modules, and key files |
| Review File | `gitpilot.review_file` | Analyzes the open file for bugs and improvements |
| Fix Selection | `gitpilot.fix_selection` | Fixes the currently selected code |
| Generate Tests | `gitpilot.generate_tests` | Creates tests for the selected code |
| Security Scan | `gitpilot.security_scan` | Scans for vulnerabilities (OWASP Top 10) |
| Refresh Index | Click button in panel | Rebuilds the project context cache |

---

## Code Intelligence

### CodeLens

Above every function and class, you'll see clickable hints:

```
  Explain | Review
  function handleLogin(email, password) {
```

Click "Explain" for a plain-English explanation. Click "Review" for a code review.

### Right-click context menu

Select code, right-click, and choose:
- GitPilot: Explain Selection
- GitPilot: Review Selection
- GitPilot: Fix Selection
- GitPilot: Generate Tests for Selection

### Security Diagnostics

Security findings appear in the VS Code **Problems** panel (`Ctrl+Shift+M`) with severity levels and CWE IDs.

---

## Git Features

| Command | What it does |
|---|---|
| `gitpilot.smartCommit` | Generates a commit message from your staged changes |
| `gitpilot.branchManager` | Create, merge, compare, and delete branches |
| `gitpilot.stashManager` | Save, pop, and list stashed changes |
| `gitpilot.conflictResolver` | AI-assisted merge conflict resolution |
| `gitpilot.gitStatus` | Show git status in a friendly format |
| `gitpilot.gitDiffAnalysis` | Analyze your current changes |
| `gitpilot.commitSearch` | Search commit history with natural language |
| `gitpilot.impactAnalysis` | Analyze the impact of changes |
| `gitpilot.naturalLanguageGit` | Run git commands in plain English |

---

## AI Providers

### Switching providers

1. Click "Provider" in the sidebar header
2. Select a provider from the dropdown
3. Enter your API key (if needed)
4. The model is selected automatically

### Provider comparison

| Provider | Cost | Speed | Privacy | Best for |
|---|---|---|---|---|
| **Ollama** | Free | Fast | 100% local | Privacy-focused, offline work |
| **OllaBridge** | Free | Medium | Cloud | Quick setup, no installation |
| **OpenAI** | Paid | Fast | Cloud | Best quality (GPT-4o) |
| **Claude** | Paid | Fast | Cloud | Long context, reasoning |
| **Watsonx** | Paid | Medium | Cloud | Enterprise, IBM ecosystem |

### Ollama setup (recommended for free use)

```bash
# 1. Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull a model
ollama pull llama3

# 3. In VS Code, select Ollama as your provider
# It connects to http://localhost:11434 automatically
```

---

## Architecture

```
VS Code                              Backend Server
+------------------+                 +------------------+
| GitPilot Panel   |   HTTP/REST     | FastAPI          |
| (Sidebar UI)     | <------------>  | /api/chat/send   |
|                  |                 | /api/v2/stream   |
| - Chat           |   WebSocket    | /ws/v2/sessions  |
| - Plan view      | <------------>  |                  |
| - Diff preview   |                 | CrewAI Agents    |
| - Apply/Revert   |                 | LLM Providers    |
+------------------+                 +------------------+
```

The extension can also run a **local agent** (in-process) using the built-in
`agentLoop.ts` for offline use with Ollama.

---

## Commands Reference

All 55 commands are available via `Ctrl+Shift+P`:

**Chat**: openChat, sendMessage, newSession, loadSession, deleteSession, refreshSessions

**Code**: reviewFile, reviewSelection, reviewSymbol, explainSelection, explainSymbol, fixSelection, testSelection

**Security**: scanFile, scanWorkspace, clearSecurityDiagnostics, security_scan

**Git**: gitStatus, gitDiffAnalysis, smartCommit, createPR, branchManager, conflictResolver, stashManager, repoHealthCheck, commitSearch, impactAnalysis, naturalLanguageGit

**Setup**: setServer, reconnect, showServerInfo, selectProvider, selectModel, openLlmSettings, setLlmApiKey, setLlmBaseUrl, setPermissionMode, setupWizard, toggleLiteMode

**Advanced**: invokeSkill, installPlugin, uninstallPlugin, refreshSkills, showAgentFlow, selectTopology, runCommand

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` | New line in message |
| `Ctrl+Shift+P` > "GitPilot" | Browse all commands |

---

## Debugging the Extension

### Quick diagnostics

```bash
cd extensions/vscode
make debug-status   # Shows build state, sourcemaps, assets
make debug-check    # 9-point validation
```

### Launch with debugger

```bash
make debug          # Compile + instructions for F5
make debug-watch    # Auto-recompile on save
```

### Inspect the webview

```bash
make debug-webview  # Opens with Chrome DevTools port 9222
```

In the Extension Development Host, run:
`Developer: Open Webview Developer Tools`

### View logs

In VS Code, open the Output panel (`Ctrl+Shift+U`) and select "GitPilot" from the dropdown.

### Profile performance

```bash
make debug-inspect  # Node inspector on port 9229
```

---

## FAQ

**Q: Do I need an API key?**
No. Use Ollama (free, local) or OllaBridge (free, cloud). API keys are only needed for OpenAI, Claude, and Watsonx.

**Q: Does GitPilot send my code to the cloud?**
Only if you choose a cloud provider (OpenAI, Claude, Watsonx). With Ollama, everything stays on your machine.

**Q: What languages does it support?**
Any language. GitPilot reads your files as text. It works best with the languages your chosen LLM knows well (most LLMs handle Python, JavaScript, TypeScript, Java, Go, Rust, C/C++ very well).

**Q: Can it run my tests?**
GitPilot can detect your test framework (jest, pytest, cargo test, go test, etc.) and suggest running tests. Direct test execution is coming in the next release.

**Q: Can I use it offline?**
Yes, with Ollama. Install Ollama, pull a model, and GitPilot works without any internet connection.

**Q: How do I report a bug?**
Open an issue at [github.com/ruslanmv/gitpilot/issues](https://github.com/ruslanmv/gitpilot/issues). Include the output from `make debug-status` and any error messages from the GitPilot output channel.

---

*GitPilot v0.1.5 | MIT License | [github.com/ruslanmv/gitpilot](https://github.com/ruslanmv/gitpilot)*
