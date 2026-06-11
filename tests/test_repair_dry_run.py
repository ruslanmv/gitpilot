"""End-to-end dry-run test (offline demo mode, no network, no PR)."""

from __future__ import annotations

import json
from pathlib import Path

from gitpilot.repair.cli import run_cli
from gitpilot.repair.schema import RepairMode, RepairRequest
from gitpilot.repair.service import run_repair

PLAN = {
    "client_id": "agent-matrix",
    "workspace_id": "ws-1",
    "task_id": "fix-health",
    "repo_url": "https://github.com/acme/app",
    "branch": "main",
    "mode": "dry_run",
    "issues": [
        {
            "id": "missing-health-test",
            "severity": "medium",
            "description": "No smoke test for health",
            "recommended_action": "Add tests/test_health.py",
        }
    ],
    "allowed_paths": ["tests/**"],
    "sandbox": {"provider": "matrixlab", "profile": "default", "required": False},
}


def test_dry_run_produces_preview_no_pr():
    req = RepairRequest.from_json(PLAN)
    resp = run_repair(req, demo_mode=True)

    assert resp.status == "ok"
    assert resp.mode == RepairMode.dry_run
    assert resp.patch_preview  # non-empty unified diff
    assert "tests/test_health.py" in resp.patch_preview
    assert any(cf.path == "tests/test_health.py" for cf in resp.changed_files)
    assert resp.review
    assert resp.risk_level.value == "low"
    # No PR opened in dry-run/first wave.
    assert resp.pr_url is None
    # Sandbox not required + unreachable -> skipped, not blocking.
    assert resp.status != "blocked"


def test_dry_run_blocks_outside_allowed():
    plan = dict(PLAN)
    plan["allowed_paths"] = ["src/**"]  # stub diff writes tests/ -> outside
    req = RepairRequest.from_json(plan)
    resp = run_repair(req, demo_mode=True)
    assert resp.status == "blocked"
    assert any("outside allowed_paths" in w for w in resp.warnings)


def test_dry_run_empty_allowed_blocks():
    plan = dict(PLAN)
    plan["allowed_paths"] = []
    req = RepairRequest.from_json(plan)
    resp = run_repair(req, demo_mode=True)
    assert resp.status == "blocked"


def test_cli_writes_response_json(tmp_path: Path):
    plan_path = tmp_path / "repair-plan.json"
    plan_path.write_text(json.dumps(PLAN), encoding="utf-8")
    out_path = tmp_path / "repair-response.json"

    code = run_cli(
        ["--plan", str(plan_path), "--out", str(out_path), "--dry-run", "--demo"]
    )
    assert code == 0
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["task_id"] == "fix-health"
    assert data["mode"] == "dry_run"
    assert data["patch_preview"]
    assert data["pr_url"] is None
