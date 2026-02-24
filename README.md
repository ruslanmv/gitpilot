# GitPilot

<div align="center">

**🚀 The AI Coding Companion That Understands Your GitHub Repositories**

[![PyPI version](https://badge.fury.io/py/gitcopilot.svg)](https://pypi.org/project/gitcopilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/ruslanmv/gitpilot.svg?style=social&label=Star)](https://github.com/ruslanmv/gitpilot)

[Installation](#-installation) • [Quick Start](#-quick-start) • [Example Usage](#-example-usage) • [Documentation](#-complete-workflow-guide) • [Contributing](#-contributing)

</div>

---

## ⭐ Star Us on GitHub!

**If GitPilot saves you time or helps your projects, please give us a star!** ⭐

Your support helps us:
- 🚀 Build new features faster
- 🐛 Fix bugs and improve stability
- 📚 Create better documentation
- 🌍 Grow the community

**[⭐ Click here to star GitPilot on GitHub](https://github.com/ruslanmv/gitpilot)** — it takes just 2 seconds and means the world to us! 💙

---

## 🌟 What is GitPilot?

GitPilot is a **production-ready agentic AI assistant** that acts as your intelligent coding companion for GitHub repositories. Unlike copy-paste coding assistants, GitPilot:

* **🧠 Understands your entire codebase** – Analyzes project structure, file relationships, and cross-repository dependencies
* **📋 Shows clear plans before executing** – Always presents an "Answer + Action Plan" with structured file operations (CREATE/MODIFY/DELETE/READ)
* **🔄 Manages multiple LLM providers** – Seamlessly switch between OpenAI, Claude, Watsonx, and Ollama with smart model routing
* **👁️ Visualizes agent workflows** – See exactly how the multi-agent system thinks and operates
* **🔗 Works locally and with GitHub** – Full local file editing, terminal execution, and GitHub integration
* **🔌 Extensible ecosystem** – MCP server connections, plugin marketplace, /command skills, and IDE extensions
* **🛡️ Built-in security scanning** – AI-powered vulnerability detection beyond traditional SAST tools
* **🤖 Self-improving agents** – Learns from outcomes and adapts strategies per project over time

**Built with CrewAI, FastAPI, and React** — GitPilot combines the power of multi-agent AI with a beautiful, modern web interface and the extensibility of a platform.

![](assets/2025-11-15-01-18-56.png)

---

## ✨ Example Usage

### Installation

```bash
# Install from PyPI
pip install gitcopilot

# Set your GitHub token
export GITPILOT_GITHUB_TOKEN="ghp_your_token_here"

# Set your LLM API key (choose one)
export OPENAI_API_KEY="sk-..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."
# or
export WATSONX_API_KEY="your_api_key"
export WATSONX_PROJECT_ID="your_project_id"

# Launch GitPilot
gitpilot
```

### Basic Workflow

```bash
# 1. Start GitPilot (opens in browser automatically)
gitpilot

# 2. Select a repository from the sidebar
#    → Search your GitHub repos
#    → Click to connect

# 3. Ask GitPilot to make changes
#    Example: "Add a new API endpoint for user login with JWT authentication"

# 4. Review the generated plan
#    ✓ Answer: Explanation of what will be done
#    ✓ Action Plan: Structured steps with file operations
#      - CREATE: src/api/auth.py
#      - MODIFY: src/routes/index.py
#      - READ: README.md (for analysis)
#      - DELETE: deprecated/old_auth.py

# 5. Approve & Execute
#    → GitPilot applies changes to your repository
#    → View execution log with detailed results
```

### Real-World Examples

**Add a new feature:**
```
User: Add a rate limiter to all API endpoints to prevent abuse

GitPilot Response:
Answer: I'll implement rate limiting using a middleware approach with Redis
for distributed rate limiting support.

Action Plan:
  Step 1: Create rate limiter middleware
    - CREATE: src/middleware/rate_limiter.py
  Step 2: Add Redis client configuration
    - MODIFY: src/config.py
  Step 3: Apply middleware to all routes
    - MODIFY: src/main.py
  Step 4: Add rate limit tests
    - CREATE: tests/test_rate_limiter.py
```

**Refactor existing code:**
```
User: Refactor the user authentication to use async/await instead of callbacks

GitPilot Response:
Answer: I'll convert all authentication functions to async/await pattern,
update the database queries, and ensure all tests pass.

Action Plan:
  Step 1: Analyze current authentication implementation
    - READ: src/services/auth_service.py
    - READ: src/routes/auth.py
  Step 2: Convert auth service to async
    - MODIFY: src/services/auth_service.py (12 functions)
  Step 3: Update route handlers
    - MODIFY: src/routes/auth.py
  Step 4: Migrate database calls
    - MODIFY: src/db/user_repository.py
  Step 5: Update unit tests
    - MODIFY: tests/test_auth_service.py
```

**Fix a bug:**
```
User: Fix the bug where empty email validation returns a 500 error instead of 400

GitPilot Response:
Answer: I'll add proper input validation with Pydantic models and return
appropriate HTTP 400 errors with helpful messages.

Action Plan:
  Step 1: Add Pydantic validation model
    - CREATE: src/models/user_input.py
  Step 2: Update login endpoint with validation
    - MODIFY: src/routes/auth.py
  Step 3: Add validation error handler
    - MODIFY: src/main.py
  Step 4: Add test cases for validation
    - MODIFY: tests/test_validation.py
```

---

## 🎯 Key Features

### 1. **Answer + Action Plan UX**
Every AI response is structured into two clear sections:
- **Answer**: Natural language explanation of what will be done and why
- **Action Plan**: Structured list of steps with explicit file operations:
  - 🟢 **CREATE** – New files to be added
  - 🔵 **MODIFY** – Existing files to be changed
  - 🔴 **DELETE** – Files to be removed
  - 📖 **READ** – Files to analyze (no changes)

See exactly what will happen before approving execution!

### 2. **Full Multi-LLM Support with Smart Routing** ✨
All four LLM providers are fully operational and tested:
- ✅ **OpenAI** – GPT-4o, GPT-4o-mini, GPT-4-turbo
- ✅ **Claude (Anthropic)** – Claude 4.5 Sonnet, Claude 3 Opus
- ✅ **IBM Watsonx.ai** – Llama 3.3, Granite 3.x models
- ✅ **Ollama** – Local models (Llama3, Mistral, CodeLlama, Phi3)

Switch between providers seamlessly through the Admin UI without restart. The **smart model router** automatically selects the optimal model (fast/balanced/powerful) for each task based on complexity analysis, keeping costs low while maintaining quality where it matters.

### 3. **Local Workspace & Terminal Execution** 🆕
GitPilot now works directly on your local filesystem — just like Claude Code:
- **Local file editing** – Read, write, search, and delete files in a sandboxed workspace
- **Terminal execution** – Run shell commands (`npm test`, `make build`, `python -m pytest`) with timeout and output capping
- **Git operations** – Commit, push, diff, stash, merge — all from the workspace
- **Path traversal protection** – All file operations are sandboxed to prevent escaping the workspace directory

### 4. **Session Management & Checkpoints** 🆕
Persistent sessions that survive restarts and support time-travel:
- **Resume any session** – Pick up exactly where you left off
- **Fork sessions** – Branch a conversation to try different approaches
- **Checkpoint & rewind** – Snapshot workspace state at key moments and roll back if something goes wrong
- **CI/CD headless mode** – Run GitPilot non-interactively via `gitpilot run --headless` for automation pipelines

### 5. **Hook System & Permissions** 🆕
Fine-grained control over what agents can do:
- **Lifecycle hooks** – Register shell commands that fire on events like `pre_commit`, `post_edit`, or `pre_push` — blocking hooks can veto risky actions
- **Three permission modes** – `normal` (ask before risky ops), `plan` (read-only), `auto` (approve everything)
- **Path-based blocking** – Prevent agents from touching `.env`, `*.pem`, or any sensitive file pattern

### 6. **Project Context Memory (GITPILOT.md)** 🆕
The equivalent of Claude Code's `CLAUDE.md` — teach your agents project-specific conventions:
- Create `.gitpilot/GITPILOT.md` with your code style, testing, and commit message rules
- Add modular rules in `.gitpilot/rules/*.md`
- Agents automatically learn patterns from session outcomes and store them in `.gitpilot/memory.json`

### 7. **MCP Server Connections** 🆕
Connect GitPilot to any MCP (Model Context Protocol) server — databases, Slack, Figma, Sentry, and more:
- **stdio, HTTP, and SSE transports** – Connect to local subprocess servers or remote endpoints
- **JSON-RPC 2.0** – Full protocol compliance with tool discovery and invocation
- **Auto-wrap as CrewAI tools** – MCP tools are automatically available to all agents
- Configure servers in `.gitpilot/mcp.json` with environment variable expansion

### 8. **Plugin Marketplace & /Command Skills** 🆕
Extend GitPilot with community plugins and project-specific skills:
- **Install plugins** from git URLs or local paths — each plugin can provide skills, hooks, and MCP configs
- **Define skills** as markdown files in `.gitpilot/skills/*.md` with YAML front-matter and template variables
- **Invoke skills** via `/command` syntax in chat or the CLI: `gitpilot skill review`
- **Auto-trigger skills** based on context patterns (e.g., auto-run lint after edits)

### 9. **Vision & Image Analysis** 🆕
Multimodal capabilities powered by OpenAI, Anthropic, and Ollama vision models:
- **Analyse screenshots** – Describe UI bugs, extract text via OCR, review design mockups
- **Compare before/after** – Detect visual regressions between two screenshots
- **Base64 encoding** with format validation (PNG, JPG, GIF, WebP, BMP, SVG) and 20 MB size limit

### 10. **AI-Powered Security Scanner** 🆕
Go beyond traditional SAST tools with pattern-based and context-aware vulnerability detection:
- **Secret detection** – AWS keys, GitHub tokens, JWTs, Slack tokens, private keys, passwords in code
- **Code vulnerability patterns** – SQL injection, command injection, XSS, SSRF, path traversal, weak crypto, insecure CORS, disabled SSL verification
- **Diff scanning** – Scan only added lines in a git diff for CI/CD integration
- **CWE mapping** – Every finding links to its CWE identifier with actionable recommendations
- **CLI command** – `gitpilot scan /path/to/repo` with confidence thresholds and severity summaries

### 11. **Predictive Workflow Engine** 🆕
Proactive suggestions based on what you just did:
- After merging a PR → suggest updating the changelog
- After test failure → suggest debugging approach
- After dependency update → suggest running full test suite
- After editing security-sensitive code → suggest security review
- 8 built-in trigger rules with configurable cooldowns and relevance scoring
- Add custom prediction rules for your own workflows

### 12. **Parallel Multi-Agent Teams** 🆕
Split large tasks across multiple agents working simultaneously:
- **Task decomposition** – Automatically split complex tasks into independent subtasks
- **Parallel execution** – Agents work concurrently via `asyncio.gather`, each on its own git worktree
- **Conflict detection** – Merging detects file-level conflicts when multiple agents touch the same files
- **Custom or auto-generated plans** — provide your own subtask descriptions or let the engine split evenly

### 13. **Self-Improving Agents** 🆕
GitPilot learns from every interaction and becomes specialised to each project over time:
- **Outcome evaluation** – Checks signals like tests_passed, pr_approved, error_fixed
- **Pattern extraction** – Generates natural-language insights from successes and failures
- **Per-repo persistence** – Learned strategies are stored in JSON and loaded in future sessions
- **Preferred style tracking** – Record project-specific code style preferences that agents follow

### 14. **Cross-Repository Intelligence** 🆕
Understand dependencies and impact across your entire codebase:
- **Dependency graph construction** – Parses `package.json`, `requirements.txt`, `pyproject.toml`, and `go.mod`
- **Impact analysis** – BFS traversal to find all affected repos when updating a dependency, with risk assessment (low/medium/high/critical)
- **Shared convention detection** – Find common config patterns across multiple repos
- **Migration planning** – Generate step-by-step migration plans when replacing one package with another

### 15. **Natural Language Database Queries** 🆕
Query your project's databases using plain English through MCP connections:
- **NL-to-SQL translation** – Rule-based translation with schema-aware table and column matching
- **Safety validation** – Read-only mode blocks INSERT/UPDATE/DELETE; read-write mode blocks DROP/TRUNCATE
- **Query explanation** – Translate SQL back to human-readable descriptions
- **Tabular output** – Results formatted as plain-text tables for CLI or API consumption

### 16. **IDE Extensions** 🆕
Use GitPilot from your favourite editor:
- **VS Code extension** – Sidebar chat panel, inline actions, keybindings (Ctrl+Shift+G), skill invocation, and server configuration
- Connects to the GitPilot API server — all the same agents and tools available from within your editor

### 17. **Topology Registry — Switchable Agent Architectures** 🆕
Seven pre-wired agent topologies that control routing, execution, and visualization in one place:
- **System architectures** (Default fan-out, GitPilot Code ReAct loop) and **task pipelines** (Feature Builder, Bug Hunter, Code Inspector, Architect Mode, Quick Fix) — each with its own agent roster, execution style, and flow graph
- **Zero-latency classifier** selects the best topology from the user's message without an extra LLM call, while the Flow Viewer dropdown lets you switch or pin a topology at any time

### 18. **Agent Flow Viewer**
Interactive visual representation of the CrewAI multi-agent system using ReactFlow:
- **Repository Explorer** – Thoroughly explores codebase structure
- **Refactor Planner** – Creates safe, step-by-step plans with verified file operations
- **Code Writer** – Implements approved changes with AI-generated content
- **Code Reviewer** – Reviews for quality and safety
- **Local Editor & Terminal** – Direct file editing and shell execution agents
- **GitHub API Tools** – Manages file operations and commits

### 19. **Admin / Settings Console**
Full-featured LLM provider configuration with:
- **OpenAI** – API key, model selection, optional base URL
- **Claude** – API key, model selection (Claude 4.5 Sonnet recommended)
- **IBM Watsonx.ai** – API key, project ID, model selection, regional URLs
- **Ollama** – Base URL (local), model selection

Settings are persisted to `~/.gitpilot/settings.json` and survive restarts.

### 20. **MCP / A2A Agent Integration (ContextForge Compatible)**
GitPilot can optionally run as an **A2A agent server** that can be **imported by URL** into **MCP ContextForge (MCP Gateway)** and exposed as MCP tools. This makes GitPilot usable not only from the web UI, but also from:
- MCP-enabled IDEs and CLIs
- automation pipelines (CI/CD)
- other AI agents orchestrated by an MCP gateway

A2A mode is **feature-flagged** and does **not** affect the existing UI/API unless enabled.

---

## 🚀 Installation

### From PyPI (Recommended)

```bash
pip install gitcopilot
```

### From Source

```bash
# Clone the repository
git clone https://github.com/ruslanmv/gitpilot.git
cd gitpilot

# Install dependencies
make install

# Build frontend
make frontend-build

# Run GitPilot
gitpilot
```

### Using Docker (Coming Soon)

```bash
docker pull ruslanmv/gitpilot
docker run -p 8000:8000 -e GITHUB_TOKEN=your_token ruslanmv/gitpilot
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **GitHub Personal Access Token** (with `repo` scope)
- **API key** for at least one LLM provider (OpenAI, Claude, Watsonx, or Ollama)

### 1. Configure GitHub Access

Create a **GitHub Personal Access Token** at https://github.com/settings/tokens with `repo` scope:

```bash
export GITPILOT_GITHUB_TOKEN="ghp_XXXXXXXXXXXXXXXXXXXX"
# or
export GITHUB_TOKEN="ghp_XXXXXXXXXXXXXXXXXXXX"
```

### 2. Configure LLM Provider

You can configure providers via the web UI's Admin/Settings page, or set environment variables:

#### OpenAI
```bash
export OPENAI_API_KEY="sk-..."
export GITPILOT_OPENAI_MODEL="gpt-4o-mini"  # optional
```

#### Claude (Anthropic)
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITPILOT_CLAUDE_MODEL="claude-3-5-sonnet-20241022"  # optional
```

**Note:** Claude integration now includes automatic environment variable configuration for seamless CrewAI compatibility.

#### IBM Watsonx.ai
```bash
export WATSONX_API_KEY="your-watsonx-api-key"
export WATSONX_PROJECT_ID="your-project-id"  # Required!
export WATSONX_BASE_URL="https://us-south.ml.cloud.ibm.com"  # optional, region-specific
export GITPILOT_WATSONX_MODEL="ibm/granite-3-8b-instruct"  # optional
```

**Note:** Watsonx integration requires both API key and Project ID for proper authentication.

#### Ollama (Local Models)
```bash
export OLLAMA_BASE_URL="http://localhost:11434"
export GITPILOT_OLLAMA_MODEL="llama3"  # optional
```

### 3. Run GitPilot

```bash
gitpilot
```

This will:
1. Start the FastAPI backend on `http://127.0.0.1:8000`
2. Serve the web UI at the root URL
3. Open your default browser automatically

Alternative commands:
```bash
# Custom host and port
gitpilot serve --host 0.0.0.0 --port 8000

# API only (no browser auto-open)
gitpilot-api

# Headless mode for CI/CD
gitpilot run --repo owner/repo --message "fix the login bug" --headless

# Initialize project conventions
gitpilot init

# Security scan
gitpilot scan /path/to/repo

# Get proactive suggestions
gitpilot predict "Tests failed in auth module"

# Manage plugins
gitpilot plugin install https://github.com/example/my-plugin.git
gitpilot plugin list

# List and invoke skills
gitpilot skill list
gitpilot skill review

# List available models
gitpilot list-models --provider openai

# Using make (for development)
make run
```

---

## 🔌 MCP / A2A Integration 🆕

GitPilot can run as a **self-contained MCP server** with A2A endpoints. You can use it standalone or optionally integrate with **MCP ContextForge gateway** for advanced multi-agent workflows.

### Two deployment modes
1. **Simple MCP Server** (recommended for most users)
   - Just GitPilot with A2A endpoints enabled
   - Direct MCP client connections
   - Use `make mcp` to deploy

2. **Full MCP Gateway** (optional - with ContextForge)
   - Complete MCP ContextForge infrastructure
   - Advanced gateway features and orchestration
   - Use `make gateway` to deploy

### Why this matters
- **Direct MCP access**: Use GitPilot from MCP-enabled IDEs/CLIs without additional infrastructure
- **No UI required**: Call GitPilot programmatically from automation pipelines
- **Composable**: GitPilot can act as the "repo editor agent" inside larger multi-agent workflows
- **Gateway optional**: Full ContextForge gateway only needed for advanced orchestration scenarios

### Enable A2A mode (does not change existing behavior)
A2A endpoints are disabled by default. Enable them using environment variables:

```bash
export GITPILOT_ENABLE_A2A=true

# Recommended: protect the A2A endpoint (gateway will inject this header)
export GITPILOT_A2A_REQUIRE_AUTH=true
export GITPILOT_A2A_SHARED_SECRET="REPLACE_WITH_LONG_RANDOM_SECRET"
```

Then start GitPilot as usual:

```bash
gitpilot serve --host 0.0.0.0 --port 8000
```

### A2A endpoints

When enabled, GitPilot exposes:

* `POST /a2a/invoke` – A2A invoke endpoint (JSON-RPC + envelope fallback)
* `POST /a2a/v1/invoke` – Versioned alias (recommended for gateways)
* `GET /a2a/health` – Health check
* `GET /a2a/manifest` – Capability discovery (methods + auth hints)

### Auth model (gateway-friendly)

GitPilot supports a gateway-friendly model:

* **Gateway → GitPilot authentication**:
  * `X-A2A-Secret: <shared_secret>` *(recommended)*
    or
  * `Authorization: Bearer <shared_secret>`

* **GitHub auth (optional)**:
  * `X-Github-Token: <token>`
    *(recommended when not using a GitHub App internally)*

> Tip: Avoid sending GitHub tokens in request bodies. Prefer headers to reduce accidental logging exposure.

### Register GitPilot in MCP ContextForge (Optional - Gateway Only)

**Note:** This section is only needed if you're using the **full MCP ContextForge gateway** (`make gateway`). If you're using the simple MCP server (`make mcp`), you can connect MCP clients directly to GitPilot's A2A endpoints.

Once the full gateway stack is deployed, register GitPilot as an A2A agent in ContextForge by providing the endpoint URL (note trailing `/` is recommended for JSON-RPC mode):

* Endpoint URL:
  * `https://YOUR_GITPILOT_DOMAIN/a2a/v1/invoke/`
* Agent type:
  * `jsonrpc`
* Inject auth header:
  * `X-A2A-Secret: <shared_secret>`

After registration, MCP clients connected to the gateway will see GitPilot as an MCP tool (name depends on the gateway configuration).

### Supported A2A methods (stable contract)

GitPilot exposes a small, composable set of methods:

* `repo.connect` – validate access and return repo metadata
* `repo.tree` – list repository tree / files
* `repo.read` – read a file
* `repo.write` – create/update a file (commit)
* `plan.generate` – generate an action plan for a goal
* `plan.execute` – execute an approved plan
* `repo.search` *(optional)* – search repositories

These methods are designed to remain stable even if internal implementation changes.

### Quick Start Deployment

#### Option 1: Simple MCP Server (Recommended)
```bash
# Configure MCP server
cp .env.a2a.example .env.a2a
# Edit .env.a2a and set GITPILOT_A2A_SHARED_SECRET

# Start GitPilot MCP server
make mcp
```

This starts GitPilot with A2A endpoints only - perfect for most use cases.

#### Option 2: Full MCP Gateway (Optional - with ContextForge)
Only needed if you want the complete MCP ContextForge gateway infrastructure:

```bash
# 1. Download ContextForge and place at: deploy/a2a-mcp/mcp-context-forge
# 2. Configure environment
cd deploy/a2a-mcp
cp .env.stack.example .env.stack
# Edit .env.stack and set secrets

# 3. Start full gateway stack
cd ../..
make gateway

# 4. Register GitPilot agent in ContextForge
export CF_ADMIN_BEARER="<jwt-token>"
export GITPILOT_A2A_SECRET="<same-as-env-stack>"
make gateway-register
```

**Note:** Most users only need `make mcp`. The full gateway is optional for advanced setups.

See `deploy/a2a-mcp/README.md` for detailed deployment instructions.

### Cloud deployment note
Because the A2A adapter is stateless, GitPilot can be deployed with multiple replicas behind a load balancer. For long-running executions, consider adding async job execution (Redis/Postgres) in a future release.

---

## 📖 Complete Workflow Guide

### Initial Setup

**Step 1: Launch GitPilot**
```bash
gitpilot
```
Your browser opens to `http://127.0.0.1:8000`

**Step 2: Configure LLM Provider**
1. Click **"⚙️ Admin / Settings"** in the sidebar
2. Select your preferred provider (e.g., OpenAI, Claude, Watsonx, or Ollama)
3. Enter your credentials:
   - **OpenAI**: API key + model
   - **Claude**: API key + model
   - **Watsonx**: API key + Project ID + model + base URL
   - **Ollama**: Base URL + model
4. Click **"Save settings"**
5. See the success message confirming your settings are saved

**Step 3: Connect to GitHub Repository**
1. Click **"📁 Workspace"** to return to the main interface
2. In the sidebar, use the search box to find your repository
3. Click **"Search my repos"** to list all accessible repositories
4. Click on any repository to connect
5. The **Project Context Panel** will show repository information
6. Use the **Refresh** button to update permissions and file counts

### Development Workflow

**Step 1: Browse Your Codebase**
- The **Project Context** panel shows repository metadata
- Browse the file tree to understand structure
- Click on files to preview their contents
- Use the **Refresh** button to update the file tree after changes

**Step 2: Describe Your Task**
In the chat panel, describe what you want in natural language:

**Example 1: Add a Feature**
```
Add a new API endpoint at /api/users/{id}/profile that returns
user profile information including name, email, and bio.
```

**Example 2: Refactor Code**
```
Refactor the authentication middleware to use JWT tokens
instead of session cookies. Update all related tests.
```

**Example 3: Analyze and Generate**
```
Analyze the README.md file and generate Python example code
that demonstrates the main features.
```

**Example 4: Fix a Bug**
```
The login endpoint is returning 500 errors when the email
field is empty. Add proper validation and return a 400
with a helpful error message.
```

**Step 3: Review the Answer + Action Plan**
GitPilot will show you:

**Answer Section:**
- Clear explanation of what will be done
- Why this approach was chosen
- Overall summary of changes

**Action Plan Section:**
- Numbered steps with descriptions
- File operations with colored pills:
  - 🟢 CREATE – Files to be created
  - 🔵 MODIFY – Files to be modified
  - 🔴 DELETE – Files to be removed
  - 📖 READ – Files to analyze (no changes)
- Summary totals (e.g., "2 files to create, 3 files to modify, 1 file to read")
- Risk warnings when applicable

**Step 4: Execute or Refine**
- If the plan looks good: Click **"Approve & Execute"**
- If you want changes: Provide feedback in the chat
  ```
  The plan looks good, but please also add rate limiting
  to the new endpoint to prevent abuse.
  ```
- GitPilot will update the plan based on your feedback

**Step 5: View Execution Results**
After execution, see a detailed log:
```
Step 1: Create authentication endpoint
  ✓ Created src/api/auth.py
  ✓ Modified src/routes/index.py

Step 2: Add authentication tests
  ✓ Created tests/test_auth.py
  ℹ️ READ-only: inspected README.md
```

**Step 6: Refresh File Tree**
After agent operations:
- Click the **Refresh** button in the file tree header
- See newly created/modified files appear
- Verify changes were applied correctly

**Step 7: View Agent Workflow (Optional)**
Click **"🔄 Agent Flow"** to see:
- How agents collaborate (Explorer → Planner → Code Writer → Reviewer)
- Data flow between components
- The complete multi-agent system architecture

---

## 🏗️ Architecture

### Frontend Structure

```
frontend/
├── App.jsx                         # Main application with navigation
├── components/
│   ├── AssistantMessage.jsx       # Answer + Action Plan display
│   ├── ChatPanel.jsx              # AI chat interface
│   ├── FileTree.jsx               # Repository file browser with refresh
│   ├── FlowViewer.jsx             # Agent workflow visualization
│   ├── Footer.jsx                 # Footer with GitHub star CTA
│   ├── LlmSettings.jsx            # Provider configuration UI
│   ├── PlanView.jsx               # Enhanced plan rendering with READ support
│   ├── ProjectContextPanel.jsx    # Repository context with refresh
│   └── RepoSelector.jsx           # Repository search/selection
├── styles.css                      # Global styles with dark theme
├── index.html                      # Entry point
└── package.json                    # Dependencies (React, ReactFlow)
```

### Backend Structure

```
gitpilot/
├── __init__.py
├── api.py                          # FastAPI routes (80+ endpoints)
├── agentic.py                      # CrewAI multi-agent orchestration
├── agent_router.py                 # NLP-based request routing
├── agent_tools.py                  # GitHub API exploration tools
├── cli.py                          # CLI (serve, run, scan, predict, plugin, skill)
├── github_api.py                   # GitHub REST API client
├── github_app.py                   # GitHub App installation management
├── github_issues.py                # Issue CRUD operations
├── github_pulls.py                 # Pull request operations
├── github_search.py                # Code, issue, repo, user search
├── github_oauth.py                 # OAuth web + device flow
├── llm_provider.py                 # Multi-provider LLM factory
├── settings.py                     # Configuration management
├── topology_registry.py            # Switchable agent topologies (7 built-in)
│
│   # --- Phase 1: Feature Parity ---
├── workspace.py                    # Local git clone & file operations
├── local_tools.py                  # CrewAI tools for local editing
├── terminal.py                     # Sandboxed shell command execution
├── session.py                      # Session persistence & checkpoints
├── hooks.py                        # Lifecycle event hooks
├── memory.py                       # GITPILOT.md context memory
├── permissions.py                  # Fine-grained permission policies
├── headless.py                     # CI/CD headless execution mode
│
│   # --- Phase 2: Ecosystem Superiority ---
├── mcp_client.py                   # MCP server connector (stdio/HTTP/SSE)
├── plugins.py                      # Plugin marketplace & management
├── skills.py                       # /command skill system
├── vision.py                       # Multimodal image analysis
├── smart_model_router.py           # Auto-route tasks to optimal model
│
│   # --- Phase 3: Intelligence Superiority ---
├── agent_teams.py                  # Parallel multi-agent on git worktrees
├── learning.py                     # Self-improving agents (per-repo)
├── cross_repo.py                   # Dependency graphs & impact analysis
├── predictions.py                  # Predictive workflow suggestions
├── security.py                     # AI-powered security scanner
├── nl_database.py                  # Natural language SQL via MCP
│
├── a2a_adapter.py                  # Optional A2A/MCP gateway adapter
└── web/                            # Production frontend build
    ├── index.html
    └── assets/
```

### API Endpoints (80+)

#### Repository Management
- `GET /api/repos` – List user repositories (paginated + search)
- `GET /api/repos/{owner}/{repo}/tree` – Get repository file tree
- `GET /api/repos/{owner}/{repo}/file` – Get file contents
- `POST /api/repos/{owner}/{repo}/file` – Update/commit file

#### Issues & Pull Requests
- `GET/POST/PATCH /api/repos/{owner}/{repo}/issues` – Full issue CRUD
- `GET/POST /api/repos/{owner}/{repo}/pulls` – Pull request management
- `PUT /api/repos/{owner}/{repo}/pulls/{n}/merge` – Merge pull request

#### Search
- `GET /api/search/code` – Search code across GitHub
- `GET /api/search/issues` – Search issues and pull requests
- `GET /api/search/repositories` – Search repositories
- `GET /api/search/users` – Search users and organisations

#### Chat & Planning
- `POST /api/chat/message` – Conversational dispatcher (auto-routes to agents)
- `POST /api/chat/plan` – Generate execution plan
- `POST /api/chat/execute` – Execute approved plan
- `POST /api/chat/execute-with-pr` – Execute and auto-create pull request
- `POST /api/chat/route` – Preview routing without execution

#### Sessions & Hooks (Phase 1)
- `GET/POST/DELETE /api/sessions` – Session CRUD
- `POST /api/sessions/{id}/checkpoint` – Create checkpoint
- `GET/POST/DELETE /api/hooks` – Hook registration
- `GET/PUT /api/permissions` – Permission mode management
- `GET/POST /api/repos/{owner}/{repo}/context` – Project memory

#### MCP, Plugins & Skills (Phase 2)
- `GET /api/mcp/servers` – List configured MCP servers
- `POST /api/mcp/connect/{name}` – Connect to MCP server
- `POST /api/mcp/call` – Call a tool on a connected server
- `GET/POST/DELETE /api/plugins` – Plugin management
- `GET/POST /api/skills` – Skill listing and invocation
- `POST /api/vision/analyze` – Analyse an image with a text prompt
- `POST /api/model-router/select` – Preview model selection for a task

#### Intelligence (Phase 3)
- `POST /api/agent-teams/plan` – Split task into parallel subtasks
- `POST /api/agent-teams/execute` – Execute subtasks in parallel
- `POST /api/learning/evaluate` – Evaluate action outcome for learning
- `GET /api/learning/insights/{owner}/{repo}` – Get learned insights
- `POST /api/cross-repo/dependencies` – Build dependency graph
- `POST /api/cross-repo/impact` – Impact analysis for package update
- `POST /api/predictions/suggest` – Get proactive suggestions
- `POST /api/security/scan-file` – Scan file for vulnerabilities
- `POST /api/security/scan-directory` – Recursive directory scan
- `POST /api/security/scan-diff` – Scan git diff for issues
- `POST /api/nl-database/translate` – Natural language to SQL
- `POST /api/nl-database/explain` – Explain SQL in plain English

#### Workflow Visualization & Topologies
- `GET /api/flow/current` – Get agent workflow graph (supports `?topology=` param)
- `GET /api/flow/topologies` – List available agent topologies
- `GET /api/flow/topology/{id}` – Get graph for a specific topology
- `POST /api/flow/classify` – Auto-detect best topology for a message
- `GET /api/settings/topology` – Read saved topology preference
- `POST /api/settings/topology` – Save topology preference

#### A2A / MCP Integration (Optional)
Enabled only when `GITPILOT_ENABLE_A2A=true`:

- `POST /a2a/invoke` – A2A invoke endpoint (JSON-RPC + envelope)
- `POST /a2a/v1/invoke` – Versioned A2A endpoint (recommended)
- `GET /a2a/health` – A2A health check
- `GET /a2a/manifest` – A2A capability discovery (methods + schemas)

---

## 🛠️ Development

### Build Commands (Makefile)

```bash
# Install all dependencies
make install

# Install frontend dependencies only
make frontend-install

# Build frontend for production
make frontend-build

# Run development server
make run

# Run tests (846 tests across 28 test files)
make test

# Lint code
make lint

# Format code
make fmt

# Build Python package
make build

# Clean build artifacts
make clean

# MCP Server Deployment (Simple - Recommended)
make mcp              # Start GitPilot MCP server (A2A endpoints)
make mcp-down         # Stop GitPilot MCP server
make mcp-logs         # View MCP server logs

# MCP Gateway Deployment (Optional - Full ContextForge Stack)
make gateway          # Start GitPilot + MCP ContextForge gateway
make gateway-down     # Stop MCP ContextForge gateway
make gateway-logs     # View gateway logs
make gateway-register # Register agent in ContextForge
```

### CLI Commands

```bash
gitpilot                          # Start server with web UI (default)
gitpilot serve --host 0.0.0.0    # Custom host/port
gitpilot config                   # Show current configuration
gitpilot version                  # Show version
gitpilot run -r owner/repo -m "task"  # Headless execution for CI/CD
gitpilot init                     # Initialize .gitpilot/ with template
gitpilot scan /path              # Security scan a directory or file
gitpilot predict "context text"   # Get proactive suggestions
gitpilot plugin install <source>  # Install a plugin
gitpilot plugin list              # List installed plugins
gitpilot skill list               # List available skills
gitpilot skill <name>             # Invoke a skill
gitpilot list-models              # List LLM models for active provider
```

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Development mode with hot reload
npm run dev

# Build for production
npm run build
```

---

## 📦 Publishing to PyPI

GitPilot uses automated publishing via GitHub Actions with OIDC-based trusted publishing.

### Automated Release Workflow

1. **Update version** in `gitpilot/version.py`
2. **Create and publish a GitHub release** (tag format: `vX.Y.Z`)
3. **GitHub Actions automatically**:
   - Builds source distribution and wheel
   - Uploads artifacts to the release
   - Publishes to PyPI via trusted publishing

See [.github/workflows/release.yml](.github/workflows/release.yml) for details.

### Manual Publishing (Alternative)

```bash
# Build distributions
make build

# Publish to TestPyPI
make publish-test

# Publish to PyPI
make publish
```

---

## 📸 Screenshots

### Example: File Deletion
![](assets/2025-11-16-00-25-49.png)

### Example: Content Generation
![](assets/2025-11-16-00-29-47.png)

### Example: File Creation
![](assets/2025-11-16-01-01-40.png)

### Example multiple operations
![](assets/2025-11-27-00-25-53.png)

---

## 🤝 Contributing

**We love contributions!** Whether it's bug fixes, new features, or documentation improvements.

### How to Contribute

1. ⭐ **Star the repository** (if you haven't already!)
2. 🍴 Fork the repository
3. 🌿 Create a feature branch (`git checkout -b feature/amazing-feature`)
4. ✍️ Make your changes
5. ✅ Run tests (`make test`)
6. 🎨 Run linter (`make lint`)
7. 📝 Commit your changes (`git commit -m 'Add amazing feature'`)
8. 🚀 Push to the branch (`git push origin feature/amazing-feature`)
9. 🎯 Open a Pull Request

### Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/gitpilot.git
cd gitpilot

# Install dependencies
make install

# Create a branch
git checkout -b feature/my-feature

# Make changes and test
make run
make test
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👨‍💻 Author

**Ruslan Magana Vsevolodovna**

- GitHub: [@ruslanmv](https://github.com/ruslanmv)
- Website: [ruslanmv.com](https://ruslanmv.com)

---

## 🙏 Acknowledgments

- **CrewAI** – Multi-agent orchestration framework
- **FastAPI** – Modern, fast web framework
- **React** – UI library
- **ReactFlow** – Interactive node-based diagrams
- **Vite** – Fast build tool
- **MCP (Model Context Protocol)** – By Anthropic, for tool interoperability
- **OWASP Top 10** – Security vulnerability categorisation
- **BIRD benchmark** and **DIN-SQL** research – Text-to-SQL approaches
- **Horvitz (1999)** – Proactive assistance in HCI research
- **All our contributors and stargazers!** ⭐

---

## 📞 Support

- **Issues**: https://github.com/ruslanmv/gitpilot/issues
- **Discussions**: https://github.com/ruslanmv/gitpilot/discussions
- **Documentation**: [Full Documentation](https://github.com/ruslanmv/gitpilot#readme)

---

## 🗺️ Roadmap

### Recently Released (v0.2.0) 🆕

**Phase 1 — Feature Parity with Claude Code:**
- ✅ **Local Workspace Manager** – Clone repos, read/write/search files locally
- ✅ **Terminal Execution** – Sandboxed shell commands with timeout and output capping
- ✅ **Session Persistence** – Resume, fork, checkpoint, and rewind sessions
- ✅ **Hook System** – Lifecycle events (pre_commit, post_edit) with blocking support
- ✅ **Permission Policies** – Three modes (normal/plan/auto) with path-based blocking
- ✅ **Context Memory (GITPILOT.md)** – Project conventions injected into agent prompts
- ✅ **Headless CI/CD Mode** – `gitpilot run --headless` for automation pipelines

**Phase 2 — Ecosystem Superiority:**
- ✅ **MCP Client** – Connect to any MCP server (databases, Slack, Figma, Sentry)
- ✅ **Plugin Marketplace** – Install/uninstall plugins from git or local paths
- ✅ **Skill System** – /command skills defined as markdown with template variables
- ✅ **Vision Analysis** – Multimodal image analysis via OpenAI, Anthropic, or Ollama
- ✅ **Smart Model Router** – Auto-route tasks to optimal model by complexity
- ✅ **VS Code Extension** – Sidebar chat, inline actions, and keybindings

**Phase 3 — Intelligence Superiority (no competitor has this):**
- ✅ **Agent Teams** – Parallel multi-agent execution on git worktrees
- ✅ **Self-Improving Agents** – Learn from outcomes and adapt per-project
- ✅ **Cross-Repo Intelligence** – Dependency graphs and impact analysis across repos
- ✅ **Predictive Workflows** – Proactive suggestions based on context patterns
- ✅ **AI Security Scanner** – Secret detection, injection analysis, CWE mapping
- ✅ **NL Database Queries** – Natural language to SQL translation via MCP
- ✅ **Topology Registry** – Seven switchable agent architectures with zero-latency message classification and visual topology selector

### Previous Features (v0.1.2)
- ✅ Full Multi-LLM Support – All 4 providers fully tested
- ✅ Answer + Action Plan UX with structured file operations
- ✅ Real Execution Engine with GitHub operations
- ✅ Agent Flow Viewer with ReactFlow
- ✅ MCP / A2A Integration (ContextForge compatible)
- ✅ Issue and Pull Request management APIs
- ✅ Code, issue, repository, and user search
- ✅ OAuth web + device flow authentication

### Planned Features (v0.2.1+)
- 🔄 Frontend components for terminal, sessions, checkpoints, and security dashboard
- 🔄 JetBrains IDE plugin
- 🔄 Real-time collaboration (shared sessions, audit log)
- 🔄 Automated test generation from code changes
- 🔄 Slack/Discord notification hooks
- 🔄 LLM-powered semantic diff review
- 🔄 OSV dependency vulnerability scanning
- 🔄 Custom agent templates and agent marketplace

---

## ⚠️ Important Notes

### Security Best Practices

1. **Never commit API keys** to version control
2. **Use environment variables** or the Admin UI for credentials
3. **Rotate tokens regularly**
4. **Limit GitHub token scopes** to only what's needed
5. **Review all plans** before approving execution
6. **Verify GitHub App installations** before granting write access
7. **Run `gitpilot scan`** before releases to catch secrets, injection risks, and insecure configurations
8. **Use permission modes** – run agents in `plan` mode (read-only) when exploring unfamiliar codebases

### LLM Provider Configuration

**All providers now fully supported!** ✨

Each provider has specific requirements:

**OpenAI**
- Requires: `OPENAI_API_KEY`
- Optional: `GITPILOT_OPENAI_MODEL`, `OPENAI_BASE_URL`

**Claude (Anthropic)**
- Requires: `ANTHROPIC_API_KEY`
- Optional: `GITPILOT_CLAUDE_MODEL`, `ANTHROPIC_BASE_URL`
- Note: Environment variables are automatically configured by GitPilot

**IBM Watsonx.ai**
- Requires: `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`
- Optional: `WATSONX_BASE_URL`, `GITPILOT_WATSONX_MODEL`
- Note: Project ID is essential for proper authentication

**Ollama**
- Requires: `OLLAMA_BASE_URL`
- Optional: `GITPILOT_OLLAMA_MODEL`
- Note: Runs locally, no API key needed

### File Action Types

GitPilot supports four file operation types in plans:

- **CREATE** (🟢) – Add new files with AI-generated content
- **MODIFY** (🔵) – Update existing files intelligently
- **DELETE** (🔴) – Remove files safely
- **READ** (📖) – Analyze files without making changes (new!)

READ operations allow agents to gather context and information without modifying your repository, enabling better-informed plans.

---

## 🎓 Learn More

### Understanding the Agent System

GitPilot uses a multi-agent architecture where each request is routed to the right set of agents by the **agent router**, which analyses your message using NLP patterns and dispatches to one of 12+ specialised agent types:

**Core Agents:**
- **Repository Explorer** – Scans codebase structure and gathers context
- **Refactor Planner** – Creates structured step-by-step plans with file operations
- **Code Writer** – Generates AI-powered content for new and modified files
- **Code Reviewer** – Reviews changes for quality, safety, and adherence to conventions

**Local Agents (Phase 1):**
- **Local Editor** – Reads, writes, and searches files directly on disk
- **Terminal Agent** – Executes shell commands (`npm test`, `make build`, `pytest`)

**Specialised Agents (v2):**
- **Issue Agent** – Creates, updates, labels, and comments on GitHub issues
- **PR Agent** – Creates pull requests, lists files, manages merges
- **Search Agent** – Searches code, issues, repos, and users across GitHub
- **Learning Agent** – Evaluates outcomes and improves strategies over time

**Intelligence Layer (Phase 3):**
- **Agent Teams** – Multiple agents work in parallel on subtasks with conflict detection
- **Predictive Engine** – Suggests next actions before you ask
- **Security Scanner** – Detects secrets, injection risks, and insecure configurations
- **Cross-Repo Analyser** – Maps dependencies and assesses impact across repositories

The **smart model router** selects the optimal LLM for each task — simple queries go to fast/cheap models while complex reasoning gets the most powerful model available.

### Choosing the Right LLM Provider

**OpenAI (GPT-4o, GPT-4o-mini)**
- ✅ Best for: General-purpose coding, fast responses
- ✅ Strengths: Excellent code quality, great at following instructions
- ✅ Status: Fully tested and working
- ⚠️ Costs: Moderate to high

**Claude (Claude 4.5 Sonnet)**
- ✅ Best for: Complex refactoring, detailed analysis
- ✅ Strengths: Deep reasoning, excellent at planning
- ✅ Status: Fully tested and working (latest integration fixes applied)
- ⚠️ Costs: Moderate to high

**Watsonx (Llama 3.3, Granite 3.x)**
- ✅ Best for: Enterprise deployments, privacy-focused
- ✅ Strengths: On-premise option, compliance-friendly
- ✅ Status: Fully tested and working (project_id integration fixed)
- ⚠️ Costs: Subscription-based

**Ollama (Local Models)**
- ✅ Best for: Cost-free operation, offline work
- ✅ Strengths: Zero API costs, complete privacy
- ✅ Status: Fully tested and working
- ⚠️ Performance: Depends on hardware, may be slower

---

## 🐛 Troubleshooting

### Common Issues and Solutions

**Issue: "ANTHROPIC_API_KEY is required" error with Claude**
- **Solution**: This is now automatically handled. Update to latest version or ensure environment variables are set via Admin UI.

**Issue: "Fallback to LiteLLM is not available" with Watsonx**
- **Solution**: Ensure you've set both `WATSONX_API_KEY` and `WATSONX_PROJECT_ID`. Install `litellm` if needed: `pip install litellm`

**Issue: Plan generation fails with validation error**
- **Solution**: Update to latest version which includes READ action support in schema validation.

**Issue: "Read Only" status despite having write access**
- **Solution**: Install the GitPilot GitHub App on your repository. Click the install link in the UI or refresh permissions.

**Issue: File tree not updating after agent operations**
- **Solution**: Click the Refresh button in the file tree header to see newly created/modified files.

For more issues, visit our [GitHub Issues](https://github.com/ruslanmv/gitpilot/issues) page.

---

<div align="center">

**⭐ Don't forget to star GitPilot if you find it useful! ⭐**

[⭐ Star on GitHub](https://github.com/ruslanmv/gitpilot) • [📖 Documentation](https://github.com/ruslanmv/gitpilot#readme) • [🐛 Report Bug](https://github.com/ruslanmv/gitpilot/issues) • [💡 Request Feature](https://github.com/ruslanmv/gitpilot/issues)

**GitPilot** – Your AI Coding Companion for GitHub 🚀

Made with ❤️ by [Ruslan Magana Vsevolodovna](https://github.com/ruslanmv)

</div>