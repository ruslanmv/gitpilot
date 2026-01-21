# Makefile - GitPilot
# Backend (Python, uv) + Frontend (React/Vite)

.DEFAULT_GOAL := install

UV      ?= uv
PYTHON  ?= python3.11
PORT    ?= 8000

# Docker Compose command (prefer v2 over v1)
DOCKER_COMPOSE := $(shell if command -v docker > /dev/null && docker compose version > /dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose > /dev/null; then echo "docker-compose"; else echo "docker compose"; fi)

.PHONY: help install uv-install frontend-install frontend-build \
        dev run test lint fmt build publish-test publish clean stop \
        vercel vercel-build vercel-deploy \
        build-container run-container stop-container logs-container clean-container publish-container \
        mcp mcp-down mcp-logs gateway gateway-down gateway-logs gateway-register

## Show available targets
help:
	@echo ""
	@echo "GitPilot Make targets"
	@echo "---------------------"
	@echo "  make install          Install backend (uv) + frontend (npm install)"
	@echo "  make uv-install       Create/refresh Python env and install deps via uv"
	@echo "  make frontend-install Install frontend npm dependencies"
	@echo "  make frontend-build   Build React/Vite frontend into gitpilot/web"
	@echo "  make dev              Alias for install"
	@echo "  make run              Run GitPilot backend + frontend dev server"
	@echo "  make stop             Stop all processes on ports 8000 and 5173"
	@echo "  make test             Run tests with pytest via uv"
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

## High-level install: backend + frontend
install: uv-install frontend-install
	@echo "✅ Backend (uv) and frontend (npm) dependencies installed."

## Create / sync the environment with uv (all extras)
uv-install:
	@echo "🔧 Syncing Python environment with uv (all extras)..."
	@$(UV) sync --all-extras
	@echo "✅ Python environment ready."

## Install frontend dependencies
frontend-install:
	@echo "📦 Installing frontend dependencies (npm)..."
	@cd frontend && npm install
	@echo "✅ Frontend dependencies installed."

## Build the React/Vite frontend and copy dist -> gitpilot/web
frontend-build: frontend-install
	@echo "🛠  Building frontend (Vite)..."
	@cd frontend && npm run build
	@echo "📂 Copying frontend/dist into gitpilot/web..."
	@$(PYTHON) -c "import shutil, pathlib; src = pathlib.Path('frontend')/'dist'; dst = pathlib.Path('gitpilot')/'web'; shutil.rmtree(dst, ignore_errors=True); shutil.copytree(src, dst)"
	@echo "✅ Frontend build complete (gitpilot/web)."

## Developer convenience alias
dev: install

## Run GitPilot from the uv-managed environment (backend + frontend)
run:
	@echo "🚀 Starting GitPilot backend on http://127.0.0.1:$(PORT)..."
	@trap 'kill 0' EXIT; \
	$(UV) run python -m gitpilot serve --host 127.0.0.1 --port $(PORT) & \
	BACKEND_PID=$$!; \
	echo "⏳ Waiting for backend to be ready..."; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
		if curl -s http://127.0.0.1:$(PORT)/api/health > /dev/null 2>&1 || \
		   nc -z 127.0.0.1 $(PORT) > /dev/null 2>&1 || \
		   lsof -i:$(PORT) -sTCP:LISTEN > /dev/null 2>&1; then \
			echo "✅ Backend is ready!"; \
			break; \
		fi; \
		if [ $$i -eq 10 ]; then \
			echo "⚠️  Backend took longer than expected, starting frontend anyway..."; \
		fi; \
		sleep 1; \
	done; \
	echo "🎨 Starting frontend dev server on http://localhost:5173..."; \
	cd frontend && npm run dev

## Stop all running processes (ports 8000 and 5173)
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

	@echo "✅ Stop attempt complete."


## Run tests
test:
	@echo "🧪 Running tests with pytest..."
	@$(UV) run pytest

## Lint code
lint:
	@echo "🔍 Linting with ruff..."
	@$(UV) run ruff check gitpilot

## Format code
fmt:
	@echo "🎨 Formatting with ruff..."
	@$(UV) run ruff format gitpilot

## Build wheel + sdist (includes built frontend)
build: frontend-build
	@echo "📦 Building distribution (wheel + sdist)..."
	@$(UV) run $(PYTHON) -m build
	@echo "✅ Build artifacts are in ./dist"

## Upload to TestPyPI
publish-test:
	@echo "🚚 Uploading to TestPyPI..."
	@$(UV) run twine upload -r testpypi dist/*
	@echo "✅ Uploaded to TestPyPI"

## Upload to PyPI
publish:
	@echo "🚀 Uploading to PyPI..."
	@$(UV) run twine upload dist/*
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
# Desktop Installer Build (All-in-One Local Web Service)
# =============================================================================
# Builds: frontend -> embedded into gitpilot/web -> PyInstaller binary -> portable archives
#
# Usage:
#   make installer-build        Build everything (frontend + embed + binary)
#   make installer-verify       Verify binary and embedded UI
#   make installer-package      Make portable archives for current OS
#   make installer-windows      Build Windows Inno Setup installer (Windows only)

APP_NAME            ?= gitpilot
SPEC_FILE           ?= installer/pyinstaller/gitpilot.spec
DIST_DIR            ?= dist/$(APP_NAME)

GIT_DESCRIBE := $(shell git describe --tags --always --dirty 2>/dev/null || true)
GIT_SHA      := $(shell git rev-parse --short HEAD 2>/dev/null || true)
DATE_UTC     := $(shell date -u +%Y%m%d)
INSTALLER_VERSION ?= $(if $(strip $(GIT_DESCRIBE)),$(GIT_DESCRIBE),$(DATE_UTC)-$(GIT_SHA))

.PHONY: installer-deps installer-embed installer-binary installer-build installer-verify installer-package installer-windows installer-clean

## Install PyInstaller for building standalone binaries
installer-deps: uv-install
	@echo "📦 Installing PyInstaller..."
	@$(UV) pip install pyinstaller
	@echo "✅ PyInstaller ready."

## Embed frontend into backend (copy frontend/dist -> gitpilot/web)
installer-embed: frontend-build
	@echo "📂 Embedding frontend into gitpilot/web..."
	@$(UV) run $(PYTHON) installer/build/embed_frontend.py
	@echo "✅ Frontend embedded."

## Build PyInstaller binary
installer-binary: installer-deps installer-embed
	@echo "🔧 Building standalone binary with PyInstaller..."
	@$(UV) run $(PYTHON) -m PyInstaller "$(SPEC_FILE)"
	@echo "✅ Binary built: $(DIST_DIR)"

## Build everything for installer (frontend + embed + binary)
installer-build: installer-binary
	@echo "✅ Installer build complete."

## Verify bundle (binary exists + embedded UI present)
installer-verify: installer-build
	@echo "🔍 Verifying bundle..."
	@$(UV) run $(PYTHON) installer/build/verify_bundle.py
	@echo "✅ Bundle verified."

## Create portable archives for current OS
installer-package: installer-verify
	@echo "📦 Creating portable package..."
	@$(UV) run $(PYTHON) installer/build/package_portable.py --version "$(INSTALLER_VERSION)"
	@echo "✅ Artifacts created under dist_artifacts/"

## Build Windows Inno Setup installer (Windows only)
installer-windows: installer-verify
	@echo "🪟 Building Windows installer..."
	@$(UV) run $(PYTHON) installer/windows/build_installer.py --version "$(INSTALLER_VERSION)"
	@echo "✅ Windows installer built."

## Clean installer artifacts
installer-clean:
	@echo "🧹 Cleaning installer artifacts..."
	@rm -rf dist dist_artifacts build
	@echo "✅ Installer artifacts cleaned."
