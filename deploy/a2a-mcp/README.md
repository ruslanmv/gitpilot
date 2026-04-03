# GitPilot + MCP ContextForge Gateway (Optional Full Stack Deployment)

**Note:** This is the **optional full MCP ContextForge gateway** deployment. Most users only need the simple MCP server:

```bash
# Simple MCP server (recommended for most users)
make mcp
```

This folder provides a turnkey *development* stack for the **full gateway** that runs:

- GitPilot backend with A2A endpoints enabled
- GitPilot frontend (optional)
- MCP ContextForge gateway (MCP server) with Admin UI
- PostgreSQL and Redis for gateway state management
- Nginx reverse proxy

**Use this only if you need:**
- Advanced gateway orchestration features
- Multi-agent workflow management
- Centralized MCP server registry

---

## Quick Command Reference

### Simple MCP Server (Recommended)
```bash
# From repo root
make mcp          # Start GitPilot MCP server
make mcp-down     # Stop MCP server
make mcp-logs     # View logs
```

### Full Gateway Stack (This Guide)
```bash
# From repo root
make gateway          # Start full ContextForge stack
make gateway-down     # Stop full stack
make gateway-logs     # View logs
make gateway-register # Register agent in ContextForge
```

---

## Full Gateway Deployment Instructions

## 1) Put ContextForge source here

Place (clone/unzip) the ContextForge project into:

`deploy/a2a-mcp/mcp-context-forge`

The folder should contain files like `Containerfile.lite`, `mcpgateway/`, etc.

## 2) Configure env

Copy:

- `deploy/a2a-mcp/.env.stack.example` -> `deploy/a2a-mcp/.env.stack`

Set **strong secrets** for:
- `GITPILOT_A2A_SHARED_SECRET`
- `MCPGATEWAY_JWT_SECRET`

## 3) Start everything

From repo root:

```bash
cd deploy/a2a-mcp
chmod +x setup.sh register_agent.sh
./setup.sh
```

This will build and start containers.

## 4) Create an admin JWT token for ContextForge

ContextForge includes tooling to create JWTs; exact command can vary by version.
If your ContextForge container provides it, you can exec into it and run a token generator.

Then export:

```bash
export CF_ADMIN_BEARER="...jwt..."
```

## 5) Register GitPilot A2A agent in ContextForge

```bash
export CF_BASE_URL="http://localhost:8080"
export GITPILOT_A2A_SECRET="same_as_.env.stack"
# Inside docker network, use service name:
export GITPILOT_A2A_URL="http://gitpilot-backend:8000/a2a/v1/invoke/"
./register_agent.sh
```

## 6) Use from an MCP client

Your script will print an MCP endpoint like:

`http://localhost:8080/servers/<SERVER_ID>/mcp`

Connect with an MCP client / inspector using Streamable HTTP.
