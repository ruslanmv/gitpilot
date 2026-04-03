---
title: GitPilot
emoji: "\U0001F916"
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
startup_duration_timeout: 5m
pinned: true
license: mit
short_description: Enterprise AI Coding Assistant for GitHub Repositories
---

# GitPilot — Hugging Face Spaces

**Enterprise-grade AI coding assistant** for GitHub repositories with multi-LLM support, visual workflow insights, and intelligent code analysis.

## What This Does

This Space runs the full GitPilot stack:
1. **React Frontend** — Professional dark-theme UI with chat, file browser, and workflow visualization
2. **FastAPI Backend** — 80+ API endpoints for repository management, AI chat, planning, and execution
3. **Multi-Agent AI** — CrewAI orchestration with 7 switchable agent topologies

## LLM Providers

GitPilot connects to your favorite LLM provider. Configure in **Admin / LLM Settings**:

| Provider | Default | API Key Required |
|---|---|---|
| **OllaBridge Cloud** (default) | `qwen2.5:1.5b` | No |
| OpenAI | `gpt-4o-mini` | Yes |
| Anthropic Claude | `claude-sonnet-4-5` | Yes |
| Ollama (local) | `llama3` | No |
| Custom endpoint | Any model | Optional |

## Quick Start

1. Open the Space UI
2. Enter your **GitHub Token** (Settings -> GitHub)
3. Select a repository from the sidebar
4. Start chatting with your AI coding assistant

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | Health check |
| `POST /api/chat/message` | Chat with AI assistant |
| `POST /api/chat/plan` | Generate implementation plan |
| `GET /api/repos` | List repositories |
| `GET /api/settings` | View/update settings |
| `GET /docs` | Interactive API docs (Swagger) |

## Connect to OllaBridge Cloud

By default, GitPilot connects to [OllaBridge Cloud](https://huggingface.co/spaces/ruslanmv/ollabridge) for LLM inference. This provides free access to open-source models without needing API keys.

To use your own OllaBridge instance:
1. Go to **Admin / LLM Settings**
2. Select **OllaBridge** provider
3. Enter your OllaBridge URL and model

## Environment Variables

Configure via HF Spaces secrets:

| Variable | Description | Default |
|---|---|---|
| `GITPILOT_PROVIDER` | LLM provider | `ollabridge` |
| `OLLABRIDGE_BASE_URL` | OllaBridge Cloud URL | `https://ruslanmv-ollabridge.hf.space` |
| `GITHUB_TOKEN` | GitHub personal access token | - |
| `OPENAI_API_KEY` | OpenAI API key (if using OpenAI) | - |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) | - |

## Links

- [GitPilot Repository](https://github.com/ruslanmv/gitpilot)
- [OllaBridge Cloud](https://huggingface.co/spaces/ruslanmv/ollabridge)
- [Documentation](https://github.com/ruslanmv/gitpilot#readme)
