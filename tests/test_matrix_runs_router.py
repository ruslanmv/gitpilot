"""Tests for the Matrix runs facade (Batch 2).

Covers the acceptance criteria:
* a signed bundle URL yields a queued run ({run_id, status: "queued", url});
* the Matrix control files are rejected even if a caller "allows" them;
* the facade is gated by the A2A shared secret.

Runs fully offline — the facade only validates + maps + registers the run; it
does not execute the repair pipeline in this batch.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.matrix_runs_router import (
    MATRIX_CONTROL_FILES,
    MatrixRunRequest,
    build_matrix_runs_alias_router,
    build_matrix_runs_router,
    merge_forbidden,
    to_repair_request,
)
from gitpilot.repair.policy import validate_changed_files
from gitpilot.repair.schema import DEFAULT_FORBIDDEN_PATHS

PAYLOAD = {
    "bundle_url": "https://build.matrixhub.io/api/v1/bundles/abc/download?expires=1&token=x",
    "project_name": "Starter controlled blueprint",
    "task_id": "TASK-001",
    "prompt": "Implement the health endpoint",
    "allowed_files": ["src/**", "tests/**"],
    "forbidden_files": ["infra/**"],
    "validation_commands": ["pytest -q"],
    "mode": "ask",
}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_matrix_runs_router())
    app.include_router(build_matrix_runs_alias_router())
    return app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off for the happy-path tests (local/dev posture).
    monkeypatch.setenv("GITPILOT_A2A_REQUIRE_AUTH", "false")
    return TestClient(_app())


def test_create_run_returns_queued(client: TestClient) -> None:
    resp = client.post("/api/v1/gitpilot/runs", json=PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["run_id"].startswith("gp-run-")
    assert body["url"].endswith(f"/api/v1/gitpilot/runs/{body['run_id']}")


def test_local_bridge_alias_runs(client: TestClient) -> None:
    # The /api/matrix/* alias (used by "Send to local GitPilot") hits the same
    # facade and returns a queued run.
    resp = client.post("/api/matrix/runs", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    health = client.get("/api/matrix/health")
    assert health.status_code == 200
    assert health.json()["service"] == "gitpilot-matrix"


def test_run_status_completes_with_diff_logs_tests(client: TestClient) -> None:
    # allowed_files include tests/** so the demo patch (tests/test_health.py) is
    # inside the contract and the run completes.
    payload = dict(PAYLOAD, allowed_files=["tests/**"])
    created = client.post("/api/v1/gitpilot/runs", json=payload)
    run_id = created.json()["run_id"]

    # TestClient runs the background task before returning, so the run is terminal.
    status = client.get(f"/api/v1/gitpilot/runs/{run_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["run_id"] == run_id
    assert body["status"] == "completed"
    # Result reflects diff / logs / tests.
    assert body["diff_url"] and body["diff_url"].endswith(f"/runs/{run_id}/diff")
    assert body["logs_url"]
    assert body["test_status"] in {"passed", "failed", "skipped"}
    assert any("test_health" in f for f in body["changed_files"])

    diff = client.get(f"/api/v1/gitpilot/runs/{run_id}/diff")
    assert diff.status_code == 200 and "test_health.py" in diff.text
    logs = client.get(f"/api/v1/gitpilot/runs/{run_id}/logs")
    assert logs.status_code == 200


def test_run_status_never_auto_promotes_to_approved(client: TestClient) -> None:
    created = client.post("/api/v1/gitpilot/runs", json=dict(PAYLOAD, allowed_files=["tests/**"]))
    run_id = created.json()["run_id"]
    body = client.get(f"/api/v1/gitpilot/runs/{run_id}").json()
    # GitPilot reports implementation status only — never a Matrix verdict.
    assert body["status"] != "approved"
    assert body["status"] in {"queued", "running", "completed", "blocked", "error", "needs_approval"}


def test_run_status_unknown_run_404(client: TestClient) -> None:
    assert client.get("/api/v1/gitpilot/runs/gp-run-nope").status_code == 404


def test_repair_creates_child_run_inside_contract(client: TestClient) -> None:
    created = client.post("/api/v1/gitpilot/runs", json=dict(PAYLOAD, allowed_files=["tests/**"]))
    run_id = created.json()["run_id"]

    repair = client.post(
        f"/api/v1/gitpilot/runs/{run_id}/repair",
        json={
            "validation_findings": ["missing health test"],
            "repair_prompt": "add tests/test_health.py",
            "allowed_files": ["tests/**"],
            # Even if a caller "allows" a control file here, repair must still deny it.
            "forbidden_files": [],
        },
    )
    assert repair.status_code == 200
    child_id = repair.json()["run_id"]
    assert child_id != run_id
    assert repair.json()["status"] == "queued"

    # Child run completes inside the contract.
    status = client.get(f"/api/v1/gitpilot/runs/{child_id}").json()
    assert status["status"] == "completed"
    assert status["status"] != "approved"


def test_repair_unknown_run_404(client: TestClient) -> None:
    resp = client.post("/api/v1/gitpilot/runs/gp-run-nope/repair", json={"repair_prompt": "x"})
    assert resp.status_code == 404


def test_pr_endpoint_returns_draft_reference(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("GITPILOT_DRAFT_PR_ENABLED", "true")
    created = client.post("/api/v1/gitpilot/runs", json=dict(PAYLOAD, allowed_files=["tests/**"]))
    run_id = created.json()["run_id"]
    resp = client.post(
        f"/api/v1/gitpilot/runs/{run_id}/pr",
        json={"repo_url": "https://github.com/acme/app", "title": "Add hello world"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["status"] in {"draft", "created"}
    assert body["pr_url"] and "github.com/acme/app/pull/" in body["pr_url"]


def test_pr_endpoint_unknown_run_404(client: TestClient) -> None:
    resp = client.post("/api/v1/gitpilot/runs/gp-run-nope/pr", json={"repo_url": "x"})
    assert resp.status_code == 404


def test_missing_required_fields_422(client: TestClient) -> None:
    # No bundle_url / task_id -> pydantic validation error.
    resp = client.post("/api/v1/gitpilot/runs", json={"prompt": "x"})
    assert resp.status_code == 422


def test_health_reports_service(client: TestClient) -> None:
    resp = client.get("/api/v1/gitpilot/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "gitpilot-matrix"
    assert body["auth_required"] is False


def test_matrix_control_files_always_forbidden_even_if_allowed() -> None:
    run = MatrixRunRequest(
        bundle_url="https://build.matrixhub.io/b/x/download",
        task_id="TASK-001",
        prompt="do it",
        # Caller tries to whitelist the control files — must not help them.
        allowed_files=["**", "MATRIX_STANDARDS.lock", "MATRIX_BLUEPRINT.yaml"],
        forbidden_files=[],
    )
    req = to_repair_request(run)
    for control in ("MATRIX_STANDARDS.lock", "MATRIX_BLUEPRINT.yaml"):
        res = validate_changed_files([control], req)
        assert res.allowed is False, f"{control} must be rejected"
    # Nested form is rejected too (policy matches on basename).
    assert validate_changed_files(["config/MATRIX_STANDARDS.lock"], req).allowed is False


def test_merge_forbidden_includes_control_and_defaults() -> None:
    merged = merge_forbidden(["infra/**"])
    assert "infra/**" in merged
    for control in MATRIX_CONTROL_FILES:
        assert control in merged
    for default in DEFAULT_FORBIDDEN_PATHS:
        assert default in merged
    # De-duplicated.
    assert len(merged) == len(set(merged))


def test_mode_maps_conservatively() -> None:
    def mode_of(mode: str) -> str:
        return to_repair_request(
            MatrixRunRequest(bundle_url="u", task_id="t", mode=mode)
        ).mode.value

    assert mode_of("ask") == "dry_run"
    assert mode_of("plan") == "dry_run"
    assert mode_of("") == "dry_run"
    assert mode_of("apply") == "apply"
    assert mode_of("draft_pr") == "draft_pr"


def test_allowed_files_map_to_allowed_paths() -> None:
    req = to_repair_request(MatrixRunRequest(**PAYLOAD))
    assert req.allowed_paths == ["src/**", "tests/**"]
    assert req.client_id == "matrix-builder"
    assert req.task_id == "TASK-001"
    # An allowed file still passes when it isn't a control/secret file.
    assert validate_changed_files(["src/app.py"], req).allowed is True


def test_auth_required_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_A2A_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GITPILOT_A2A_SHARED_SECRET", "topsecret")
    client = TestClient(_app())

    # No secret header -> 401.
    resp = client.post("/api/v1/gitpilot/runs", json=PAYLOAD)
    assert resp.status_code == 401

    # Correct secret -> 200 queued.
    ok = client.post("/api/v1/gitpilot/runs", json=PAYLOAD, headers={"X-A2A-Secret": "topsecret"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "queued"


def test_auth_required_but_secret_unset_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Auth required + no shared secret configured -> the facade refuses (never
    # silently opens). 500, not 200.
    monkeypatch.setenv("GITPILOT_A2A_REQUIRE_AUTH", "true")
    monkeypatch.delenv("GITPILOT_A2A_SHARED_SECRET", raising=False)
    client = TestClient(_app())
    resp = client.post("/api/v1/gitpilot/runs", json=PAYLOAD)
    assert resp.status_code != 200
