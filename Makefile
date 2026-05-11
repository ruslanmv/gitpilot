# Makefile - GitPilot
# Backend (Python, uv) + Frontend (React/Vite)

.DEFAULT_GOAL := install

UV      ?= uv
PYTHON  ?= python3.11
PORT    ?= 8000
# Keep uv's cache beside the project so WSL /mnt/c checkouts do not copy
# wheels from Linux home-dir cache across filesystems on every install.
UV_CACHE_DIR ?= .uv-cache
# WSL /mnt/c and some Docker/VM filesystems do not support uv hardlinks,
# causing the noisy "Failed to hardlink files" fallback warning. Use copy
# mode by default; override with `make install UV_LINK_MODE=hardlink` on
# native Linux/macOS filesystems if you want hardlinks.
UV_LINK_MODE ?= copy
UV_ENV       := UV_CACHE_DIR=$(UV_CACHE_DIR) UV_LINK_MODE=$(UV_LINK_MODE)

# Docker Compose command (prefer v2 over v1)
DOCKER_COMPOSE := $(shell if command -v docker > /dev/null && docker compose version > /dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose > /dev/null; then echo "docker-compose"; else echo "docker compose"; fi)

.PHONY: help install install-dev install-full uv-install uv-install-dev uv-install-docs frontend-install frontend-build \
        dev run run-bare test lint fmt build publish-test publish clean stop \
        benchmark benchmark-quick benchmark-report \
        vercel vercel-build vercel-deploy \
        build-container run-container stop-container logs-container clean-container publish-container \
        extension-install extension-compile extension-package extension-publish publish-extension \
        mcp mcp-down mcp-logs gateway gateway-down gateway-logs gateway-register \
        install-mcp run-mcp run-all run-all-local stop-mcp logs-mcp sync-mcp uninstall-mcp \
        fix-line-endings install-mcp-workflows register-mcp-servers \
        stop-soft stop-all smoke-mcp

## Show available targets
help:
	@echo ""
	@echo "GitPilot Make targets"
	@echo "---------------------"
	@echo "  make install          Install runtime deps + frontend + MCP stack"
	@echo "  make install-dev      Install developer/test tooling"
	@echo "  make install-full     Install runtime + dev/docs tooling + MCP stack"
	@echo "  make uv-install       Create/refresh Python env with runtime deps only"
	@echo "  make uv-install-dev   Add developer/test tooling via uv"
	@echo "  make uv-install-docs  Add documentation tooling via uv"
	@echo "  make frontend-install Install frontend npm dependencies"
	@echo "  make frontend-build   Build React/Vite frontend into gitpilot/web"
	@echo "  make dev              Alias for install-dev"
	@echo "  make run              Run MCP stack + GitPilot backend/frontend"
	@echo "  make run-bare         Run GitPilot backend + frontend WITHOUT MCP (no Docker required)"
	@echo "  make stop             Stop all processes on ports 8000 and 5173"
	@echo "  make test             Run tests with pytest via uv"
	@echo "  make benchmark        Run code generation benchmark (all tiers)"
	@echo "  make benchmark-quick  Run quick benchmark (tier 1 smoke test)"
	@echo "  make benchmark-report Run benchmark + save HTML dashboard to reports/"
	@echo "  make lint             Lint codebase with ruff via uv"
	@echo "  make fmt              Format codebase with ruff via uv"
	@echo "  make build            Build wheel and sdist (includes built frontend)"
	@echo "  make publish-test     Upload distribution to TestPyPI with twine via uv"
	@echo "  make publish          Upload distribution to PyPI with twine via uv"
	@echo "  make clean            Remove build artifacts and cache directories"
	@echo "  make vercel           Run Vercel dev server locally (test deployment)"
	@echo "  make vercel-build     Test Vercel build locally without deploying"
	@echo "  make vercel-deploy    Deploy to Vercel (requires authentication)"
	@echo ""
	@echo "Docker Container Commands:"
	@echo "  make build-container  Build Docker containers for backend and frontend"
	@echo "  make run-container    Run both containers with docker-compose"
	@echo "  make stop-container   Stop and remove all containers"
	@echo "  make logs-container   View logs from all containers"
	@echo "  make clean-container  Remove containers, images, and volumes"
	@echo "  make publish-container Publish Docker images to Docker Hub"
	@echo ""
	@echo "VS Code Extension Commands:"
	@echo "  make extension-install   Install extension npm dependencies"
	@echo "  make extension-compile   Compile TypeScript to JavaScript"
	@echo "  make extension-package   Package extension into .vsix file"
	@echo "  make extension-publish   Publish extension to VS Code Marketplace"
	@echo "  make publish-extension   Alias for extension-publish"
	@echo ""
	@echo "  Extension publish requires VSCE_PAT (Azure DevOps Personal Access Token)"
	@echo "  with Marketplace > Manage scope."
	@echo ""
	@echo "  Usage:"
	@echo "    make publish-extension VSCE_PAT=your-pat-here"
	@echo "    # or"
	@echo "    export VSCE_PAT=your-pat-here"
	@echo "    make publish-extension"
	@echo ""
	@echo "MCP Deployment Commands:"
	@echo "  make mcp              Start GitPilot MCP server (A2A endpoints only)"
	@echo "  make mcp-down         Stop GitPilot MCP server"
	@echo "  make mcp-logs         View GitPilot MCP server logs"
	@echo ""
	@echo "MCP Gateway (Optional - Full ContextForge Stack):"
	@echo "  make gateway          Start GitPilot + MCP ContextForge gateway"
	@echo "  make gateway-down     Stop MCP ContextForge gateway stack"
	@echo "  make gateway-logs     View MCP ContextForge gateway logs"
	@echo "  make gateway-register Register GitPilot agent in ContextForge"
	@echo ""

## High-level install: runtime backend + frontend + MCP stack.
## GitPilot uses the MCP stack by default, so keep MCP in the happy path while
## leaving heavyweight developer/docs tooling opt-in.
install: uv-install frontend-install install-mcp
	@echo "✅ Backend runtime (uv), frontend (npm) and MCP env ready."
	@echo "   Run 'make run' to start MCP Context Forge + GitPilot."
	@echo "   No Docker?  Use 'make run-bare' to start GitPilot without MCP."
	@echo "   Optional:   'make install-dev' for test/lint/build tooling."

## Custom developer install: add dev/test/build tooling when you need it.
install-dev: uv-install-dev frontend-install
	@echo "✅ Developer tooling ready."

## Full local workstation install: runtime + MCP + dev/docs tooling.
install-full: install
	@echo "🔧 Syncing Python environment with dev + docs tooling..."
	@$(UV_ENV) $(UV) sync --extra dev --extra docs
	@echo "✅ Full local environment ready."
	@echo "   Run 'make run-all' to start GitPilot plus the MCP stack."

## Create / sync the environment with uv (runtime dependencies only).
uv-install:
	@echo "🔧 Syncing Python environment with uv (runtime deps only)..."
	@$(UV_ENV) $(UV) sync
	@echo "✅ Python runtime environment ready."
	@echo "⚡ Precompiling bytecode for faster startup (WSL/HF Spaces)..."
	@$(UV_ENV) $(UV) run --no-dev python -m compileall -q -j 4 gitpilot/ 2>/dev/null || true
	@echo "✅ Bytecode cache warmed."

## Add developer/test/build tooling without docs dependencies.
uv-install-dev:
	@echo "🔧 Syncing Python environment with dev/test tooling..."
	@$(UV_ENV) $(UV) sync --extra dev
	@echo "✅ Python developer environment ready."

## Add docs tooling only when building or serving documentation.
uv-install-docs:
	@echo "🔧 Syncing Python environment with docs tooling..."
	@$(UV_ENV) $(UV) sync --extra docs
	@echo "✅ Python docs environment ready."

## Install frontend dependencies
frontend-install:
	@echo "📦 Installing frontend dependencies (npm)..."
	@if [ -f frontend/package-lock.json ] && [ ! -d frontend/node_modules ]; then \
		cd frontend && npm ci --prefer-offline --no-audit --no-fund; \
	else \
		cd frontend && npm install --prefer-offline --no-audit --no-fund; \
	fi
	@echo "✅ Frontend dependencies installed."

## Build the React/Vite frontend and copy dist -> gitpilot/web
frontend-build: frontend-install
	@echo "🛠  Building frontend (Vite)..."
	@cd frontend && npm run build
	@echo "📂 Copying frontend/dist into gitpilot/web..."
	@$(PYTHON) -c "import shutil, pathlib; src = pathlib.Path('frontend')/'dist'; dst = pathlib.Path('gitpilot')/'web'; shutil.rmtree(dst, ignore_errors=True); shutil.copytree(src, dst)"
	@echo "✅ Frontend build complete (gitpilot/web)."

## Developer convenience alias
dev: install-dev

## Run GitPilot from the uv-managed environment (MCP stack + backend + frontend).
## Idempotent: `run-mcp` starts/keeps Context Forge healthy first; if a
## GitPilot backend is already responding on :$(PORT)
## (because you ran `make run` earlier in another tab, or `make run-all`
## was re-invoked), we skip the backend boot and go straight to the
## frontend dev server. The port-in-use check only fires when the port
## is held by *something else*.
##
## No Docker?  Use `make run-bare` for the Docker-free path: it starts
## GitPilot backend + frontend without the MCP stack.  The UI will show
## the gateway as Unreachable but everything else works.
run: run-mcp run-bare

## Docker-free run path.  Starts GitPilot backend + frontend without
## the MCP stack — useful on Hugging Face Spaces, CI smoke runs, and
## any environment where Docker is unavailable.  The MCP Servers tab
## will show the gateway as Unreachable; clicking Sync is a no-op.
run-bare:
	@echo "🚀 Starting GitPilot on http://127.0.0.1:$(PORT)..."
	@# 1. Already a healthy GitPilot? → skip backend boot, go straight to frontend.
	@if curl -sf http://127.0.0.1:$(PORT)/api/ping > /dev/null 2>&1; then \
		echo "✅ GitPilot backend already running on :$(PORT) — skipping start."; \
		echo "🎨 Starting frontend dev server on http://localhost:5173..."; \
		cd frontend && exec npm run dev -- --open; \
	fi
	@# 2. Port held by something *else*? → stop and ask the user to clean up.
	@if lsof -i:$(PORT) -sTCP:LISTEN > /dev/null 2>&1 || \
	    nc -z 127.0.0.1 $(PORT) > /dev/null 2>&1; then \
		echo "⚠️  Port $(PORT) is held by a non-GitPilot process. Run 'make stop' first."; \
		exit 1; \
	fi
	@trap 'kill 0' EXIT; \
	$(UV_ENV) $(UV) run --no-dev python -m gitpilot serve --host 127.0.0.1 --port $(PORT) --no-open & \
	BACKEND_PID=$$!; \
	echo "⏳ Waiting for backend to be ready (up to 60s for WSL/first-start)..."; \
	READY=0; \
	for i in $$(seq 1 30); do \
		if curl -sf http://127.0.0.1:$(PORT)/api/ping > /dev/null 2>&1; then \
			echo "✅ Backend is ready after $$((i * 2))s"; \
			READY=1; \
			break; \
		fi; \
		if [ $$((i % 5)) -eq 0 ]; then \
			echo "   ... still waiting ($$((i * 2))s elapsed)"; \
		fi; \
		sleep 2; \
	done; \
	if [ $$READY -eq 0 ]; then \
		echo "⚠️  Backend took longer than 60s. Starting frontend anyway — the frontend"; \
		echo "    will keep polling /api/ping and recover when the backend comes online."; \
	fi; \
	echo "🎨 Starting frontend dev server on http://localhost:5173..."; \
	cd frontend && npm run dev -- --open

## Stop all running processes (ports 8000 and 5173) AND the MCP stack.
## Now that `make run` starts the MCP Context Forge stack by default, `make
## stop` is symmetric: it stops both GitPilot and Forge.  `stop-mcp` is
## idempotent — running it when nothing is up is a clean no-op.
stop:
	@echo "🛑 Attempting to stop processes on ports $(PORT) and 5173..."

	@# Stop anything on backend port $(PORT)
	@pids=$$(sudo lsof -t -i:$(PORT) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then \
		echo "Killing $$pids on port $(PORT)..."; \
		sudo kill -9 $$pids; \
	else \
		echo "No process found on port $(PORT)."; \
	fi

	@# Stop anything on frontend port 5173
	@pids=$$(sudo lsof -t -i:5173 -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then \
		echo "Killing $$pids on port 5173..."; \
		sudo kill -9 $$pids; \
	else \
		echo "No process found on port 5173."; \
	fi

	@# Tear down the MCP stack started by `make run` (idempotent).
	@$(MAKE) --no-print-directory stop-mcp
	@echo "✅ GitPilot + MCP stack stopped."

## Soft-stop GitPilot WITHOUT sudo. Only kills processes the current user
## owns; never prompts for a password. Suitable for `make run-all` to call
## as a pre-step so a stale backend can't hide newly-pulled code paths.
stop-soft:
	@echo "🛑 Stopping user-owned GitPilot processes on :$(PORT) and :5173..."
	@for port in $(PORT) 5173; do \
		pids=$$(lsof -t -i:$$port -sTCP:LISTEN 2>/dev/null || true); \
		if [ -n "$$pids" ]; then \
			for pid in $$pids; do \
				if [ -O /proc/$$pid 2>/dev/null ] || kill -0 $$pid 2>/dev/null; then \
					kill -TERM $$pid 2>/dev/null && \
						echo "  TERM $$pid (port $$port)" || true; \
				fi; \
			done; \
			sleep 1; \
			pids=$$(lsof -t -i:$$port -sTCP:LISTEN 2>/dev/null || true); \
			[ -n "$$pids" ] && for pid in $$pids; do kill -KILL $$pid 2>/dev/null || true; done; \
		fi; \
	done
	@echo "✅ Soft-stop done."

## One-command full teardown: GitPilot (no sudo) + MCP stack.
stop-all: stop-soft stop-mcp
	@echo "✅ GitPilot + MCP stack stopped."


## Run tests
test:
	@echo "🧪 Running tests with isolated GitPilot config..."
	@TMP_CFG="$$(mktemp -d)"; \
	echo "Using GITPILOT_CONFIG_DIR=$$TMP_CFG"; \
	GITPILOT_CONFIG_DIR="$$TMP_CFG" GITPILOT_LITE_MODE=0 PYTHONWARNINGS="ignore::RuntimeWarning" $(UV_ENV) $(UV) run --extra dev pytest; \
	STATUS=$$?; \
	rm -rf "$$TMP_CFG"; \
	exit $$STATUS

test-fast:
	@echo "🧪 Running tests (no isolation)..."
	@$(UV_ENV) $(UV) run --extra dev pytest

## Coverage gate — Batch P1-B
## Enforces the >= 80 % threshold on the gated modules listed in
## pyproject.toml [tool.coverage.run] include.  Use `make coverage` locally;
## CI runs the same command.  `make coverage-full` reports the whole tree
## without enforcement, useful for spotting candidates to add to the gate.
coverage:
	@echo "📈 Running coverage gate (gated modules only)..."
	@TMP_CFG="$$(mktemp -d)"; \
	echo "Using GITPILOT_CONFIG_DIR=$$TMP_CFG"; \
	GITPILOT_CONFIG_DIR="$$TMP_CFG" GITPILOT_LITE_MODE=0 PYTHONWARNINGS="ignore::RuntimeWarning" \
		$(UV_ENV) $(UV) run --extra dev pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html; \
	STATUS=$$?; \
	rm -rf "$$TMP_CFG"; \
	exit $$STATUS

coverage-html: coverage
	@echo "📈 HTML report: htmlcov/index.html"

coverage-full:
	@echo "📈 Full-tree coverage report (informational, no gate)..."
	@TMP_CFG="$$(mktemp -d)"; \
	GITPILOT_CONFIG_DIR="$$TMP_CFG" GITPILOT_LITE_MODE=0 PYTHONWARNINGS="ignore::RuntimeWarning" \
		$(UV_ENV) $(UV) run --extra dev pytest --cov=gitpilot --cov-report=term --no-cov-on-fail --cov-config=/dev/null; \
	rm -rf "$$TMP_CFG"

## Type-check gate — Batch P1-C
## Strict mypy on the modules listed in mypy.ini.  Run via `make typecheck`.
typecheck:
	@echo "🔎 Running mypy --strict on gated modules..."
	@$(UV_ENV) $(UV) run --extra dev mypy --config-file mypy.ini

## Docs site — Batch P4-D
## mkdocs serve + mkdocs build (requires mkdocs-material; install with
## `pip install mkdocs mkdocs-material` or via uv).
docs-serve:
	@echo "📚 Serving docs at http://127.0.0.1:8001 ..."
	@$(UV_ENV) $(UV) run --extra docs mkdocs serve -a 127.0.0.1:8001

docs-build:
	@echo "📚 Building static docs site -> site/ ..."
	@$(UV_ENV) $(UV) run --extra docs mkdocs build --strict

linkcheck:
	@echo "🔗 Running in-repo markdown link checker..."
	@$(UV_ENV) $(UV) run --extra dev pytest tests/test_docs_links.py -q

## Supply chain — Batch P4-E
## Generate a CycloneDX SBOM for the installed Python deps.  Output is
## artefacts/sbom.json.  Run via `make sbom`.  CI uploads it alongside
## the signed wheel.
sbom:
	@echo "🧾 Generating CycloneDX SBOM..."
	@mkdir -p artefacts
	@$(UV_ENV) $(UV) run --extra dev python -m cyclonedx_py environment \
		--output-format json \
		--output-file artefacts/sbom.json \
		--PEP-639 || \
		(echo "Falling back to pip freeze SBOM..." && \
		 $(UV_ENV) $(UV) run --extra dev python scripts/sbom_fallback.py > artefacts/sbom.json)
	@echo "✅ artefacts/sbom.json"

sbom-verify:
	@echo "🧾 Verifying artefacts/sbom.json shape..."
	@$(UV_ENV) $(UV) run --no-dev python -c "import json,sys; d=json.load(open('artefacts/sbom.json')); \
		assert d.get('bomFormat')=='CycloneDX', 'Not a CycloneDX SBOM'; \
		print(f'OK: {len(d.get(\"components\", []))} components')"

audit-npm:
	@echo "🛡  npm audit (dev deps)..."
	@npm --prefix frontend audit --omit=dev --audit-level=high || \
		(echo '⚠️  npm audit found issues; see report above.' && exit 1)

## Benchmark: code generation stress test
benchmark:
	@echo "📊 Running code generation benchmark (all tiers)..."
	@$(UV_ENV) $(UV) run --extra dev python tests/benchmark.py --model $${GITPILOT_OLLAMA_MODEL:-llama3} --timeout $${BENCHMARK_TIMEOUT:-300}

benchmark-quick:
	@echo "📊 Running quick benchmark (tier 1 only)..."
	@$(UV_ENV) $(UV) run --extra dev python tests/benchmark.py --quick --model $${GITPILOT_OLLAMA_MODEL:-llama3} --timeout $${BENCHMARK_TIMEOUT:-120}

benchmark-report:
	@echo "📊 Running benchmark with HTML dashboard..."
	@mkdir -p reports
	@$(UV_ENV) $(UV) run --extra dev python tests/benchmark.py \
		--model $${GITPILOT_OLLAMA_MODEL:-llama3} \
		--timeout $${BENCHMARK_TIMEOUT:-300} \
		--output reports/benchmark-results.json \
		--dashboard reports/benchmark-dashboard.html
	@echo "📈 Results: reports/benchmark-results.json"
	@echo "📈 Dashboard: reports/benchmark-dashboard.html"

## Lint code
lint:
	@echo "🔍 Linting with ruff..."
	@$(UV_ENV) $(UV) run --extra dev ruff check gitpilot

## Format code
fmt:
	@echo "🎨 Formatting with ruff..."
	@$(UV_ENV) $(UV) run --extra dev ruff format gitpilot

## Build wheel + sdist (includes built frontend)
build: frontend-build
	@echo "📦 Building distribution (wheel + sdist)..."
	@$(UV_ENV) $(UV) run --extra dev $(PYTHON) -m build
	@echo "✅ Build artifacts are in ./dist"

## Upload to TestPyPI
publish-test:
	@echo "🚚 Uploading to TestPyPI..."
	@$(UV_ENV) $(UV) run --extra dev twine upload -r testpypi dist/*
	@echo "✅ Uploaded to TestPyPI"

## Upload to PyPI
publish:
	@echo "🚀 Uploading to PyPI..."
	@$(UV_ENV) $(UV) run --extra dev twine upload dist/*
	@echo "✅ Uploaded to PyPI"

## Clean build artifacts and caches (cross-platform)
clean:
	@echo "🧹 Cleaning build artifacts and caches..."
	@$(PYTHON) -c "import shutil, pathlib; \
paths = ['build', 'dist', '.pytest_cache', '.ruff_cache']; \
[shutil.rmtree(p, ignore_errors=True) for p in paths]; \
[shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').glob('*.egg-info')]"
	@echo "✅ Clean complete"

## Run Vercel dev server locally (simulates Vercel deployment environment)
vercel: frontend-install
	@echo "🚀 Starting Vercel dev server locally..."
	@echo "📝 This simulates the Vercel deployment environment"
	@echo "🌐 Frontend will be available at http://localhost:3000"
	@vercel dev

## Test Vercel build locally without deploying
vercel-build: frontend-install
	@echo "🔨 Testing Vercel build locally..."
	@vercel build
	@echo "✅ Vercel build test complete"

## Deploy to Vercel (requires vercel login)
vercel-deploy:
	@echo "🚀 Deploying to Vercel..."
	@vercel --prod
	@echo "✅ Deployment complete"

## Build Docker containers for backend and frontend
build-container:
	@echo "🐳 Building Docker containers..."
	@if [ ! -f .env ]; then \
		echo "⚠️  Warning: .env file not found. Creating from template..."; \
		cp .env.example .env; \
		echo "📝 Please edit .env and add your credentials before running containers"; \
	fi
	@$(DOCKER_COMPOSE) build
	@echo "✅ Docker containers built successfully"
	@echo ""
	@echo "Images created:"
	@docker images | grep gitpilot || echo "  (no gitpilot images found)"

## Run both containers with docker-compose
run-container:
	@echo "🚀 Starting GitPilot containers..."
	@if [ ! -f .env ]; then \
		echo "❌ Error: .env file not found!"; \
		echo "📝 Please copy .env.example to .env and configure your credentials"; \
		exit 1; \
	fi
	@echo "📝 Backend will be available at http://localhost:8000"
	@echo "📝 Frontend will be available at http://localhost:3000"
	@echo ""
	@$(DOCKER_COMPOSE) up -d
	@echo ""
	@echo "✅ Containers started successfully!"
	@echo ""
	@echo "View logs: make logs-container"
	@echo "Stop containers: make stop-container"

## Stop and remove all containers
stop-container:
	@echo "🛑 Stopping GitPilot containers..."
	@$(DOCKER_COMPOSE) down
	@echo "✅ Containers stopped and removed"

## View logs from all containers
logs-container:
	@echo "📋 Viewing container logs (Ctrl+C to exit)..."
	@$(DOCKER_COMPOSE) logs -f

## Remove containers, images, and volumes
clean-container:
	@echo "🧹 Cleaning up Docker resources..."
	@$(DOCKER_COMPOSE) down -v --rmi all
	@echo "✅ Docker cleanup complete"

## Publish Docker images to Docker Hub for deployment
publish-container:
	@echo "🚀 Publishing Docker containers to Docker Hub..."
	@echo ""
	@# Check if DOCKERHUB_USERNAME is set
	@if [ -z "$(DOCKERHUB_USERNAME)" ]; then \
		echo "❌ Error: DOCKERHUB_USERNAME not set!"; \
		echo ""; \
		echo "Usage:"; \
		echo "  export DOCKERHUB_USERNAME=your-dockerhub-username"; \
		echo "  make publish-container"; \
		echo ""; \
		echo "Or:"; \
		echo "  make publish-container DOCKERHUB_USERNAME=your-dockerhub-username"; \
		echo ""; \
		exit 1; \
	fi
	@echo "📦 Docker Hub username: $(DOCKERHUB_USERNAME)"
	@echo ""
	@# Login to Docker Hub
	@echo "🔐 Please login to Docker Hub..."
	@docker login
	@echo ""
	@# Build containers if not already built
	@echo "🔨 Building containers..."
	@$(DOCKER_COMPOSE) build
	@echo ""
	@# Tag backend
	@echo "🏷️  Tagging backend image..."
	@docker tag gitpilot-backend $(DOCKERHUB_USERNAME)/gitpilot-backend:latest
	@docker tag gitpilot-backend $(DOCKERHUB_USERNAME)/gitpilot-backend:$$(date +%Y%m%d-%H%M%S)
	@echo "   → $(DOCKERHUB_USERNAME)/gitpilot-backend:latest"
	@echo ""
	@# Tag frontend
	@echo "🏷️  Tagging frontend image..."
	@docker tag gitpilot-frontend $(DOCKERHUB_USERNAME)/gitpilot-frontend:latest
	@docker tag gitpilot-frontend $(DOCKERHUB_USERNAME)/gitpilot-frontend:$$(date +%Y%m%d-%H%M%S)
	@echo "   → $(DOCKERHUB_USERNAME)/gitpilot-frontend:latest"
	@echo ""
	@# Push backend
	@echo "📤 Pushing backend to Docker Hub..."
	@docker push $(DOCKERHUB_USERNAME)/gitpilot-backend:latest
	@echo ""
	@# Push frontend
	@echo "📤 Pushing frontend to Docker Hub..."
	@docker push $(DOCKERHUB_USERNAME)/gitpilot-frontend:latest
	@echo ""
	@echo "✅ Successfully published to Docker Hub!"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "🎉 Your images are now available at:"
	@echo ""
	@echo "Backend:"
	@echo "  docker.io/$(DOCKERHUB_USERNAME)/gitpilot-backend:latest"
	@echo ""
	@echo "Frontend:"
	@echo "  docker.io/$(DOCKERHUB_USERNAME)/gitpilot-frontend:latest"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "📋 Next Steps:"
	@echo ""
	@echo "1. Deploy Backend on Render:"
	@echo "   → Go to https://dashboard.render.com"
	@echo "   → New → Web Service"
	@echo "   → 'Deploy an existing image from a registry'"
	@echo "   → Image URL: docker.io/$(DOCKERHUB_USERNAME)/gitpilot-backend:latest"
	@echo "   → Add environment variables (see DEPLOYMENT_RENDER.md)"
	@echo ""
	@echo "2. Get your backend URL:"
	@echo "   → https://gitpilot-backend-xxx.onrender.com"
	@echo ""
	@echo "3. Configure Vercel frontend:"
	@echo "   → Vercel Dashboard → Settings → Environment Variables"
	@echo "   → Add: VITE_BACKEND_URL=https://gitpilot-backend-xxx.onrender.com"
	@echo ""
	@echo "4. Redeploy Vercel to use new backend URL"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# =============================================================================
# VS Code Extension (Build, Package, Publish)
# =============================================================================

EXTENSION_DIR := extensions/vscode
VSCE          ?= npx vsce

## Install VS Code extension npm dependencies
extension-install:
	@echo "📦 Installing VS Code extension dependencies..."
	@cd $(EXTENSION_DIR) && npm install
	@echo "✅ Extension dependencies installed."

## Compile TypeScript to JavaScript
extension-compile: extension-install
	@echo "🔧 Compiling VS Code extension TypeScript..."
	@cd $(EXTENSION_DIR) && npm run compile
	@echo "✅ Extension compiled successfully."

## Package extension into .vsix file
extension-package: extension-compile
	@echo "📦 Packaging VS Code extension..."
	@cd $(EXTENSION_DIR) && $(VSCE) package
	@echo ""
	@echo "✅ Extension packaged successfully!"
	@echo ""
	@echo "📁 VSIX file:"
	@ls -lh $(EXTENSION_DIR)/*.vsix 2>/dev/null || echo "  (no .vsix found)"
	@echo ""
	@echo "Install locally with:"
	@echo "  code --install-extension $(EXTENSION_DIR)/gitpilot-vscode-*.vsix"

## Publish extension to VS Code Marketplace
extension-publish: extension-compile
	@echo "🚀 Publishing VS Code extension to Marketplace..."
	@echo ""
	@if [ -z "$(VSCE_PAT)" ]; then \
		echo "❌ Error: VSCE_PAT not set!"; \
		echo ""; \
		echo "You need an Azure DevOps Personal Access Token with"; \
		echo "Marketplace > Manage scope."; \
		echo ""; \
		echo "Usage:"; \
		echo "  make publish-extension VSCE_PAT=your-pat-here"; \
		echo ""; \
		echo "Or:"; \
		echo "  export VSCE_PAT=your-pat-here"; \
		echo "  make publish-extension"; \
		echo ""; \
		echo "To create a PAT:"; \
		echo "  1. Go to https://dev.azure.com/_usersSettings/tokens"; \
		echo "  2. New Token → Organization: All accessible organizations"; \
		echo "  3. Scopes → Show all → check Marketplace > Manage"; \
		echo ""; \
		exit 1; \
	fi
	@cd $(EXTENSION_DIR) && $(VSCE) publish -p "$(VSCE_PAT)"
	@echo ""
	@echo "✅ Extension published successfully!"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "🎉 Your extension is live at:"
	@echo "  https://marketplace.visualstudio.com/items?itemName=ruslanmv.gitpilot-vscode"
	@echo ""
	@echo "Search 'GitPilot' in VS Code Extensions to install."
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

## Alias: publish-extension → extension-publish
publish-extension: extension-publish

# =============================================================================
# MCP Server Deployment (GitPilot with A2A endpoints - Simple MCP Server)
# =============================================================================

mcp:
	@echo "Starting GitPilot MCP server (A2A endpoints)..."
	@if [ ! -f .env.a2a ]; then \
		echo "Creating .env.a2a from .env.a2a.example..."; \
		cp .env.a2a.example .env.a2a; \
		echo "IMPORTANT: Edit .env.a2a and set GITPILOT_A2A_SHARED_SECRET"; \
	fi
	@$(DOCKER_COMPOSE) -f docker-compose.yml --env-file .env.a2a up -d --build
	@echo ""
	@echo "✅ GitPilot MCP server started successfully!"
	@echo ""
	@echo "MCP endpoints available at:"
	@echo "  http://localhost:8000/a2a/invoke        - JSON-RPC + envelope"
	@echo "  http://localhost:8000/a2a/v1/invoke     - Versioned endpoint"
	@echo "  http://localhost:8000/a2a/health        - Health check"
	@echo "  http://localhost:8000/a2a/manifest      - Capability discovery"
	@echo ""
	@echo "This is a simple MCP server. For full MCP ContextForge gateway, use:"
	@echo "  make gateway"

mcp-down:
	@echo "Stopping GitPilot MCP server..."
	@$(DOCKER_COMPOSE) -f docker-compose.yml down
	@echo "✅ GitPilot MCP server stopped"

mcp-logs:
	@echo "📋 Viewing GitPilot MCP server logs (Ctrl+C to exit)..."
	@$(DOCKER_COMPOSE) -f docker-compose.yml logs -f

# =============================================================================
# MCP Gateway Deployment (GitPilot + MCP ContextForge - OPTIONAL Full Stack)
# =============================================================================

gateway:
	@echo "Starting GitPilot + MCP ContextForge gateway stack..."
	@echo ""
	@if [ ! -d deploy/a2a-mcp/mcp-context-forge ]; then \
		echo "❌ ERROR: MCP ContextForge source not found."; \
		echo ""; \
		echo "To use the full MCP gateway, you need to:"; \
		echo "1. Clone/download MCP ContextForge"; \
		echo "2. Place it at: deploy/a2a-mcp/mcp-context-forge"; \
		echo ""; \
		echo "If you just need a simple MCP server (A2A endpoints), use:"; \
		echo "  make mcp"; \
		echo ""; \
		exit 1; \
	fi
	@cd deploy/a2a-mcp && chmod +x setup.sh && ./setup.sh

gateway-down:
	@echo "Stopping MCP ContextForge gateway stack..."
	@cd deploy/a2a-mcp && $(DOCKER_COMPOSE) -f docker-compose.a2a-mcp.yml down
	@echo "✅ MCP ContextForge gateway stopped"

gateway-logs:
	@echo "📋 Viewing MCP ContextForge gateway logs (Ctrl+C to exit)..."
	@cd deploy/a2a-mcp && $(DOCKER_COMPOSE) -f docker-compose.a2a-mcp.yml logs -f

gateway-register:
	@echo "Registering GitPilot agent in ContextForge gateway..."
	@if [ -z "$$CF_ADMIN_BEARER" ]; then \
		echo "❌ ERROR: CF_ADMIN_BEARER environment variable required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  CF_ADMIN_BEARER=<jwt> GITPILOT_A2A_SECRET=<secret> make gateway-register"; \
		echo ""; \
		exit 1; \
	fi
	@if [ -z "$$GITPILOT_A2A_SECRET" ]; then \
		echo "❌ ERROR: GITPILOT_A2A_SECRET environment variable required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  CF_ADMIN_BEARER=<jwt> GITPILOT_A2A_SECRET=<secret> make gateway-register"; \
		echo ""; \
		exit 1; \
	fi
	@cd deploy/a2a-mcp && chmod +x register_agent.sh && ./register_agent.sh

# =============================================================================
# MCP Context Forge stack (additive services; default `make run` starts it)
# -----------------------------------------------------------------------------
# `make install` includes this target because GitPilot uses the MCP stack by
# default. The script is skip-safe and incremental: it only clones/builds what
# is missing unless MCP_UPDATE=1 or MCP_BUILD=1 is supplied.
# =============================================================================

## Pull MCP Context Forge stack images and seed .mcp.env (idempotent)
install-mcp:
	@bash scripts/install-mcp.sh

## Bring up MCP Context Forge + 3 reference MCP servers (postgre, milvus, inspector)
run-mcp: install-mcp
	@if [ ! -f .mcp.env ]; then \
		echo "❌ .mcp.env missing. Run 'make install-mcp' first."; exit 1; \
	fi
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "❌ Docker is required because 'make run' starts MCP Context Forge by default."; \
		echo "   Install/start Docker Desktop, then rerun 'make run'."; \
		echo "   Or run without MCP:  make run-bare"; \
		exit 1; \
	fi
	@if ! docker compose version >/dev/null 2>&1; then \
		echo "❌ Docker Compose v2 is required for the MCP stack."; \
		echo "   Upgrade Docker Desktop or install the compose v2 plugin."; \
		echo "   Or run without MCP:  make run-bare"; \
		exit 1; \
	fi
	@if ! docker info >/dev/null 2>&1; then \
		echo "❌ Docker daemon is not running; MCP Context Forge cannot start."; \
		echo "   Start Docker Desktop, then rerun 'make run'."; \
		echo "   Or run without MCP:  make run-bare"; \
		exit 1; \
	fi
	@echo "🚀 Starting MCP Context Forge stack..."
	docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp up -d
	@set -a; . ./.mcp.env; set +a; \
	forge_port="$${MCP_FORGE_PORT:-4444}"; \
	echo "⏳ Waiting for MCP Context Forge on http://localhost:$$forge_port/health..."; \
	ready=0; \
	for i in $$(seq 1 60); do \
		if curl -fsS "http://localhost:$$forge_port/health" >/dev/null 2>&1; then \
			echo "✅ MCP Context Forge reachable after $$((i * 2))s."; \
			ready=1; \
			break; \
		fi; \
		sleep 2; \
	done; \
	if [ $$ready -ne 1 ]; then \
		echo "❌ MCP Context Forge did not become host-reachable on http://localhost:$$forge_port."; \
		echo "   Tail logs with: make logs-mcp"; \
		exit 1; \
	fi
	@set -a; . ./.mcp.env; set +a; \
	echo "✅ Forge: http://localhost:$${MCP_FORGE_PORT:-4444}"; \
	echo "   Postgre: http://localhost:$${MCP_POSTGRE_PORT:-8080}"; \
	echo "   Inspector: http://localhost:$${MCP_INSPECTOR_PORT:-8081}"; \
	echo "   Milvus (opt-in): docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile milvus up -d"
	@bash scripts/register-mcp-servers.sh

## Register the 3 MCP servers with Forge (idempotent; called by run-mcp).
register-mcp-servers:
	@bash scripts/register-mcp-servers.sh

## One-shot with a forced GitPilot backend restart.
##
## `make run` now starts the MCP stack by default. Keep `run-all` as the
## explicit "fresh backend" path for users who just pulled code, changed
## config, or rebuilt MCP images and do not want to reuse an old backend.
run-all: stop-soft run

## Local-first: rebuild every MCP image from the cloned mcp-stack/ source
## (mirrors HomePilot's docker-compose.mcp.yml `build:` pattern), then run.
## Use this after pulling new commits in any mcp-stack/<repo>/ checkout
## or when iterating on a local source change. Forces a fresh build
## (`--no-cache`), so 'context.git' changes are guaranteed picked up,
## and `--pull=false` keeps the build registry-free.
run-all-local:
	@if [ ! -d mcp-stack ]; then \
		echo "❌ mcp-stack/ missing. Run 'make install-mcp' first to clone the upstream MCP repos."; \
		exit 1; \
	fi
	@echo "🔨 Rebuilding MCP images from local mcp-stack/ sources (no cache)..."
	docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp build --no-cache --pull=false
	@echo "✅ Local rebuild complete. Restarting full stack..."
	@$(MAKE) --no-print-directory stop-soft
	@$(MAKE) --no-print-directory stop-mcp 2>/dev/null || true
	@$(MAKE) --no-print-directory run-all

## Stop the MCP stack (volumes preserved)
stop-mcp:
	@docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp down 2>/dev/null || true
	@echo "🛑 MCP stack stopped (volumes kept). 'make uninstall-mcp' to remove data."

## Tail logs from the MCP stack
logs-mcp:
	@docker compose --env-file .mcp.env -f docker-compose.mcp.yml --profile mcp logs -f --tail=100

## Trigger a sync from the running GitPilot (REST POST /api/mcp/sync)
sync-mcp:
	@bash scripts/sync-mcp.sh

## Tear down the MCP stack and remove all images + volumes (prompts y/N)
uninstall-mcp:
	@bash scripts/uninstall-mcp.sh

## Recovery helper for Windows / WSL checkouts whose shell scripts and
## Makefiles got CRLF-converted by core.autocrlf. Idempotent and safe
## to run on a clean Linux/macOS checkout (no-op).
fix-line-endings:
	@echo "🔧 Stripping CRLF from shell scripts + Makefile (idempotent)..."
	@find scripts -name "*.sh" -type f -exec sed -i 's/\r$$//' {} + 2>/dev/null || true
	@sed -i 's/\r$$//' Makefile 2>/dev/null || true
	@sed -i 's/\r$$//' docker-compose*.yml 2>/dev/null || true
	@echo "✅ Line endings normalised. Run 'make install' again."

## Install the three MCP-server docker-publish workflows into each
## checkout under mcp-stack/. Commits locally; pushes only if
## GH_PAT_WORKFLOW is set (must have repo + workflow scopes). When it
## isn't, prints the per-repo 'git push' command so you can run it
## with your own auth.
install-mcp-workflows:
	@bash scripts/install-mcp-workflows.sh

## Post-deploy smoke test: hits every /health endpoint, runs a sync,
## and checks the agent_tools surface. Run after 'make run-all'.
## Add --milvus to also check the milvus profile.
smoke-mcp:
	@bash scripts/smoke-mcp.sh
