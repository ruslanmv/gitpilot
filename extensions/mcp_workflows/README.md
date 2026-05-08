# Docker-publish workflows for the three MCP servers

Same shape as `gitpilot/.github/workflows/build-and-push-docker.yml` so
all four MCP-stack images carry consistent tags and labels.

| Source repo | Image published | Build context | Workflow file |
|-------------|-----------------|---------------|---------------|
| `ruslanmv/mcp-postgre-server` | `<DOCKERHUB_USERNAME>/mcp-postgre-server` | `.` | `mcp-postgre-server.docker-publish.yml` |
| `ruslanmv/mcp-inspector-server` | `<DOCKERHUB_USERNAME>/mcp-inspector-server` | `.` | `mcp-inspector-server.docker-publish.yml` |
| `ruslanmv/milvus-admin-ui` | `<DOCKERHUB_USERNAME>/mcp-milvus-server` | `./mcp-server` | `mcp-milvus-server.docker-publish.yml` |

## Why these live here, not in their own repos yet

The PAT used by this branch's automation lacks the `workflow` scope —
GitHub refuses to push files under `.github/workflows/` from a PAT
without that scope. So the workflows are staged here as
ready-to-deploy templates. You install them with a single command (next
section), or copy each one into the corresponding repo by hand.

## One-command install (recommended)

```bash
make install-mcp-workflows
```

That target lives in the gitpilot Makefile. It iterates over each
checkout under `mcp-stack/`, copies the matching workflow into
`<repo>/.github/workflows/docker-publish.yml`, commits with a
descriptive message, and (if `GH_PAT_WORKFLOW` is set in your env)
pushes. If `GH_PAT_WORKFLOW` is empty the target stops after the
commit and prints the `git push` command so you can run it with the
auth of your choice.

`GH_PAT_WORKFLOW` must be a Personal Access Token with **`repo` and
`workflow`** scopes. Mint one at:
<https://github.com/settings/tokens?type=beta>.

## Manual install (per repo)

```bash
cd path/to/mcp-postgre-server
mkdir -p .github/workflows
cp .../gitpilot/extensions/mcp_workflows/mcp-postgre-server.docker-publish.yml \
   .github/workflows/docker-publish.yml
git add .github/workflows/docker-publish.yml
git commit -m "ci: publish Docker image on release / workflow_dispatch"
git push   # PAT must have the workflow scope
```

Repeat for `mcp-inspector-server` and `milvus-admin-ui`.

## Required Docker Hub secrets (set on each repo)

```
DOCKERHUB_USERNAME   the Docker Hub account that owns the images
DOCKERHUB_TOKEN      a Docker Hub access token with write scope
```

Set them at `Settings → Secrets and variables → Actions → New repository secret`.

## Triggering a publish

Two paths:

1. **GitHub Release** (canonical) — create a release in the repo's
   "Releases" page; the workflow picks up the tag and publishes
   `<image>:<semver>`, `<image>:latest`, `<image>:sha-<short>`, and the
   `{{major}}.{{minor}}` shorthand.
2. **Manual** — Actions → "🐳 Build & Push Docker Image" → "Run
   workflow" → enter a version (or `latest`) → green button.

Both paths run on `linux/amd64` and `linux/arm64`, push under
`<DOCKERHUB_USERNAME>/<image>`, and end with a step summary listing
the published tags and pull commands.

## After the workflows run, your `.mcp.env` can pin a real tag

Once the images exist on Docker Hub, you can switch from the
build-from-source flow to image pulls:

```bash
# in .mcp.env
MCP_POSTGRE_REF=v1.0.0      # tag to ship
MCP_FORGE_REF=...
```

…then either re-clone with that ref (current default) or rewrite
`docker-compose.mcp.yml` to use `image: ruslanmv/mcp-postgre-server:v1.0.0`
instead of `build:`. The latter is the next iteration once the first
release tags exist.
