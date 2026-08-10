<div align="center">

# GitPilot for VS Code

### Not a chatbot. A team of AI agents that code together.

Most AI coding tools are a single model guessing at your codebase. GitPilot is different: **ten specialized agents**, coordinated by a router that picks the right ones for each request — one explores your repo, one plans safe changes, one writes the code and runs your tests, one reviews the result, and others handle issues, pull requests, search and your local workspace. You approve every step.

**Open source · Multi-agent · Any LLM · Free with Ollama · Enterprise-ready**

</div>

### How is this different from Copilot, Cursor, or Cody?

| | Single-model tools | **GitPilot** |
|---|---|---|
| Architecture | One model, one prompt | **10 specialized agents** with a request router |
| Context | Reads the open file | **Explores** your full repo, git history, tests |
| Safety | Suggests code inline | **Plans first**, shows diffs, waits for approval |
| Testing | You run tests manually | **Runs your test suite** and self-corrects on failure |
| Lock-in | One vendor | **Any LLM**: OpenAI, Claude, Ollama, Watsonx, OllaBridge |
| Privacy | Cloud-only | **Fully local** with Ollama — nothing leaves your machine |
| Source | Proprietary | **Apache 2.0 open source** — fork it, audit it, extend it |

---

## Quick Start

```bash
pip install gitcopilot      # install the backend (one-time)
gitpilot serve              # start the AI server locally
```

Then in VS Code:

1. **Install** "GitPilot" from the Extensions Marketplace
2. **Click** the GitPilot icon in the sidebar
3. **Choose** your AI provider — `GitPilot: Settings` → **AI Providers** (OllaBridge and Ollama are free, no API key)
4. **Ask** anything: *"Explain this project"*, *"Fix the bug in login.ts"*, *"Write tests for auth"*

That's it. No account required. No data leaves your machine unless you choose a cloud provider.

> **Requirements**: Python 3.11 or 3.12 · VS Code 1.110+ · The PyPI package is **`gitcopilot`** (the CLI command is `gitpilot`)

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

All provider setup happens inside VS Code — no browser, no config files.

Run **`GitPilot: Settings`** from the command palette and open **AI
Providers**. You get an overview of the server connection, the active
provider, and the rest; clicking one opens its configuration page.

| Provider | What you need | Free? |
|---|---|---|
| **OllaBridge Cloud** | Nothing — sign in with your browser for more models | Yes |
| **Ollama** | Ollama installed locally (`ollama pull llama3`) | Yes |
| **Open WebUI** | Your instance URL | Yes (self-hosted) |
| **OpenAI** | API key from [platform.openai.com](https://platform.openai.com/api-keys) | Paid |
| **Claude** | API key from [console.anthropic.com](https://console.anthropic.com/settings/keys) | Paid |
| **IBM watsonx** | API key **and** project ID from [cloud.ibm.com](https://cloud.ibm.com/iam/apikeys) | Paid |
| **Custom endpoint** | Any OpenAI-compatible URL, key, and request headers | Depends |

A provider becomes active only when you press **Save and activate**.

**API keys are stored by the GitPilot backend, never in VS Code settings.**
The settings page is told only that a key exists, plus its last four
characters (`••••A7X2`). An empty key field means "keep the current key", so
you can change a model without re-entering the secret.

> **Claude:** a Claude.ai subscription does not include API access. You need
> an Anthropic API key, billed separately.

**Full guide:** [AI provider setup](https://github.com/ruslanmv/gitpilot/blob/master/docs/vscode/ai-providers.md)

---

## Agent Topologies

A **topology** decides which of the ten agents run, and in what order.

Open **`GitPilot: Settings`** → **Agent** → *Agent topology*. Each preset is a
card showing its full sequence:

| Topology | Sequence |
|---|---|
| **Automatic** (recommended) | Routed per request |
| 🚀 **Feature Builder** | Explorer → Planner → Coder → Reviewer → PR Manager |
| 🐛 **Bug Hunter** | Explorer → Coder → Reviewer → PR Manager |
| 🔍 **Code Inspector** | Explorer → Reviewer (read-only) |
| 📐 **Architect Mode** | Explorer → Planner (read-only) |
| ⚡ **Quick Fix** | Coder → PR Manager |

The default routed flow runs Explorer → Planner → Coder. To have **every**
change reviewed, or a PR opened automatically, pin a topology that includes
those stages.

Topology (which agents run) and permission mode (what they may do unattended)
compose — Feature Builder in **Plan** mode gives you the full plan and review
with all writes blocked.

**Full guide:** [Agent topologies](https://github.com/ruslanmv/gitpilot/blob/master/docs/vscode/agent-topologies.md)

---

## MCP Servers

MCP servers extend what the agents can do. Attach a PostgreSQL server and the
Explorer reads your live schema, the Coder writes queries against real tables,
and the Reviewer validates a migration before it lands.

Open **`GitPilot: Settings`** → **MCP Servers**.

- **[Install MCP Context Forge]** — one click. GitPilot checks Docker, starts
  the gateway, waits for it, and points itself at it. No terminal, no compose
  file.
- **Attach** a server from the bundled catalogue (PostgreSQL, Milvus,
  Inspector, GitPilot's own), or **Search** a registry —
  [MatrixHub](https://matrixhub.io) by default — for anything else.
- Each server has its own page listing every tool, its risk, and which agents
  call it. Toggle tools individually.

Two safety properties worth knowing:

- **Attaching is not enabling.** A newly attached server arrives disabled, so
  gaining a capability is never a side effect of browsing a catalogue.
- **Tokens never enter the editor.** GitPilot asks for the *name* of an
  environment variable and reads its value on the GitPilot host. Destructive
  tools (`drop`, `delete`, `truncate`) are off by default and ask before being
  enabled.

**Full guide:** [MCP servers](https://github.com/ruslanmv/gitpilot/blob/master/docs/vscode/mcp-servers.md)

---

## Settings

Open settings: `Ctrl+Shift+P` > "Preferences: Open Settings" > search "gitpilot"

| Setting | Default | What it does |
|---|---|---|
| `gitpilot.serverUrl` | `http://127.0.0.1:8000` | Where the GitPilot backend is |
| `gitpilot.serverCommand` | `gitpilot` | Command used to start a local server from the settings page |
| `gitpilot.autoConnect` | `true` | Connect to server on startup |
| `gitpilot.permissionMode` | `normal` | `normal` (ask), `auto`, or `plan` (read-only) |
| `gitpilot.showInlineHints` | `true` | Show Explain/Review CodeLens hints |
| `gitpilot.liteMode` | `false` | Simplified prompts for models under ~7B parameters |
| `gitpilot.mcp.gatewayUrl` | `http://localhost:4444` | MCP Context Forge address |
| `gitpilot.mcp.forgePort` | `4444` | Port used when GitPilot starts Forge for you |
| `gitpilot.mcp.forgeImage` | `ghcr.io/ibm/mcp-context-forge:latest` | Image used when the workspace has no compose file |

Provider credentials are deliberately absent: they live on the GitPilot
server, so they are never written to `settings.json` or synced by Settings
Sync.

---

## Troubleshooting

**"Provider not configured"**
Run `GitPilot: Settings` → **AI Providers** and pick one. OllaBridge and
Ollama need no API key.

**"Disconnected"**
GitPilot needs a backend server running at `http://127.0.0.1:8000`. The
settings page can start one for you — **AI Providers** → **Start local
server** — or do it yourself:
```bash
pip install gitcopilot
gitpilot serve
```
The PyPI package is **`gitcopilot`** (the CLI is `gitpilot`). Requires Python 3.11 or 3.12.
If `gitpilot` is not on your `PATH`, set `gitpilot.serverCommand` to its full path.

**"Version mismatch"**
This VS Code extension (v0.2.x) requires **GitPilot backend v0.2.x** (`gitcopilot>=0.2.6`).
Upgrade both together: `pip install --upgrade 'gitcopilot>=0.2.6'`.

**Extension not loading?**
Open the Output panel (`Ctrl+Shift+U`) and select "GitPilot" from the dropdown to see logs.

---

## Links

- [GitHub](https://github.com/ruslanmv/gitpilot)
- [Report a Bug](https://github.com/ruslanmv/gitpilot/issues)
- [Full Documentation](https://github.com/ruslanmv/gitpilot/blob/main/extensions/vscode/EXTENSION_DOCS.md)

---

**Made by [Ruslan Magana Vsevolodovna](https://github.com/ruslanmv)** | Apache 2.0 | v0.2.8
