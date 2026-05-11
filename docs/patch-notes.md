# GitPilot A2A + MCP Upgrade Bundle

This bundle adds an OPTIONAL A2A adapter to GitPilot, enabling it to function as an MCP server.

## What changes
- Adds: gitpilot/a2a_adapter.py
  - POST /a2a/invoke
  - POST /a2a/v1/invoke
  - GET  /a2a/health
  - GET  /a2a/manifest
- Updates: gitpilot/api.py
  - Feature flag mount: GITPILOT_ENABLE_A2A=true
- Adds deployment assets:
  - .env.a2a.example
  - deploy/a2a-mcp/* (compose + scripts for optional gateway)
- Adds Makefile targets:
  - `make mcp` - Simple MCP server (recommended)
  - `make gateway` - Full ContextForge stack (optional)

## How to apply to your repo
Copy the files from this bundle into your GitPilot repository, preserving paths.

## Deployment Options

### Option 1: Simple MCP Server (Recommended)
Start GitPilot with A2A endpoints:
```bash
# Configure
cp .env.a2a.example .env.a2a
# Edit .env.a2a and set:
# - GITPILOT_ENABLE_A2A=true
# - GITPILOT_A2A_SHARED_SECRET=<long random>
# - GITPILOT_A2A_REQUIRE_AUTH=true

# Deploy
make mcp
```

### Option 2: Full MCP Gateway (Optional)
Only needed for advanced gateway orchestration:
```bash
# See deploy/a2a-mcp/README.md for full setup
make gateway
```

Most users only need `make mcp`.
