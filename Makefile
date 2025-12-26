# Makefile - GitPilot
# Backend (Python, uv) + Frontend (React/Vite)

.DEFAULT_GOAL := install

UV      ?= uv
PYTHON  ?= python3.11
PORT    ?= 8000

.PHONY: help install uv-install frontend-install frontend-build \
        dev run test lint fmt build publish-test publish clean stop \
        vercel vercel-build vercel-deploy

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
	$(UV) run gitpilot serve --host 127.0.0.1 --port $(PORT) & \
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