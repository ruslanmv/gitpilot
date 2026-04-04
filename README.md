<div align="center">

<img src="docs/logo.svg" alt="GitPilot" width="140" />

# GitPilot

**Your AI coding companion. Ask. Plan. Code. Ship.**

[![Version](https://img.shields.io/badge/version-0.1.5-D95C3D?style=flat-square&labelColor=1C1C1F)](https://github.com/ruslanmv/gitpilot)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-D95C3D?style=flat-square&labelColor=1C1C1F)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-D95C3D?style=flat-square&labelColor=1C1C1F)](LICENSE)
[![VS Code](https://img.shields.io/badge/VS%20Code-Extension-D95C3D?style=flat-square&labelColor=1C1C1F)](https://marketplace.visualstudio.com/)

[Get Started](#get-started) &#8226; [VS Code Extension](#vs-code-extension) &#8226; [Web App](#web-app) &#8226; [How It Works](#how-it-works) &#8226; [Contributing](#contributing)

</div>

---

## What is GitPilot?

GitPilot is an AI assistant that helps you code faster. It reads your project, understands the structure, creates a plan, writes the code, and runs your tests.

Works with **any language**. Runs on **any LLM** (OpenAI, Claude, Ollama, Watsonx, OllaBridge).

```
You: "Add input validation to the login form"

GitPilot:
  1. Reading src/auth/login.ts...
  2. Planning 3 changes...
  3. Editing login.ts (Allow? [Yes] [No])
  4. Running npm test... 3 passed
  5. Done.
```

---

## Get Started

### Option 1: VS Code Extension (recommended)

Install the extension, configure your LLM, and start chatting:

```
1. Open VS Code
2. Install "GitPilot Workspace" from Extensions
3. Click the GitPilot icon in the sidebar
4. Choose your AI provider (OpenAI, Claude, Ollama...)
5. Start asking questions about your code
```

### Option 2: Web App

Run the full web interface with Docker:

```bash
git clone https://github.com/ruslanmv/gitpilot.git
cd gitpilot
docker compose up
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Option 3: Python CLI

```bash
pip install gitcopilot
gitpilot serve
```

Open [http://localhost:8000](http://localhost:8000).

> **Note**: The PyPI package is named `gitcopilot`, but the command-line tool is `gitpilot`. Requires Python 3.11 or 3.12.

---

## VS Code Extension

The sidebar panel gives you everything in one place:

| Feature | What it does |
|---|---|
| **Chat** | Ask questions, request changes, review code |
| **Plan View** | See the step-by-step plan before changes are made |
| **Diff Preview** | Review proposed edits in VS Code's native diff viewer |
| **Apply / Revert** | One click to apply changes, one click to undo |
| **Quick Actions** | Explain, Review, Fix, Generate Tests, Security Scan |
| **Smart Commit** | AI-generated commit messages |
| **Code Lens** | Inline "Explain / Review" hints on functions |

### Supported AI Providers

| Provider | Setup | Free? |
|---|---|---|
| **Ollama** | Install Ollama, run `ollama pull llama3` | Yes |
| **OllaBridge** | Works out of the box (cloud Ollama) | Yes |
| **OpenAI** | Add your API key in settings | Paid |
| **Claude** | Add your Anthropic API key | Paid |
| **Watsonx** | Add IBM credentials | Paid |

---

## Web App

The web interface includes:

- Chat with real-time responses
- GitHub integration (connect your repos)
- File tree browser
- Diff viewer with line-by-line changes
- Pull request creation
- Session history with checkpoints
- Multi-repo support

---

## How It Works

```
          You ask a question
                 |
                 v
        +--------+--------+
        |  GitPilot Server |
        |  (FastAPI + AI)  |
        +--------+--------+
                 |
     +-----------+-----------+
     |           |           |
     v           v           v
  Explore     Plan       Execute
  (read       (create     (write files,
   files,      steps,      run tests,
   git log)    diffs)      commit)
```

GitPilot uses a multi-agent system powered by CrewAI:

1. **Explorer** reads your repo structure
2. **Planner** creates a safe step-by-step plan
3. **Executor** writes code and runs tests
4. **Reviewer** checks the results

You approve every change before it's applied.

---

## Project Structure

```
gitpilot/
  gitpilot/           Python backend (FastAPI)
  frontend/           React web app
  extensions/vscode/  VS Code extension
  docs/               Documentation and assets
  tests/              Test suite
```

---

## Configuration

GitPilot works with environment variables or the settings UI.

**Minimal setup** (Ollama, free, local):

```bash
# .env
GITPILOT_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
GITPILOT_OLLAMA_MODEL=llama3
```

**Cloud setup** (OpenAI):

```bash
# .env
GITPILOT_PROVIDER=openai
OPENAI_API_KEY=sk-...
GITPILOT_OPENAI_MODEL=gpt-4o-mini
```

**Cloud setup** (Claude):

```bash
# .env
GITPILOT_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
GITPILOT_CLAUDE_MODEL=claude-sonnet-4-5
```

All settings can also be changed from the VS Code extension or web UI without editing files.

---

## API

GitPilot exposes a REST + WebSocket API:

| Endpoint | What it does |
|---|---|
| `GET /api/status` | Server health check |
| `POST /api/chat/send` | Send a message, get a response |
| `POST /api/v2/chat/stream` | Stream agent events (SSE) |
| `WS /ws/v2/sessions/{id}` | Real-time WebSocket streaming |
| `POST /api/chat/plan` | Generate an execution plan |
| `POST /api/chat/execute` | Execute a plan |
| `GET /api/repos` | List connected repositories |
| `GET /api/sessions` | List chat sessions |

Full API docs at `http://localhost:8000/docs` (Swagger UI).

---

## Deployment

### Hugging Face Spaces

GitPilot runs on Hugging Face Spaces with OllaBridge (free):

```
Runtime: Docker
Port: 7860
Provider: OllaBridge (cloud Ollama)
```

### Docker Compose

```bash
docker compose up -d
# Backend: http://localhost:8000
# Frontend: http://localhost:3000
```

### Vercel

The frontend deploys to Vercel. Set `VITE_BACKEND_URL` to your backend.

---

## Contributing

```bash
# Backend
cd gitpilot
pip install -e ".[dev]"
pytest

# Frontend
cd frontend
npm install
npm run dev

# VS Code Extension
cd extensions/vscode
npm install
make compile
# Press F5 in VS Code to launch debug host
```

---

## License

MIT License. See [LICENSE](LICENSE).

---

<div align="center">

**GitPilot** is made by [Ruslan Magana Vsevolodovna](https://github.com/ruslanmv)

[Star on GitHub](https://github.com/ruslanmv/gitpilot) &#8226; [Report a Bug](https://github.com/ruslanmv/gitpilot/issues) &#8226; [Request a Feature](https://github.com/ruslanmv/gitpilot/issues)

</div>
