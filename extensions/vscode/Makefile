SHELL := /bin/bash

EXT_NAME := gitpilot-vscode
NODE_MODULES := node_modules
DIST_DIR := dist
OUT_DIR := out
VSIX_FILE := $(shell ls -1 *.vsix 2>/dev/null | tail -n 1)

# Extension log location (macOS / Linux / Windows via WSL)
LOG_DIR_LINUX := $(HOME)/.config/Code/logs
LOG_DIR_MAC := $(HOME)/Library/Application\ Support/Code/logs
LOG_DIR := $(shell \
	if [ -d "$(LOG_DIR_LINUX)" ]; then echo "$(LOG_DIR_LINUX)"; \
	elif [ -d "$(LOG_DIR_MAC)" ]; then echo "$(LOG_DIR_MAC)"; \
	else echo ""; fi)

.DEFAULT_GOAL := help

.PHONY: help install clean compile watch lint test package reinstall-vsix run logs \
        check-env debug debug-watch debug-log debug-inspect debug-webview debug-status \
        debug-clean debug-check

help:
	@echo ""
	@echo "GitPilot VS Code Extension"
	@echo ""
	@echo "Build:"
	@echo "  make install         Install npm dependencies"
	@echo "  make compile         Compile the extension"
	@echo "  make watch           Compile in watch mode"
	@echo "  make lint            Run eslint"
	@echo "  make package         Build a .vsix package"
	@echo "  make reinstall-vsix  Package and install latest .vsix into VS Code"
	@echo "  make run             Compile and open Extension Development Host"
	@echo "  make clean           Remove build artifacts"
	@echo "  make check-env       Check required tools"
	@echo ""
	@echo "Debug:"
	@echo "  make debug           Launch Extension Development Host with debugger"
	@echo "  make debug-watch     Launch debugger with watch mode (auto-recompile)"
	@echo "  make debug-log       Tail GitPilot output channel logs in real time"
	@echo "  make debug-inspect   Launch with Node inspector on port 9229"
	@echo "  make debug-webview   Launch with webview DevTools port 9222"
	@echo "  make debug-status    Show extension diagnostics (sourcemaps, output, config)"
	@echo "  make debug-clean     Clean build + node_modules, full rebuild"
	@echo "  make debug-check     Validate debug infrastructure is set up correctly"
	@echo ""
	@echo "Logs:"
	@echo "  make logs            Show recent npm debug logs"
	@echo ""

# ──────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────

check-env:
	@command -v node >/dev/null 2>&1 || { echo "Error: node is not installed"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm is not installed"; exit 1; }
	@command -v code >/dev/null 2>&1 || { echo "Error: VS Code CLI 'code' is not installed or not in PATH"; exit 1; }
	@echo "Environment looks good."

# ──────────────────────────────────────────────
# Build
# ──────────────────────────────────────────────

install: check-env
	npm install

compile: check-env
	npm run compile

watch: check-env
	npm run watch

lint: check-env
	npm run lint

package: check-env
	npm run compile
	npm run package
	@echo ""
	@echo "Built VSIX:"
	@ls -1 *.vsix 2>/dev/null || true
	@echo ""

reinstall-vsix: check-env package
	@VSIX="$$(ls -1t *.vsix 2>/dev/null | head -n 1)"; \
	if [ -z "$$VSIX" ]; then \
		echo "Error: no .vsix file found"; \
		exit 1; \
	fi; \
	echo "Installing $$VSIX ..."; \
	code --install-extension "$$VSIX" --force

run: compile
	@echo ""
	@echo "Next step:"
	@echo "  1. Press F5 inside VS Code"
	@echo "  2. Choose 'VS Code Extension Development' if prompted"
	@echo "  3. In the Extension Development Host, open a folder and test GitPilot"
	@echo ""
	@echo "Tip: run 'make watch' in another terminal while developing."
	@echo ""

# ──────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────

# Launch Extension Development Host with F5-equivalent debugging.
# Sets GITPILOT_DEBUG=1 so the extension can enable verbose logging.
debug: compile
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║               GitPilot Debug Mode                          ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║                                                            ║"
	@echo "║  Extension compiled with source maps.                      ║"
	@echo "║                                                            ║"
	@echo "║  To start debugging:                                       ║"
	@echo "║    1. Open this folder in VS Code                          ║"
	@echo "║    2. Press F5 (or Run > Start Debugging)                  ║"
	@echo "║    3. Select 'Run Extension' from the dropdown             ║"
	@echo "║                                                            ║"
	@echo "║  Breakpoints:                                              ║"
	@echo "║    • Set breakpoints in src/*.ts files                     ║"
	@echo "║    • They map through sourcemaps to out/*.js               ║"
	@echo "║    • Hit breakpoints trigger in the original .ts source    ║"
	@echo "║                                                            ║"
	@echo "║  Debug Console:                                            ║"
	@echo "║    • Type expressions to evaluate at breakpoints           ║"
	@echo "║    • Use 'currentState' to inspect extension state         ║"
	@echo "║    • console.log() output appears here                     ║"
	@echo "║                                                            ║"
	@echo "║  Output Channel:                                           ║"
	@echo "║    • In the Dev Host, open Output > GitPilot               ║"
	@echo "║    • Shows all [GitPilotPanel] structured logs             ║"
	@echo "║    • Provider, state sync, and error traces                ║"
	@echo "║                                                            ║"
	@echo "║  Environment: GITPILOT_DEBUG=1                             ║"
	@echo "║                                                            ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@GITPILOT_DEBUG=1 code --extensionDevelopmentPath="$$(pwd)" .

# Same as debug but uses watch mode for live recompilation.
debug-watch: check-env
	@echo "[debug-watch] Starting compile watcher + Extension Development Host..."
	@echo ""
	@echo "  Terminal 1 (this): Watching for TypeScript changes..."
	@echo "  Terminal 2:        Press F5 in VS Code to launch debugger"
	@echo ""
	@echo "  Tip: Changes to .ts files recompile automatically."
	@echo "       Use Ctrl+Shift+F5 in the Dev Host to reload."
	@echo ""
	@GITPILOT_DEBUG=1 npm run watch

# Tail the extension's OutputChannel logs in real time.
# Works by monitoring the VS Code log directory for GitPilot entries.
debug-log:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║               GitPilot Log Monitor                         ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║                                                            ║"
	@echo "║  Monitoring for GitPilot log output...                     ║"
	@echo "║  Press Ctrl+C to stop.                                     ║"
	@echo "║                                                            ║"
	@echo "║  Log sources checked:                                      ║"
	@echo "║    • VS Code Output Channel: Output > GitPilot             ║"
	@echo "║    • Extension Host log directory                          ║"
	@echo "║    • npm debug logs                                        ║"
	@echo "║                                                            ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@if [ -n "$(LOG_DIR)" ] && [ -d "$(LOG_DIR)" ]; then \
		echo "[debug-log] Tailing VS Code logs from: $(LOG_DIR)"; \
		echo "[debug-log] Filtering for: GitPilot, gitpilot, extension-output"; \
		echo ""; \
		find "$(LOG_DIR)" -name "*.log" -newer "$(LOG_DIR)" -o -name "*.log" 2>/dev/null \
			| head -20 \
			| xargs tail -f 2>/dev/null \
			| grep --line-buffered -iE "(gitpilot|GitPilotPanel|extension-output)" \
			|| echo "[debug-log] No active log files found. Launch the extension first."; \
	else \
		echo "[debug-log] VS Code log directory not found at standard paths."; \
		echo ""; \
		echo "  Linux:  ~/.config/Code/logs/"; \
		echo "  macOS:  ~/Library/Application Support/Code/logs/"; \
		echo ""; \
		echo "Manual alternative:"; \
		echo "  1. In VS Code, open Output panel (Ctrl+Shift+U)"; \
		echo "  2. Select 'GitPilot' from the channel dropdown"; \
		echo "  3. All [GitPilotPanel] logs appear there in real time"; \
		echo ""; \
		echo "Or run with verbose logging:"; \
		echo "  code --log=debug --extensionDevelopmentPath=\$$(pwd) ."; \
	fi

# Launch with Node.js inspector protocol for CPU profiling and heap snapshots.
# Connect Chrome DevTools to chrome://inspect or use VS Code's built-in profiler.
debug-inspect: compile
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║               GitPilot Node Inspector                      ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║                                                            ║"
	@echo "║  Launching with --inspect-extensions=9229                  ║"
	@echo "║                                                            ║"
	@echo "║  To connect:                                               ║"
	@echo "║    • Chrome: Navigate to chrome://inspect                  ║"
	@echo "║    • VS Code: Run 'Debug: Attach to Node Process'         ║"
	@echo "║    • CLI:    node --inspect=9229                           ║"
	@echo "║                                                            ║"
	@echo "║  Capabilities:                                             ║"
	@echo "║    • CPU profiling    (Performance tab)                    ║"
	@echo "║    • Heap snapshots   (Memory tab)                         ║"
	@echo "║    • Live breakpoints (Sources tab)                        ║"
	@echo "║    • Console eval     (Console tab)                        ║"
	@echo "║                                                            ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@GITPILOT_DEBUG=1 code \
		--inspect-extensions=9229 \
		--extensionDevelopmentPath="$$(pwd)" .

# Launch with remote debugging port for webview (Chrome DevTools Protocol).
# Allows inspecting the webview HTML/CSS/JS with full DevTools.
debug-webview: compile
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║               GitPilot Webview Debugger                    ║"
	@echo "╠══════════════════════════════════════════════════════════════╣"
	@echo "║                                                            ║"
	@echo "║  Launching VS Code with remote debugging port 9222         ║"
	@echo "║                                                            ║"
	@echo "║  To inspect the webview:                                   ║"
	@echo "║    1. In the Dev Host, run:                                ║"
	@echo "║       Developer: Open Webview Developer Tools              ║"
	@echo "║       (Ctrl+Shift+P > 'webview dev')                      ║"
	@echo "║                                                            ║"
	@echo "║    2. Or attach externally:                                ║"
	@echo "║       Chrome > chrome://inspect > Configure > localhost:9222║"
	@echo "║                                                            ║"
	@echo "║  What you can debug:                                       ║"
	@echo "║    • CSS styles and layout (Elements panel)                ║"
	@echo "║    • JavaScript in the webview (Sources panel)             ║"
	@echo "║    • Network requests (Network panel)                      ║"
	@echo "║    • postMessage traffic (Console: vscode.postMessage)     ║"
	@echo "║    • Animation performance (Performance panel)             ║"
	@echo "║                                                            ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@GITPILOT_DEBUG=1 code \
		--remote-debugging-port=9222 \
		--extensionDevelopmentPath="$$(pwd)" .

# Print diagnostic information about the extension's build and debug setup.
debug-status:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║               GitPilot Debug Status                        ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "── Build Artifacts ──"
	@echo ""
	@if [ -d "$(OUT_DIR)" ]; then \
		JS_COUNT=$$(find $(OUT_DIR) -name '*.js' 2>/dev/null | wc -l); \
		MAP_COUNT=$$(find $(OUT_DIR) -name '*.js.map' 2>/dev/null | wc -l); \
		echo "  out/ directory:     EXISTS"; \
		echo "  .js files:          $$JS_COUNT"; \
		echo "  .js.map files:      $$MAP_COUNT (source maps)"; \
		if [ "$$JS_COUNT" -gt 0 ] && [ "$$MAP_COUNT" -gt 0 ]; then \
			echo "  Source maps:        OK (breakpoints will work)"; \
		elif [ "$$JS_COUNT" -gt 0 ]; then \
			echo "  Source maps:        MISSING (breakpoints will NOT map to .ts)"; \
		else \
			echo "  Source maps:        NOT BUILT (run make compile first)"; \
		fi; \
	else \
		echo "  out/ directory:     NOT FOUND (run make compile)"; \
	fi
	@echo ""
	@echo "── Webview Assets ──"
	@echo ""
	@for f in $(OUT_DIR)/ui/webview/gitpilotWorkspaceTemplate.html $(OUT_DIR)/ui/webview/gitpilotWorkspace.css; do \
		if [ -f "$$f" ]; then \
			SIZE=$$(wc -c < "$$f" | tr -d ' '); \
			echo "  $$(basename $$f):  OK ($$SIZE bytes)"; \
		else \
			echo "  $$(basename $$f):  MISSING"; \
		fi; \
	done
	@echo ""
	@echo "── Debug Configuration ──"
	@echo ""
	@if [ -f ".vscode/launch.json" ]; then \
		CONFIGS=$$(grep '"name"' .vscode/launch.json 2>/dev/null | wc -l); \
		echo "  launch.json:        OK ($$CONFIGS configurations)"; \
	else \
		echo "  launch.json:        MISSING"; \
	fi
	@if [ -f ".vscode/tasks.json" ]; then \
		TASKS=$$(grep '"label"' .vscode/tasks.json 2>/dev/null | wc -l); \
		echo "  tasks.json:         OK ($$TASKS tasks)"; \
	else \
		echo "  tasks.json:         MISSING"; \
	fi
	@echo ""
	@echo "── TypeScript ──"
	@echo ""
	@if [ -f "tsconfig.json" ]; then \
		SM=$$(grep '"sourceMap"' tsconfig.json 2>/dev/null); \
		echo "  tsconfig.json:      OK"; \
		echo "  sourceMap:          $$SM"; \
	else \
		echo "  tsconfig.json:      MISSING"; \
	fi
	@echo ""
	@echo "── Node & Dependencies ──"
	@echo ""
	@echo "  node:               $$(node --version 2>/dev/null || echo 'NOT FOUND')"
	@echo "  npm:                $$(npm --version 2>/dev/null || echo 'NOT FOUND')"
	@echo "  typescript:         $$(npx tsc --version 2>/dev/null || echo 'NOT FOUND')"
	@echo "  vsce:               $$(npx vsce --version 2>/dev/null || echo 'NOT FOUND')"
	@if [ -d "node_modules" ]; then \
		PKG_COUNT=$$(ls node_modules 2>/dev/null | wc -l); \
		echo "  node_modules:       OK ($$PKG_COUNT packages)"; \
	else \
		echo "  node_modules:       MISSING (run make install)"; \
	fi
	@echo ""
	@echo "── VSIX Packages ──"
	@echo ""
	@VSIX_FILES=$$(ls -1t *.vsix 2>/dev/null); \
	if [ -n "$$VSIX_FILES" ]; then \
		echo "$$VSIX_FILES" | while read f; do \
			SIZE=$$(wc -c < "$$f" | tr -d ' '); \
			echo "  $$f  ($$SIZE bytes)"; \
		done; \
	else \
		echo "  No .vsix files found (run make package)"; \
	fi
	@echo ""

# Full clean rebuild: nuke everything and start fresh.
debug-clean: clean
	rm -rf $(NODE_MODULES)
	@echo "[debug-clean] Cleaned build artifacts and node_modules."
	@echo "[debug-clean] Run 'make install && make compile' to rebuild."

# Validate the debug infrastructure is correctly set up.
debug-check:
	@echo ""
	@echo "Checking debug infrastructure..."
	@echo ""
	@PASS=0; FAIL=0; \
	check() { if [ "$$1" = "ok" ]; then PASS=$$((PASS+1)); echo "  [PASS] $$2"; else FAIL=$$((FAIL+1)); echo "  [FAIL] $$2"; fi; }; \
	\
	if [ -f ".vscode/launch.json" ]; then check ok "launch.json exists"; else check fail "launch.json missing"; fi; \
	if [ -f ".vscode/tasks.json" ]; then check ok "tasks.json exists"; else check fail "tasks.json missing"; fi; \
	if [ -f "tsconfig.json" ]; then check ok "tsconfig.json exists"; else check fail "tsconfig.json missing"; fi; \
	if grep -q '"sourceMap": true' tsconfig.json 2>/dev/null; then check ok "Source maps enabled"; else check fail "Source maps not enabled in tsconfig.json"; fi; \
	if [ -d "$(OUT_DIR)" ]; then check ok "out/ directory exists"; else check fail "out/ not found (run make compile)"; fi; \
	if find $(OUT_DIR) -name '*.js.map' 2>/dev/null | grep -q .; then check ok "Source map files generated"; else check fail "No .js.map files in out/"; fi; \
	if [ -f "$(OUT_DIR)/ui/webview/gitpilotWorkspaceTemplate.html" ]; then check ok "Webview HTML in out/"; else check fail "Webview HTML not in out/"; fi; \
	if [ -f "$(OUT_DIR)/ui/webview/gitpilotWorkspace.css" ]; then check ok "Webview CSS in out/"; else check fail "Webview CSS not in out/"; fi; \
	if [ -d "node_modules" ]; then check ok "node_modules installed"; else check fail "node_modules missing"; fi; \
	\
	echo ""; \
	echo "Results: $$PASS passed, $$FAIL failed"; \
	if [ "$$FAIL" -gt 0 ]; then echo ""; echo "Fix the failures above, then re-run 'make debug-check'."; exit 1; fi; \
	echo "Debug infrastructure is ready. Press F5 in VS Code to start."
	@echo ""

# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

clean:
	rm -rf out $(DIST_DIR) *.vsix .eslintcache
	find . -type f \( -name "*.tsbuildinfo" -o -name "npm-debug.log*" \) -delete

logs:
	@echo "Recent npm logs:"
	@ls -1t ~/.npm/_logs/* 2>/dev/null | head -n 10 || true