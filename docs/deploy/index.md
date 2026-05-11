# Deploying GitPilot

GitPilot is a standard Python package + a FastAPI server + a static frontend.
Pick the path that matches your environment.

## Hosted (one-click)

* **[Render](render.md)** — Python + Docker, free tier available.
* **[Vercel](vercel.md)** — serverless frontend + API.
* **[Quick deploy](quick.md)** — opinionated 60-second deploy.

## Self-hosted

* **[Docker](docker.md)** — single-host docker-compose stack.
* **[Production](production.md)** — production-hardened defaults.
* **[Production with MCP](production-mcp.md)** — adds the MCP context-forge stack.
* **[Install MCP](install-mcp.md)** — install just the MCP layer separately.

## Detailed guides

* **[Render — detailed](render-detailed.md)** — every knob explained.
* **[Vercel setup](vercel-setup.md)** — initial configuration.
* **[Vercel testing](vercel-testing.md)** — smoke tests after deploy.

## Recommended path

For a brand-new project:

1.  `pip install gitcopilot` — try locally.
2.  `gitpilot init --wizard` — generate the workspace artefacts.
3.  Pick the deployment target that matches your team's existing
    infrastructure (Docker if self-hosting, Render or Vercel if you
    want managed).

All deployment recipes assume you have set the appropriate provider
API key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) in the platform's
secret store.  See **[../API_STABILITY.md](../API_STABILITY.md)** for
the import surface your integration should rely on.
