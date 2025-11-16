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

* **🧠 Understands your entire codebase** – Analyzes project structure and file relationships
* **📋 Shows clear plans before executing** – Always presents an "Answer + Action Plan" with structured file operations (CREATE/MODIFY/DELETE)
* **🔄 Manages multiple LLM providers** – Seamlessly switch between OpenAI, Claude, Watsonx, and Ollama
* **👁️ Visualizes agent workflows** – See exactly how the multi-agent system thinks and operates
* **🔗 Integrates directly with GitHub** – Repository access, file editing, commits, and more

**Built with CrewAI, FastAPI, and React** — GitPilot combines the power of multi-agent AI with a beautiful, modern web interface.

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
#      - MODIFY: tests/test_auth.py

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
  Step 1: Convert auth service to async
    - MODIFY: src/services/auth_service.py (12 functions)
  Step 2: Update route handlers
    - MODIFY: src/routes/auth.py
  Step 3: Migrate database calls
    - MODIFY: src/db/user_repository.py
  Step 4: Update unit tests
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

### 1. **Answer + Action Plan UX** 🆕
Every AI response is structured into two clear sections:
- **Answer**: Natural language explanation of what will be done and why
- **Action Plan**: Structured list of steps with explicit file operations:
  - 🟢 **CREATE** – New files to be added
  - 🔵 **MODIFY** – Existing files to be changed
  - 🔴 **DELETE** – Files to be removed

See exactly what will happen before approving execution!

### 2. **Project Context Panel** 🆕
Visual display of your repository state:
- Repository name and branch
- Total file count
- Last analysis timestamp
- Interactive file tree browser

### 3. **Real Execution Engine** 🆕
GitPilot now performs actual GitHub operations:
- Creates new files with proper content
- Modifies existing files intelligently
- Marks files for deletion (safe approach)
- Returns detailed execution logs with success/failure status

### 4. **Admin / Settings Console**
Full-featured LLM provider configuration with:
- **OpenAI** – GPT-4o, GPT-4o-mini, GPT-4-turbo support
- **Claude** – Claude 3.5 Sonnet, Claude 3 Opus support
- **IBM Watsonx.ai** – Llama, Granite models
- **Ollama** – Local models (Llama3, Mistral, CodeLlama, Phi3, etc.)

Settings are persisted to `~/.gitpilot/settings.json` and survive restarts.

### 5. **Agent Flow Viewer**
Interactive visual representation of the CrewAI multi-agent system using ReactFlow:
- **Repository Reader** – Analyzes codebase structure
- **Refactor Planner** – Creates safe, step-by-step plans
- **Code Writer** – Implements approved changes
- **Code Reviewer** – Reviews for quality and safety
- **GitHub API Tools** – Manages file operations and commits

### 6. **Three-Tab Navigation**
Seamlessly switch between:
- 📁 **Workspace** – Repository browsing and AI chat
- 🔄 **Agent Flow** – Visual workflow diagram
- ⚙️ **Admin / Settings** – LLM provider management

---

## 🚀 Installation

### From PyPI (Recommended)

```bash
pip install gitpilot
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

#### IBM Watsonx
```bash
export WATSONX_API_KEY="your-watsonx-api-key"
export WATSONX_PROJECT_ID="your-project-id"
export GITPILOT_WATSONX_MODEL="meta-llama/llama-3-1-70b-instruct"  # optional
```

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

# Using make (for development)
make run
```

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
2. Select your preferred provider (e.g., OpenAI)
3. Enter your API key and select a model (e.g., `gpt-4o-mini`)
4. Click **"Save settings"**
5. See the success message confirming your settings are saved

**Step 3: Connect to GitHub Repository**
1. Click **"📁 Workspace"** to return to the main interface
2. In the sidebar, use the search box to find your repository
3. Click **"Search my repos"** to list all accessible repositories
4. Click on any repository to connect

### Development Workflow

**Step 1: Browse Your Codebase**
- The **Project Context** panel shows repository metadata
- Browse the file tree to understand structure
- Click on files to preview their contents

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

**Example 3: Fix a Bug**
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
- Summary totals (e.g., "2 files to create, 3 files to modify")
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
```

**Step 6: View Agent Workflow (Optional)**
Click **"🔄 Agent Flow"** to see:
- How agents collaborate (Repository Reader → Planner → Code Writer → Reviewer)
- Data flow between components
- The complete multi-agent system architecture

---

## 🏗️ Architecture

### Frontend Structure

```
frontend/
├── App.jsx                         # Main application with navigation
├── components/
│   ├── AssistantMessage.jsx       # Answer + Action Plan display (NEW)
│   ├── ChatPanel.jsx              # AI chat interface
│   ├── FileTree.jsx               # Repository file browser
│   ├── FlowViewer.jsx             # Agent workflow visualization
│   ├── Footer.jsx                 # Footer with GitHub star CTA (NEW)
│   ├── LlmSettings.jsx            # Provider configuration UI
│   ├── PlanView.jsx               # Enhanced plan rendering (NEW)
│   ├── ProjectContextPanel.jsx    # Repository context display (NEW)
│   └── RepoSelector.jsx           # Repository search/selection
├── styles.css                      # Global styles with dark theme
├── index.html                      # Entry point
└── package.json                    # Dependencies (React, ReactFlow)
```

### Backend Structure

```
gitpilot/
├── __init__.py
├── api.py                          # FastAPI routes and endpoints
├── agentic.py                      # CrewAI agents + real executor (UPDATED)
├── cli.py                          # Command-line interface
├── github_api.py                   # GitHub REST API client
├── langflow_client.py              # LangFlow integration (optional)
├── llm_provider.py                 # Multi-provider LLM factory
├── settings.py                     # Configuration management
└── web/                            # Production frontend build
    ├── index.html
    └── assets/
        ├── index-*.css
        └── index-*.js
```

### API Endpoints

#### Repository Management
- `GET /api/repos` – List user repositories
- `GET /api/repos/{owner}/{repo}/tree` – Get repository file tree
- `GET /api/repos/{owner}/{repo}/file` – Get file contents
- `POST /api/repos/{owner}/{repo}/file` – Update/commit file

#### Settings & Configuration
- `GET /api/settings` – Get current LLM settings
- `POST /api/settings/provider` – Change active provider
- `PUT /api/settings/llm` – Update provider-specific settings

#### Chat & Planning
- `POST /api/chat/plan` – Generate execution plan (with structured actions)
- `POST /api/chat/execute` – Execute approved plan (returns execution log)

#### Workflow Visualization
- `GET /api/flow/current` – Get agent workflow graph

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

# Run tests
make test

# Lint code
make lint

# Format code
make fmt

# Build Python package
make build

# Clean build artifacts
make clean
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

# Example of deletion of files

![](assets/2025-11-16-00-25-49.png)

# Example of genertion of content

![](assets/2025-11-16-00-29-47.png)

# Example of generation of files

![](assets/2025-11-16-01-01-40.png)

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
- **All our contributors and stargazers!** ⭐

---

## 📞 Support

- **Issues**: https://github.com/ruslanmv/gitpilot/issues
- **Discussions**: https://github.com/ruslanmv/gitpilot/discussions
- **Documentation**: [Full Documentation](https://github.com/ruslanmv/gitpilot#readme)

---

## 🗺️ Roadmap

### Recently Released (v0.2.0) 🆕
- ✅ **Answer + Action Plan UX** – Clear separation of explanation and action items
- ✅ **Structured File Actions** – Explicit CREATE/MODIFY/DELETE operations
- ✅ **Project Context Panel** – Repository metadata display
- ✅ **Real Execution Engine** – Actual GitHub file operations
- ✅ **Execution Logs** – Detailed success/failure tracking
- ✅ **Enhanced Plan View** – Color-coded pills and totals
- ✅ **Footer with GitHub CTA** – Community engagement

### Current Features (v0.1.0)
- ✅ GitHub repository browsing
- ✅ Multi-LLM provider support (OpenAI, Claude, Watsonx, Ollama)
- ✅ Admin/Settings console
- ✅ Agent Flow Viewer
- ✅ AI-powered plan generation
- ✅ Production-ready web UI

### Planned Features (v0.3.0+)
- 🔄 Intelligent code modification (using LLM for actual changes)
- 🔄 Pull request creation and management
- 🔄 Multi-file refactoring
- 🔄 Automated test generation
- 🔄 Code review automation
- 🔄 Branch management
- 🔄 Team collaboration features
- 🔄 Integration with CI/CD pipelines
- 🔄 Custom agent templates
- 🔄 Slack/Discord notifications

---

## ⚠️ Important Notes

### Security Best Practices

1. **Never commit API keys** to version control
2. **Use environment variables** or the Admin UI for credentials
3. **Rotate tokens regularly**
4. **Limit GitHub token scopes** to only what's needed
5. **Review all plans** before approving execution

### Current Behavior

**Execution Safety**: The current executor performs simple file operations:
- CREATE adds files with placeholder content
- MODIFY appends comments
- DELETE marks files (safe approach)

Future versions will use LLM-powered code generation for intelligent modifications.

---

## 🎓 Learn More

### Understanding the Agent System

GitPilot uses a multi-agent architecture:

1. **Repository Reader** – Scans and understands your codebase
2. **Planner** – Converts your goals into actionable steps with structured file operations
3. **Code Writer** – Generates code based on the plan
4. **Reviewer** – Checks for quality, safety, and best practices
5. **GitHub Tools** – Interfaces with GitHub API

Each agent specializes in a specific task, working together like a development team.

### Choosing the Right LLM Provider

**OpenAI (GPT-4o, GPT-4o-mini)**
- ✅ Best for: General-purpose coding, fast responses
- ✅ Strengths: Excellent code quality, great at following instructions
- ⚠️ Costs: Moderate to high

**Claude (Claude 3.5 Sonnet)**
- ✅ Best for: Complex refactoring, detailed analysis
- ✅ Strengths: Deep reasoning, excellent at planning
- ⚠️ Costs: Moderate to high

**Watsonx (Llama, Granite)**
- ✅ Best for: Enterprise deployments, privacy-focused
- ✅ Strengths: On-premise option, compliance-friendly
- ⚠️ Costs: Subscription-based

**Ollama (Local Models)**
- ✅ Best for: Cost-free operation, offline work
- ✅ Strengths: Zero API costs, complete privacy
- ⚠️ Performance: Depends on hardware, may be slower

---

<div align="center">

**⭐ Don't forget to star GitPilot if you find it useful! ⭐**

[⭐ Star on GitHub](https://github.com/ruslanmv/gitpilot) • [📖 Documentation](https://github.com/ruslanmv/gitpilot#readme) • [🐛 Report Bug](https://github.com/ruslanmv/gitpilot/issues) • [💡 Request Feature](https://github.com/ruslanmv/gitpilot/issues)

**GitPilot** – Your AI Coding Companion for GitHub 🚀

Made with ❤️ by [Ruslan Magana Vsevolodovna](https://github.com/ruslanmv)

</div>
