<div align="center">

<img src="https://raw.githubusercontent.com/ruslanmv/gitpilot/main/docs/logo.svg" alt="GitPilot" width="80" />

# GitPilot for VS Code

**AI coding assistant right in your sidebar. Ask questions. Get code. Ship faster.**

</div>

---

## Quick Start

1. **Install** the extension from the VS Code Marketplace
2. **Click** the GitPilot icon in the left sidebar
3. **Choose** your AI provider (Ollama is free and local)
4. **Ask** anything: "Explain this project", "Fix the bug in login.ts", "Write tests"

That's it. No account needed. No server to run (unless you want the web app too).

---

## What Can GitPilot Do?

### Chat

Type a question or request in the chat panel. GitPilot reads your project, creates a plan, and writes the code.

**Try these:**
- "Explain this project's architecture"
- "Review the current file for issues"
- "Add error handling to the API endpoints"
- "Write tests for the auth module"

### Quick Actions

One-click buttons in the sidebar:

| Button | What it does |
|---|---|
| **Explain Project** | Summarizes your entire codebase |
| **Review File** | Finds bugs and improvements in the open file |
| **Fix Selection** | Fixes the selected code |
| **Generate Tests** | Creates tests for the selected code |
| **Security Scan** | Checks for vulnerabilities |

### Code Intelligence

- **CodeLens** hints appear above functions: click "Explain" or "Review"
- **Right-click menu** on selected code: Explain, Review, Fix, Test
- **Command Palette** (`Ctrl+Shift+P`): search "GitPilot"

### Git

- **Smart Commit**: generates commit messages from your changes
- **Branch Manager**: create, merge, compare branches
- **Conflict Resolver**: AI-assisted merge conflict resolution

---

## Setup Your AI Provider

GitPilot works with any of these providers:

### Free (no API key needed)

**Ollama** (local, private, fast):
```
1. Install Ollama: https://ollama.com
2. Run: ollama pull llama3
3. In GitPilot: click Provider > Ollama
```

**OllaBridge** (cloud, works out of the box):
```
1. In GitPilot: click Provider > OllaBridge
2. It connects automatically (no setup needed)
```

### Paid (API key required)

**OpenAI**:
```
1. Get an API key from https://platform.openai.com
2. In GitPilot: click Provider > OpenAI
3. Paste your API key
```

**Claude (Anthropic)**:
```
1. Get an API key from https://console.anthropic.com
2. In GitPilot: click Provider > Claude
3. Paste your API key
```

**IBM Watsonx**:
```
1. Get credentials from https://cloud.ibm.com
2. In GitPilot: click Provider > Watsonx
3. Add your API key and project ID
```

---

## Settings

Open settings: `Ctrl+Shift+P` > "Preferences: Open Settings" > search "gitpilot"

| Setting | Default | What it does |
|---|---|---|
| `gitpilot.provider` | `ollabridge` | Which AI to use |
| `gitpilot.codeLens.enabled` | `true` | Show Explain/Review hints |
| `gitpilot.autoConnect` | `true` | Connect to server on startup |

---

## Troubleshooting

**"Provider not configured"**
Click the "Provider" button in the sidebar header and select your AI provider.

**"Disconnected"**
GitPilot needs a backend server. Either:
- Use the built-in local provider (Ollama / OllaBridge)
- Or start the server: `pip install gitpilot && gitpilot serve`

**Extension not loading?**
Open the Output panel (`Ctrl+Shift+U`) and select "GitPilot" from the dropdown to see logs.

---

## Links

- [GitHub](https://github.com/ruslanmv/gitpilot)
- [Report a Bug](https://github.com/ruslanmv/gitpilot/issues)
- [Full Documentation](https://github.com/ruslanmv/gitpilot/blob/main/extensions/vscode/EXTENSION_DOCS.md)

---

**Made by [Ruslan Magana Vsevolodovna](https://github.com/ruslanmv)** | MIT License | v0.1.5
