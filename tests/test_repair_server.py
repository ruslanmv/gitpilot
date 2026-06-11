"""Tests for the additive GitPilot HTTP repair API.

Runs fully offline in demo mode (``GITPILOT_DEMO_MODE=true``): no network,
no real PR, deterministic stub patch.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Force offline/deterministic demo mode for the whole module BEFORE importing
# the app so the server reports demo_mode=true and emits a stub patch.
os.environ["GITPILOT_DEMO_MODE"] = "true"
os.environ.pop("GITPILOT_DRAFT_PR_ENABLED", None)

from gitpilot.api.repair_server import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "gitpilot"
    assert isinstance(body["version"], str) and body["version"]
    assert body["demo_mode"] is True


def test_repair_minimal_plan(client: TestClient) -> None:
    plan = {
        "client_id": "agent-matrix",
        "workspace_id": "ws-1",
        "task_id": "fix-health",
        "repo_url": "https://github.com/acme/app",
        "issues": [
            {
                "id": "missing-health-test",
                "severity": "medium",
                "description": "No smoke test for health",
                "recommended_action": "Add tests/test_health.py",
            }
        ],
        "allowed_paths": ["tests/test_health.py"],
        "sandbox": {"required": False},
        # extra/unknown key must be tolerated gracefully
        "x_unknown_key": {"nested": True},
    }
    resp = client.post("/repair", json=plan)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "blocked", "needs_approval"}
    assert body["patch_preview"], "patch_preview should be non-empty"
    assert body["changed_files"], "changed_files should be present"
    # mode defaulted to dry-run, never a real PR.
    assert body["mode"] == "dry_run"
    assert body.get("pr_url") in (None, "")


def test_repair_empty_allowed_paths_blocks(client: TestClient) -> None:
    plan = {
        "client_id": "agent-matrix",
        "workspace_id": "ws-1",
        "task_id": "fix-health",
        "repo_url": "https://github.com/acme/app",
        "issues": [
            {
                "id": "missing-health-test",
                "severity": "medium",
                "description": "No smoke test",
                "recommended_action": "Add tests/test_health.py",
            }
        ],
        "allowed_paths": [],
        "sandbox": {"required": False},
    }
    resp = client.post("/repair", json=plan)
    # fail-closed: 200 with status "blocked", NOT a 500.
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


def test_repair_draft_pr_downgraded_to_dry_run(client: TestClient) -> None:
    plan = {
        "client_id": "agent-matrix",
        "workspace_id": "ws-1",
        "task_id": "fix-health",
        "repo_url": "https://github.com/acme/app",
        "mode": "draft_pr",
        "issues": [
            {"id": "missing-health-test", "severity": "medium",
             "description": "x", "recommended_action": "add test"}
        ],
        "allowed_paths": ["tests/test_health.py"],
        "sandbox": {"required": False},
    }
    resp = client.post("/repair", json=plan)
    assert resp.status_code == 200
    body = resp.json()
    # draft_pr downgraded to dry_run (GITPILOT_DRAFT_PR_ENABLED not set).
    assert body["mode"] == "dry_run"
    assert body.get("pr_url") in (None, "")


def test_repair_invalid_plan_returns_422(client: TestClient) -> None:
    # Missing required fields (client_id, workspace_id, task_id, repo_url).
    resp = client.post("/repair", json={"issues": []})
    assert resp.status_code == 422
    assert "detail" in resp.json()
