# Changelog

All notable changes to GitPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2024-11-14

### Added
- **Admin / Settings Console**
  - Full LLM provider management (OpenAI, Claude, Watsonx, Ollama)
  - Provider-specific configuration forms
  - Persistent settings storage (~/.gitpilot/settings.json)
  - API key management with secure storage
  - Model selection for each provider

- **Agent Flow Viewer**
  - Interactive workflow visualization using ReactFlow
  - Visual representation of multi-agent system
  - Node-based diagram showing agent collaboration
  - Animated edges displaying data flow
  - Color-coded agents vs. tools
  - Mini-map and zoom controls

- **Three-Tab Navigation**
  - Workspace tab for repository browsing and AI chat
  - Agent Flow tab for workflow visualization
  - Admin/Settings tab for LLM configuration

- **Multi-LLM Provider Support**
  - OpenAI (GPT-4o, GPT-4o-mini, GPT-4-turbo)
  - Claude (Claude 3.5 Sonnet, Claude 3 Opus)
  - IBM Watsonx.ai (Llama, Granite models)
  - Ollama (local models: Llama3, Mistral, CodeLlama, Phi3)

- **Core Features**
  - GitHub repository browsing and file tree navigation
  - AI-powered plan generation using CrewAI
  - Step-by-step execution plans with risk assessment
  - Repository file content viewing
  - Chat interface for natural language interactions

- **API Endpoints**
  - `GET /api/settings` - Get current LLM settings
  - `PUT /api/settings/llm` - Update provider configurations
  - `POST /api/settings/provider` - Change active provider
  - `GET /api/flow/current` - Get agent workflow graph
  - `GET /api/repos` - List user repositories
  - `GET /api/repos/{owner}/{repo}/tree` - Get repository file tree
  - `GET /api/repos/{owner}/{repo}/file` - Get file contents
  - `POST /api/chat/plan` - Generate execution plan
  - `POST /api/chat/execute` - Execute approved plan

- **Documentation**
  - Comprehensive README with installation and usage guide
  - Complete frontend code reference
  - Architecture documentation
  - API endpoint reference
  - Development guide

### Technical Details
- Built with FastAPI for backend
- React + ReactFlow for frontend
- CrewAI for multi-agent orchestration
- Production-ready build with optimized bundles
- Type hints and py.typed marker for type checking
- Ruff for linting and formatting
- Comprehensive error handling and loading states

### Notes
- Plan execution is currently stubbed for safety
- Full execution capabilities planned for v0.2.0
- Requires Python 3.11
- GitHub token with `repo` scope required

[0.1.0]: https://github.com/ruslanmv/gitpilot/releases/tag/v0.1.0
