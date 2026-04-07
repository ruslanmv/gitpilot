#!/usr/bin/env bash
# tests/test_cli_topologies.sh
# -----------------------------------------------------------
# Smoke/integration checks for all registered GitPilot topologies.
# Verifies topology registry integrity and runs a small hello-world
# multi-file benchmark pass across every topology.
#
# Usage:
#   bash tests/test_cli_topologies.sh
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
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
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

PYTHON_BIN="python"

bold "GitPilot CLI Topology Smoke Test"
echo "Work dir: $WORK_DIR"
echo ""

# -----------------------------------------------------------
bold "1. Discover all registered topologies"
TOPOLOGY_FILE="$WORK_DIR/topologies.txt"
cd "$PROJECT_ROOT"
$PYTHON_BIN - <<'PY' > "$TOPOLOGY_FILE"
from gitpilot.topology_registry import list_topologies
for t in list_topologies():
    if isinstance(t, dict):
        print(t["id"])
    else:
        print(t.id)
PY

check "Topology list file generated" test -s "$TOPOLOGY_FILE"
TOPOLOGY_COUNT="$(wc -l < "$TOPOLOGY_FILE" | tr -d ' ')"
check "At least 1 topology found" test "$TOPOLOGY_COUNT" -ge 1

echo "  Topologies ($TOPOLOGY_COUNT): $(tr '\n' ' ' < "$TOPOLOGY_FILE")"

# -----------------------------------------------------------
bold "2. Validate graph and metadata for every topology"
while IFS= read -r TOPO; do
  [ -z "$TOPO" ] && continue

  check "[$TOPO] topology exists" $PYTHON_BIN - <<PY
from gitpilot.topology_registry import get_topology
assert get_topology("$TOPO") is not None
PY

  check "[$TOPO] has nodes and edges" $PYTHON_BIN - <<PY
from gitpilot.topology_registry import get_topology_graph
g = get_topology_graph("$TOPO")
assert g["nodes"] and g["edges"]
PY

done < "$TOPOLOGY_FILE"

# -----------------------------------------------------------
bold "3. Run hello-world multi-file benchmark across ALL topologies"
BENCH_OUT="$WORK_DIR/bench_topologies"
mkdir -p "$BENCH_OUT"

if $PYTHON_BIN tests/benchmark_agents.py --simulate --topologies all --max-files 5 --repeats 1 --output-dir "$BENCH_OUT" > "$WORK_DIR/benchmark.log" 2>&1; then
  green "  ✓ Topology benchmark completed"
  PASS=$((PASS + 1))
else
  red "  ✗ Topology benchmark failed"
  FAIL=$((FAIL + 1))
  tail -40 "$WORK_DIR/benchmark.log" || true
fi

check "benchmark_raw.csv created" test -f "$BENCH_OUT/benchmark_raw.csv"
check "benchmark_report.json created" test -f "$BENCH_OUT/benchmark_report.json"
check "benchmark_dashboard.html created" test -f "$BENCH_OUT/benchmark_dashboard.html"

# Ensure each topology produced at least one result row.
while IFS= read -r TOPO; do
  [ -z "$TOPO" ] && continue
  check "[$TOPO] appears in benchmark JSON" grep -q "\"topology\": \"$TOPO\"" "$BENCH_OUT/benchmark_report.json"
done < "$TOPOLOGY_FILE"

# -----------------------------------------------------------
echo ""
bold "Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
  red "SOME TOPOLOGY TESTS FAILED"
  exit 1
else
  green "ALL TOPOLOGY TESTS PASSED"
  exit 0
fi
