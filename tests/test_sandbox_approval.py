"""The sandbox approval token — Batch V4-D6 (F7).

The defect: ``POST /api/sandbox/run`` took ``{language, code}`` and ran it. The
ExecutionPlan card, its safety warnings and its Approve button were all
client-side, so anything that could reach the endpoint executed arbitrary code
with the server's privileges. These tests are about the server no longer taking
the client's word for it.
"""
from __future__ import annotations

import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot.sandbox_tokens import (
    SandboxApprovalStore,
    content_digest,
    reset_store,
    session_runs_without_asking,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_store()
    yield
    reset_store()


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    from gitpilot import settings as settings_module

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_module, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(settings_module, "CONFIG_FILE", cfg_dir / "settings.json")
    settings_module.reload_settings()

    from gitpilot.api import app

    with TestClient(app) as test_client:
        yield test_client
    settings_module.reload_settings()


CODE = "print('hello')"


def _plan(client, code=CODE, language="python", session_id=None):
    response = client.post(
        "/api/sandbox/plan",
        json={
            "language": language, "code": code, "source": "code_block",
            "session_id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["plan"]


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


class TestRunRequiresApproval:
    def test_a_run_without_a_token_is_refused(self, client):
        response = client.post(
            "/api/sandbox/run", json={"language": "python", "code": CODE},
        )
        assert response.status_code == 403
        assert "not been approved" in response.json()["detail"]

    def test_an_approved_plan_runs(self, client):
        plan = _plan(client)
        approval = client.post("/api/sandbox/approve", json={"plan_id": plan["plan_id"]})
        assert approval.status_code == 200

        response = client.post("/api/sandbox/run", json={
            "language": "python", "code": CODE,
            "approval_token": approval.json()["approval_token"],
        })

        assert response.status_code == 200, response.text
        assert "hello" in response.json()["stdout"]

    def test_a_made_up_token_is_refused(self, client):
        response = client.post("/api/sandbox/run", json={
            "language": "python", "code": CODE, "approval_token": "not-a-real-token",
        })
        assert response.status_code == 403
        assert "unknown" in response.json()["detail"]

    def test_approving_a_plan_that_does_not_exist_is_a_404(self, client):
        response = client.post("/api/sandbox/approve", json={"plan_id": "made-up"})
        assert response.status_code == 404

    def test_a_token_cannot_be_used_for_different_code(self, client):
        """The check that makes the binding mean something: approve
        `print('hello')`, then try to run something else under it."""
        plan = _plan(client)
        approval = client.post("/api/sandbox/approve", json={"plan_id": plan["plan_id"]})

        response = client.post("/api/sandbox/run", json={
            "language": "python", "code": "import shutil; shutil.rmtree('/tmp/x')",
            "approval_token": approval.json()["approval_token"],
        })

        assert response.status_code == 403
        assert "does not match the approved plan" in response.json()["detail"]

    def test_a_token_cannot_be_replayed(self, client):
        plan = _plan(client)
        token = client.post(
            "/api/sandbox/approve", json={"plan_id": plan["plan_id"]},
        ).json()["approval_token"]
        body = {"language": "python", "code": CODE, "approval_token": token}

        assert client.post("/api/sandbox/run", json=body).status_code == 200
        second = client.post("/api/sandbox/run", json=body)

        assert second.status_code == 403, (
            "'approve once, run repeatedly' is not what the user agreed to"
        )

    def test_the_env_opt_out_still_works(self, client, monkeypatch):
        """For a single-user local install driving the endpoint from a script.
        Opt-out rather than opt-in, because a shipped default of "trust the
        client" is the defect."""
        monkeypatch.setenv("GITPILOT_SANDBOX_REQUIRE_APPROVAL", "0")
        response = client.post(
            "/api/sandbox/run", json={"language": "python", "code": CODE},
        )
        assert response.status_code == 200


class TestNoBodySuppliedMode:
    def test_a_request_cannot_claim_auto_mode(self, client):
        """§8.1: a request body is not an authorisation channel. The mode is read
        from the persisted session record or not at all."""
        response = client.post("/api/sandbox/run", json={
            "language": "python", "code": CODE, "permission_mode": "auto",
        })
        assert response.status_code == 403

    def test_the_request_model_has_no_mode_field(self):
        from gitpilot.sandbox_api import SandboxRunRequest

        assert "permission_mode" not in SandboxRunRequest.model_fields

    def test_an_auto_session_runs_without_a_card(self, tmp_path, monkeypatch):
        from gitpilot.session import SessionManager

        manager = SessionManager(root=tmp_path / "sessions")
        session = manager.create(name="trusting")
        session.permission_mode = "auto"
        manager.save(session)

        monkeypatch.setattr(
            "gitpilot.session.SessionManager", lambda *a, **k: manager,
        )
        assert session_runs_without_asking(session.id) is True

    def test_a_normal_session_still_needs_a_token(self, tmp_path, monkeypatch):
        from gitpilot.session import SessionManager

        manager = SessionManager(root=tmp_path / "sessions")
        session = manager.create(name="ordinary")
        manager.save(session)

        monkeypatch.setattr(
            "gitpilot.session.SessionManager", lambda *a, **k: manager,
        )
        assert session_runs_without_asking(session.id) is False

    def test_an_unknown_session_is_not_trusted(self):
        assert session_runs_without_asking("no-such-session") is False
        assert session_runs_without_asking("") is False


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class TestStore:
    def test_a_token_is_bound_to_its_plan(self):
        store = SandboxApprovalStore()
        store.remember(plan_id="p1", session_id="s", language="python", code="a")
        store.remember(plan_id="p2", session_id="s", language="python", code="b")

        token = store.approve("p1", "s")
        assert token is not None

        ok, reason = store.consume(token.token, language="python", code="b", session_id="s")
        assert ok is False
        assert "does not match" in reason

    def test_a_token_expires(self):
        store = SandboxApprovalStore(ttl_s=-1)
        store.remember(plan_id="p1", session_id="s", language="python", code="a")
        token = store.approve("p1", "s")

        ok, reason = store.consume(token.token, language="python", code="a", session_id="s")

        assert ok is False
        assert "expired" in reason

    def test_a_token_cannot_be_spent_by_another_session(self):
        store = SandboxApprovalStore()
        store.remember(plan_id="p1", session_id="mine", language="python", code="a")
        token = store.approve("p1", "mine")

        ok, reason = store.consume(
            token.token, language="python", code="a", session_id="someone-else",
        )

        assert ok is False
        assert "different session" in reason

    def test_a_plan_cannot_be_approved_by_another_session(self):
        store = SandboxApprovalStore()
        store.remember(plan_id="p1", session_id="mine", language="python", code="a")
        assert store.approve("p1", "someone-else") is None

    def test_the_plan_store_is_bounded(self):
        store = SandboxApprovalStore(max_plans=4)
        for index in range(20):
            store.remember(
                plan_id=f"p{index}", session_id="s", language="python", code="a",
            )
            time.sleep(0.001)
        assert store.plan("p0") is None
        assert store.plan("p19") is not None

    def test_expired_tokens_can_be_purged(self):
        store = SandboxApprovalStore(ttl_s=-1)
        store.remember(plan_id="p1", session_id="s", language="python", code="a")
        store.approve("p1", "s")
        assert store.purge_expired() == 1
        assert len(store) == 0

    def test_the_digest_ignores_language_casing_only(self):
        assert content_digest("Python", "a") == content_digest("python", "a")
        assert content_digest("python", "a") != content_digest("python", "b")


class TestInternalCaller:
    def test_the_agents_own_snippet_path_carries_a_token(self):
        """`terminal.run_snippet` reaches this endpoint through
        ``sandbox_routing``. It is already authorized by the policy engine, but
        an internal-caller escape hatch would be the same "trust whoever asks"
        defect — so it records the approval instead."""
        from gitpilot.sandbox_routing import _mint_internal_approval
        from gitpilot.sandbox_tokens import get_store

        token = _mint_internal_approval("python", "print(1)")

        assert token
        ok, _reason = get_store().consume(token, language="python", code="print(1)")
        assert ok is True
