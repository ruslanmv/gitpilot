SHELL := /bin/bash

EXT_NAME := gitpilot-vscode
NODE_MODULES := node_modules
DIST_DIR := dist
VSIX_FILE := $(shell ls -1 *.vsix 2>/dev/null | tail -n 1)

.DEFAULT_GOAL := help

.PHONY: help install clean compile watch lint test package reinstall-vsix run logs check-env

help:
	@echo ""
	@echo "GitPilot VS Code Extension"
	@echo ""
	@echo "Targets:"
	@echo "  make install         Install npm dependencies"
	@echo "  make compile         Compile the extension"
	@echo "  make watch           Compile in watch mode"
	@echo "  make lint            Run eslint"
	@echo "  make package         Build a .vsix package"
	@echo "  make reinstall-vsix  Package and install latest .vsix into VS Code"
	@echo "  make run             Compile and open Extension Development Host instructions"
	@echo "  make clean           Remove build artifacts"
	@echo "  make check-env       Check required tools"
	@echo "  make logs            Show recent npm debug logs"
	@echo ""

check-env:
	@command -v node >/dev/null 2>&1 || { echo "Error: node is not installed"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm is not installed"; exit 1; }
	@command -v code >/dev/null 2>&1 || { echo "Error: VS Code CLI 'code' is not installed or not in PATH"; exit 1; }
	@echo "Environment looks good."

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

clean:
	rm -rf out $(DIST_DIR) *.vsix .eslintcache
	find . -type f \( -name "*.tsbuildinfo" -o -name "npm-debug.log*" \) -delete

logs:
	@echo "Recent npm logs:"
	@ls -1t ~/.npm/_logs/* 2>/dev/null | head -n 10 || true