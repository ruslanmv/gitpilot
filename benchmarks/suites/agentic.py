"""GitPilot agent tasks: the real ``AgentLoop`` driving one model (optional, slow).

This is the suite closest to "could this model replace a frontier model inside
GitPilot". It reuses ``bench/`` — the 13 fixture tasks the v4 flip gate is
built on — through its frontier tier, which can be pointed at any
OpenAI-compatible gateway. Each task builds a throwaway project, lets the loop
read, edit and run tests in it, then checks what is on disk.

Expect minutes per task on local hardware; enable with ``--suites ...,agentic``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_agentic(model: str, *, gateway_root: str, timeout_s: float = 600.0,
                provider: str = "ollabridge") -> List[Dict[str, Any]]:
    """Run ``python -m bench`` for one model; one row per task, in the runner's shape."""
    with tempfile.TemporaryDirectory(prefix="gpbench-agentic-") as scratch:
        out = Path(scratch) / "bench.json"
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            # A fresh config dir, so the user's saved provider and flags do not leak in.
            "GITPILOT_CONFIG_DIR": str(Path(scratch) / "config"),
            "GITPILOT_IDEMPOTENCY_DB": str(Path(scratch) / "idempotency.sqlite3"),
            "GITPILOT_PROVIDER": provider,
            "GITPILOT_BENCH_FRONTIER_FAMILY": provider,
            "GITPILOT_BENCH_FRONTIER_MODEL": model,
            "OLLABRIDGE_BASE_URL": gateway_root,
            "OLLAMA_BASE_URL": gateway_root if provider == "ollama" else os.environ.get("OLLAMA_BASE_URL", ""),
            "GITPILOT_OLLAMA_MODEL": model,
            "GITPILOT_OLLABRIDGE_MODEL": model,
        }
        command = [sys.executable, "-m", "bench", "--tiers", "frontier", "--engines", "loop",
                   "--timeout", str(int(timeout_s)), "--out", str(out)]
        try:
            subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True,  # noqa: S603
                           timeout=timeout_s * 14 + 120, check=False)
        except subprocess.TimeoutExpired:
            return [_infra_row("agentic/all", "the agent suite exceeded its overall time budget")]
        if not out.exists():
            return [_infra_row("agentic/all", "the bench harness produced no report")]
        report = json.loads(out.read_text(encoding="utf-8"))

    rows: List[Dict[str, Any]] = []
    for cell in report.get("cells", []):
        if cell.get("engine") != "loop" or cell.get("unavailable") or cell.get("skipped"):
            continue
        status = cell.get("status") or ""
        infra = status in ("timeout", "error") and "provider" in str(cell.get("error") or "").lower()
        rows.append({
            "task": f"agentic/{cell.get('task')}",
            "suite": "agentic",
            "category": cell.get("category") or "agent",
            "passed": None if infra else bool(cell.get("ok")),
            "infra_error": infra,
            "detail": cell.get("detail") or cell.get("error") or status,
            "latency_s": float(cell.get("wall_time_s") or 0.0),
            "output_tokens": int(cell.get("completion_tokens") or 0) or None,
            "tokens_estimated": False,
            "attempts": 1,
            "error_kind": status if infra else None,
            "iterations": cell.get("iterations"),
            "tool_calls": cell.get("tool_calls"),
        })
    return rows or [_infra_row("agentic/all", "the bench harness reported no loop cells")]


def _infra_row(task: str, detail: str) -> Dict[str, Any]:
    return {"task": task, "suite": "agentic", "category": "agent", "passed": None,
            "infra_error": True, "detail": detail, "latency_s": 0.0, "output_tokens": None,
            "tokens_estimated": False, "attempts": 1, "error_kind": "harness"}
