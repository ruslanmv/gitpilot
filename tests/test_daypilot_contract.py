"""Provider verification: GitPilot accepts DayPilot's native coding run.

DayPilot's coding executor posts to ``/api/v1/gitpilot/runs`` in its own dialect
(``{task, repo, mode, branch, baseBranch}``) — see DayPilot's
``coding/gitpilot_adapter.py``. This file replays that exact payload against the
real router, so the two products cannot drift apart silently.

DayPilot's repo carries the mirror of this contract in
``tests/test_gitpilot_contract.py``; DAYPILOT_RUN_REQUEST below is kept
identical in both repos.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.matrix_runs_router import (
    MatrixRunRequest,
    build_matrix_runs_router,
    to_repair_request,
)

# ── The contract (mirrored in DayPilot) ─────────────────────────────────────
DAYPILOT_RUN_REQUEST = {
    "task": "Add a health endpoint",
    "repo": "https://github.com/acme/widget",
    "mode": "ask",
    "branch": "feature/health",
    "baseBranch": "main",
}

# The Matrix Builder dialect must keep working unchanged.
MATRIX_RUN_REQUEST = {
    "bundle_url": "https://build.matrixhub.io/api/v1/bundles/abc/download?token=x",
    "task_id": "TASK-001",
    "prompt": "Implement the health endpoint",
    "allowed_files": ["src/**"],
    "mode": "ask",
}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_matrix_runs_router())
    return app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GITPILOT_A2A_REQUIRE_AUTH", "false")
    return TestClient(_app())


# ── The end-to-end proof ────────────────────────────────────────────────────

def test_daypilot_run_is_accepted_and_reaches_a_terminal_state(client: TestClient) -> None:
    """The payload DayPilot actually sends is accepted, queued, and executed."""
    created = client.post("/api/v1/gitpilot/runs", json=DAYPILOT_RUN_REQUEST)
    assert created.status_code == 200, created.text
    body = created.json()
    run_id = body["run_id"]
    assert body["status"] == "queued"

    # TestClient drains background tasks, so the run is terminal by now.
    status = client.get(f"/api/v1/gitpilot/runs/{run_id}")
    assert status.status_code == 200
    result = status.json()
    assert result["status"] != "queued"
    # Fields DayPilot's normalizer reads back.
    assert result["run_id"] == run_id
    assert result["repo"] == DAYPILOT_RUN_REQUEST["repo"]
    assert result["branch"] == DAYPILOT_RUN_REQUEST["branch"]
    assert "test_status" in result and "changed_files" in result


def test_matrix_dialect_still_works(client: TestClient) -> None:
    """No regression for the original caller."""
    created = client.post("/api/v1/gitpilot/runs", json=MATRIX_RUN_REQUEST)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "queued"


# ── Fail closed ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,missing", [
    ({"task": "do something"}, "source"),                      # no repo/bundle
    ({"repo": "https://github.com/acme/widget"}, "instruction"),  # no task/prompt
    ({}, "both"),
])
def test_incomplete_run_is_rejected(client: TestClient, payload: dict, missing: str) -> None:
    """An under-specified run is refused before anything is queued, so the
    pipeline is never asked to guess a repo or a task."""
    resp = client.post("/api/v1/gitpilot/runs", json=payload)
    assert resp.status_code == 422, f"{missing}: {resp.text}"


# ── Mapping + guardrails apply to both dialects ─────────────────────────────

def test_daypilot_dialect_maps_onto_the_governed_pipeline() -> None:
    run = MatrixRunRequest.model_validate(DAYPILOT_RUN_REQUEST)
    repair = to_repair_request(run)

    assert repair.repo_url == DAYPILOT_RUN_REQUEST["repo"]
    assert repair.branch == DAYPILOT_RUN_REQUEST["baseBranch"]
    assert repair.client_id == "daypilot"           # attributable
    assert repair.mode.value == "dry_run"           # "ask" stays approval-safe
    assert repair.issues[0].description == DAYPILOT_RUN_REQUEST["task"]
    # The Matrix control files are forbidden no matter which dialect called.
    assert "MATRIX_STANDARDS.lock" in repair.forbidden_paths
    assert "MATRIX_BLUEPRINT.yaml" in repair.forbidden_paths


def test_daypilot_cannot_unlock_matrix_control_files() -> None:
    """Even if a DayPilot caller 'allows' them, control files stay forbidden."""
    run = MatrixRunRequest.model_validate(
        {**DAYPILOT_RUN_REQUEST, "allowed_files": ["**/MATRIX_STANDARDS.lock"]}
    )
    repair = to_repair_request(run)
    assert "MATRIX_STANDARDS.lock" in repair.forbidden_paths
