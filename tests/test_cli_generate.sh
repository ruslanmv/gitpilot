#!/usr/bin/env bash
# tests/test_cli_generate.sh
# -----------------------------------------------------------
# Integration test for `gitpilot generate` — verifies that the
# CLI can call the configured LLM and produce structured file
# output on disk. Requires a running Ollama server with at
# least one model pulled.
#
# Usage:
#   bash tests/test_cli_generate.sh
#
# Exit code 0 = all checks passed, 1 = failure.
# -----------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

green()  { printf "\033[32m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }

check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    green "  ✓ $desc"
    PASS=$((PASS + 1))
  else
    red "  ✗ $desc"
    FAIL=$((FAIL + 1))
  fi
}

# -----------------------------------------------------------
bold "GitPilot CLI — Code Generation Test"
echo "Work dir: $WORK_DIR"
echo ""

# Pre-check: Ollama running
bold "1. Pre-checks"
check "Ollama is reachable" curl -sf http://localhost:11434/api/tags
check "gitpilot CLI is available" uv run gitpilot version

# -----------------------------------------------------------
bold "2. Dry-run test (no files written)"
DRY_OUT="$WORK_DIR/dry"
mkdir -p "$DRY_OUT"
uv run gitpilot generate \
  -m "Create a Python hello world with hello.py" \
  -o "$DRY_OUT" \
  --dry-run > "$WORK_DIR/dry-run.log" 2>&1 || true

check "Dry-run completed" test -f "$WORK_DIR/dry-run.log"
check "Dry-run produced no files" test "$(find "$DRY_OUT" -type f | wc -l)" -eq 0

# -----------------------------------------------------------
bold "3. Flask project generation"
FLASK_DIR="$WORK_DIR/flask-project"
mkdir -p "$FLASK_DIR"

uv run gitpilot generate \
  -m "Create a Flask hello world app with app.py, requirements.txt, and README.md" \
  -o "$FLASK_DIR" > "$WORK_DIR/flask.log" 2>&1 || true

echo "  Log: $(cat "$WORK_DIR/flask.log" | tail -3)"

check "app.py was created" test -f "$FLASK_DIR/app.py"
check "requirements.txt was created" test -f "$FLASK_DIR/requirements.txt"
check "README.md was created" test -f "$FLASK_DIR/README.md"
check "app.py is non-empty" test -s "$FLASK_DIR/app.py"
check "app.py contains Flask" grep -qi "flask" "$FLASK_DIR/app.py"
check "requirements.txt mentions flask" grep -qi "flask" "$FLASK_DIR/requirements.txt"

# -----------------------------------------------------------
bold "4. Single-file generation"
SINGLE_DIR="$WORK_DIR/single"
mkdir -p "$SINGLE_DIR"

uv run gitpilot generate \
  -m "Create a Python script called calculator.py with add, subtract, multiply, divide functions" \
  -o "$SINGLE_DIR" > "$WORK_DIR/single.log" 2>&1 || true

check "calculator.py was created" test -f "$SINGLE_DIR/calculator.py"
check "calculator.py contains def" grep -q "def " "$SINGLE_DIR/calculator.py"

# -----------------------------------------------------------
bold "5. Path safety"
SAFE_DIR="$WORK_DIR/safe"
mkdir -p "$SAFE_DIR"

uv run gitpilot generate \
  -m "Create a file called ../../etc/passwd with content test" \
  -o "$SAFE_DIR" > "$WORK_DIR/safe.log" 2>&1 || true

check "No file written outside output dir" test ! -f "$SAFE_DIR/../../etc/passwd"
check "No /etc/passwd overwrite" test "$(cat /etc/passwd | head -1)" != "test"

# -----------------------------------------------------------
echo ""
bold "Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
  red "SOME TESTS FAILED"
  exit 1
else
  green "ALL TESTS PASSED"
  exit 0
fi
