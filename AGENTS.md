# AGENTS.md

> Persistent project context loaded into every GitPilot session.
> Edit freely — agents will follow these notes.

## Project Overview
This project has a `README.md` at its root — refer to it for purpose and high-level usage.

## Directory Layout
- `CHANGELOG.md`
- `Dockerfile.backend`
- `Dockerfile.frontend`
- `LICENSE`
- `MANIFEST.in`
- `Makefile`
- `README.md`
- `api/`
- `assets/`
- `deploy/`
- `docker-compose.mcp.yml`
- `docker-compose.yml`
- `docs/`
- `extensions/`
- `frontend/`
- `gitcopilot.egg-info/`
- `gitpilot/`
- `mcp-stack/`
- `mkdocs.yml`
- `mypy.ini`
- `package-lock.json`
- `package.json`
- `pyproject.toml`
- `render.yaml`
- `scripts/`
- `tests/`
- `uv.lock`
- `vercel.json`

## Stack
Python, Node.js, Docker, docker-compose

## Workflows
- `make install`, `make test`, `make run`
- `npm install`, `npm test`
- `pip install -e .` and `pytest`

## Conventions
- Keep changes small and reversible.
- Run the test suite before committing.
- Write docstrings for any new public function.

## Mode-Specific Notes
Place per-mode overrides in `.gitpilot/AGENTS.<mode>.md` (for example
`.gitpilot/AGENTS.coder.md`).  Use `@./relative/path.md` on its own line to
include another markdown file.
