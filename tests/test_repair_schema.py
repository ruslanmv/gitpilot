"""Schema tests for the GitPilot repair contract."""

from __future__ import annotations

from gitpilot.repair.schema import (
    DEFAULT_FORBIDDEN_PATHS,
    RepairMode,
    RepairRequest,
    RepairResponse,
)

EXAMPLE = {
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
            "description": "No smoke test",
            "recommended_action": "Add tests/test_health.py",
        }
    ],
    "allowed_paths": ["tests/**"],
    "coder": {"provider": "ollabridge", "model": "code-coder"},
    "sandbox": {"provider": "matrixlab", "profile": "default", "required": False},
}


def test_parses_example_request():
    req = RepairRequest.from_json(EXAMPLE)
    assert req.client_id == "agent-matrix"
    assert req.task_id == "fix-health"
    assert req.mode == RepairMode.dry_run
    assert req.allowed_paths == ["tests/**"]
    assert req.issues[0].id == "missing-health-test"
    assert req.coder.model == "code-coder"
    assert req.sandbox.required is False


def test_default_forbidden_paths():
    req = RepairRequest.from_json(EXAMPLE)
    # forbidden_paths not supplied -> defaults applied
    assert req.forbidden_paths == DEFAULT_FORBIDDEN_PATHS
    assert ".env" in req.forbidden_paths
    assert "secrets/**" in req.forbidden_paths
    assert "**/*token*" in req.forbidden_paths
    assert "**/*secret*" in req.forbidden_paths


def test_response_serializes():
    resp = RepairResponse(task_id="t1")
    data = resp.to_json()
    assert data["task_id"] == "t1"
    assert data["status"] == "ok"
    assert data["risk_level"] == "low"
    assert "patch_preview" in data
