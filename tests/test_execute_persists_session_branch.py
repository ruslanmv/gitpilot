"""Regression tests for session-branch persistence on /api/chat/execute.

Bug:
    A session was created on ``master``, the user approved a plan, the
    executor created a fresh ``gitpilot-<goal>-<ts>`` branch and pushed
    to it.  ``Session.branch`` was *never updated* to that new branch —
    so reopening the session next day jumped back to ``master`` and the
    user couldn't find their work.

Fix:
    ``/api/chat/execute`` now accepts an optional ``session_id``.  When
    supplied AND the executor returns a branch name, the endpoint loads
    the session, writes the new branch onto both ``session.branch`` and
    (if present) the matching ``session.repos[i].branch``, and saves.

These tests pin every branch of that contract.
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot import api as api_module


@pytest.fixture()
def client() -> Iterator[TestClient]:
    yield TestClient(api_module.app)


def _stub_executor(branch: str) -> None:
    """Replace the real execute path with a deterministic stub that
    returns a fixed branch and step count."""

    async def _fake_execute(plan, repo_full_name, token=None, branch_name=None):
        return {
            "status": "completed",
            "message": "ok",
            "branch": branch,
            "executionLog": [{"step": 1, "title": "noop"}],
        }

    api_module.execute_plan = _fake_execute            # type: ignore[assignment]
    api_module.execute_plan_lite = _fake_execute       # type: ignore[assignment]


def _stub_auth_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the GitHub-token + execution_context plumbing so the
    test doesn't need a network or credentials."""
    from contextlib import contextmanager

    @contextmanager
    def _noop_ctx(*_a: Any, **_kw: Any) -> Iterator[None]:
        yield

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)
    monkeypatch.setattr(api_module, "get_github_token", lambda *_a, **_kw: None)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: True)


def _plan_payload() -> dict:
    """Minimum PlanResult-shaped dict the endpoint will accept."""
    return {
        "goal": "do thing",
        "summary": "noop",
        "steps": [],
    }


# ----------------------------------------------------------------------
# Branch is written through to the session record
# ----------------------------------------------------------------------

def test_execute_persists_new_branch_on_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_auth_and_context(monkeypatch)
    _stub_executor(branch="gitpilot-do-thing-123456")

    session = api_module._session_mgr.create(
        repo_full_name="owner/repo", branch="master", name="branch-persist"
    )

    resp = client.post(
        "/api/chat/execute",
        json={
            "repo_owner": "owner",
            "repo_name": "repo",
            "plan": _plan_payload(),
            "branch_name": None,
            "session_id": session.id,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["branch"] == "gitpilot-do-thing-123456"

    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.branch == "gitpilot-do-thing-123456", (
        "session.branch must be updated to the branch the executor wrote to"
    )


def test_execute_updates_matching_repos_entry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-repo sessions: repos[i].branch for the matching full_name
    is updated alongside the legacy session.branch field."""
    _stub_auth_and_context(monkeypatch)
    _stub_executor(branch="gitpilot-multi-987654")

    session = api_module._session_mgr.create(
        repo_full_name="owner/repo", branch="master", name="multi-repo"
    )
    session.repos = [
        {"full_name": "owner/repo", "branch": "master", "mode": "write"},
        {"full_name": "owner/other", "branch": "trunk", "mode": "read"},
    ]
    session.active_repo = "owner/repo"
    api_module._session_mgr.save(session)

    resp = client.post(
        "/api/chat/execute",
        json={
            "repo_owner": "owner",
            "repo_name": "repo",
            "plan": _plan_payload(),
            "branch_name": None,
            "session_id": session.id,
        },
    )
    assert resp.status_code == 200, resp.text

    reloaded = api_module._session_mgr.load(session.id)
    write_entry = next(r for r in reloaded.repos if r["full_name"] == "owner/repo")
    read_entry = next(r for r in reloaded.repos if r["full_name"] == "owner/other")
    assert write_entry["branch"] == "gitpilot-multi-987654"
    # Untouched second repo retains its original branch.
    assert read_entry["branch"] == "trunk"


# ----------------------------------------------------------------------
# Backwards compatibility: no session_id, no error
# ----------------------------------------------------------------------

def test_execute_without_session_id_is_backwards_compatible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_auth_and_context(monkeypatch)
    _stub_executor(branch="gitpilot-anon-111111")

    resp = client.post(
        "/api/chat/execute",
        json={
            "repo_owner": "owner",
            "repo_name": "repo",
            "plan": _plan_payload(),
            "branch_name": None,
        },
    )
    assert resp.status_code == 200, resp.text
    # Result still carries the executor's branch — that's the
    # frontend's only signal in the legacy path.
    assert resp.json()["branch"] == "gitpilot-anon-111111"


def test_execute_unknown_session_id_does_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale session id from the frontend cache must not poison the
    execute result — the user already has their commit published."""
    _stub_auth_and_context(monkeypatch)
    _stub_executor(branch="gitpilot-stale-222222")

    resp = client.post(
        "/api/chat/execute",
        json={
            "repo_owner": "owner",
            "repo_name": "repo",
            "plan": _plan_payload(),
            "branch_name": None,
            "session_id": "does-not-exist",
        },
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# Sticky mode: caller already on the session branch
# ----------------------------------------------------------------------

def test_execute_sticky_mode_keeps_branch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the request specifies branch_name (sticky mode), the
    session must record the same branch — this is what reopening the
    session lands on next time."""
    _stub_auth_and_context(monkeypatch)
    _stub_executor(branch="gitpilot-sticky-333333")

    session = api_module._session_mgr.create(
        repo_full_name="owner/repo", branch="master", name="sticky"
    )

    resp = client.post(
        "/api/chat/execute",
        json={
            "repo_owner": "owner",
            "repo_name": "repo",
            "plan": _plan_payload(),
            "branch_name": "gitpilot-sticky-333333",
            "session_id": session.id,
        },
    )
    assert resp.status_code == 200, resp.text

    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.branch == "gitpilot-sticky-333333"
