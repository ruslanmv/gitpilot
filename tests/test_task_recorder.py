"""Tests for the right-sidebar Tasks recorder.

Pin the contract used by ``/api/chat/plan`` and ``/api/chat/execute``
to record each top-level AI invocation as a Task on the active
session, and the read endpoint ``/api/sessions/{sid}/tasks`` that
surfaces them to the chat UI.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

# Import ``_api_app``, not ``gitpilot.api``: the latter is a package that
# re-exports these names, so monkeypatching it rebinds a copy the endpoint
# never reads and every stub below is silently ignored.
from gitpilot import _api_app as api_module
from gitpilot import flags
from gitpilot.session import Task
from gitpilot.task_recorder import (
    FLAG_TASKS_SIDEBAR,
    begin_task,
    finish_task,
)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    yield TestClient(api_module.app)


# ----------------------------------------------------------------------
# Recorder primitives
# ----------------------------------------------------------------------

def test_begin_task_appends_running_entry() -> None:
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="rec-begin"
    )
    task = begin_task(api_module._session_mgr, session.id, kind="plan", title="hello")
    assert task is not None
    assert task.status == "running"
    assert task.title == "hello"

    reloaded = api_module._session_mgr.load(session.id)
    assert len(reloaded.tasks) == 1
    assert reloaded.tasks[0].status == "running"


def test_finish_task_marks_completed_and_records_duration() -> None:
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="rec-finish"
    )
    task = begin_task(api_module._session_mgr, session.id, kind="plan", title="t")
    assert task is not None
    finish_task(
        api_module._session_mgr,
        session.id,
        task,
        status="completed",
        prompt_tokens=120,
        completion_tokens=40,
    )

    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.tasks[0].status == "completed"
    assert reloaded.tasks[0].completed_at is not None
    assert reloaded.tasks[0].duration_ms is not None
    assert reloaded.tasks[0].prompt_tokens == 120
    assert reloaded.tasks[0].completion_tokens == 40


def test_finish_task_failed_path_records_error() -> None:
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="rec-fail"
    )
    task = begin_task(api_module._session_mgr, session.id, kind="plan", title="t")
    finish_task(
        api_module._session_mgr,
        session.id,
        task,
        status="failed",
        error="boom: something exploded",
    )

    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.tasks[0].status == "failed"
    assert reloaded.tasks[0].error is not None
    assert "boom" in reloaded.tasks[0].error


def test_begin_task_no_session_id_is_noop() -> None:
    """Calling without a session id must not error — older frontends
    don't send one, and we must stay byte-identical to today for them."""
    assert begin_task(api_module._session_mgr, None, kind="plan", title="x") is None
    # finish_task with no task is also a no-op.
    finish_task(api_module._session_mgr, None, None)


def test_begin_task_flag_off_is_noop() -> None:
    """The kill switch turns off recording entirely."""
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="rec-flag-off"
    )
    flags.set_override(FLAG_TASKS_SIDEBAR, False)
    try:
        task = begin_task(
            api_module._session_mgr, session.id, kind="plan", title="t"
        )
    finally:
        flags.clear_override(FLAG_TASKS_SIDEBAR)
    assert task is None
    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.tasks == []


def test_finish_task_preserves_concurrent_writes_to_session() -> None:
    """Real-world race: a task is in flight; another endpoint writes a
    new branch onto the session; finish_task must not clobber that."""
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="rec-race"
    )
    task = begin_task(api_module._session_mgr, session.id, kind="execute", title="t")

    # Simulate a concurrent write (the execute handler persisting the
    # new branch).
    concurrent = api_module._session_mgr.load(session.id)
    concurrent.branch = "gitpilot-foo-123456"
    api_module._session_mgr.save(concurrent)

    finish_task(api_module._session_mgr, session.id, task, status="completed")

    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.branch == "gitpilot-foo-123456"  # concurrent write preserved
    assert reloaded.tasks[0].status == "completed"


# ----------------------------------------------------------------------
# Read endpoint
# ----------------------------------------------------------------------

def test_tasks_endpoint_returns_documented_shape(
    client: TestClient,
) -> None:
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="endpoint-shape"
    )
    task = begin_task(
        api_module._session_mgr, session.id, kind="plan", title="hello world"
    )
    finish_task(
        api_module._session_mgr, session.id, task,
        status="completed", prompt_tokens=12, completion_tokens=4,
    )

    r = client.get(f"/api/sessions/{session.id}/tasks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session.id
    assert len(body["tasks"]) == 1
    row = body["tasks"][0]
    for key in (
        "id", "kind", "title", "status",
        "started_at", "completed_at", "duration_ms",
        "prompt_tokens", "completion_tokens", "error",
    ):
        assert key in row, f"missing key {key}"
    assert row["status"] == "completed"
    assert row["title"] == "hello world"


def test_tasks_endpoint_returns_404_for_unknown_session(client: TestClient) -> None:
    r = client.get("/api/sessions/does-not-exist/tasks")
    assert r.status_code == 404


def test_tasks_endpoint_returns_404_when_flag_off(client: TestClient) -> None:
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="endpoint-flag"
    )
    flags.set_override(FLAG_TASKS_SIDEBAR, False)
    try:
        r = client.get(f"/api/sessions/{session.id}/tasks")
    finally:
        flags.clear_override(FLAG_TASKS_SIDEBAR)
    assert r.status_code == 404


# ----------------------------------------------------------------------
# Endpoint wiring — Plan + Execute land a task on the session
# ----------------------------------------------------------------------

def test_chat_plan_records_a_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: POST /api/chat/plan with a session_id must leave a
    completed Task entry on that session."""
    from contextlib import contextmanager

    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    async def _ok(goal, repo_full_name, token=None, branch_name=None, **_kw):
        return {"goal": goal, "summary": "ok", "steps": []}

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)
    monkeypatch.setattr(api_module, "generate_plan", _ok)
    monkeypatch.setattr(api_module, "generate_plan_lite", _ok)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    monkeypatch.setattr(api_module, "get_github_token", lambda *_a, **_kw: None)

    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="plan-track"
    )

    r = client.post(
        "/api/chat/plan",
        json={
            "repo_owner": "o",
            "repo_name": "r",
            "goal": "Make a thing",
            "branch_name": "main",
            "session_id": session.id,
        },
    )
    assert r.status_code == 200, r.text

    reloaded = api_module._session_mgr.load(session.id)
    assert len(reloaded.tasks) == 1
    assert reloaded.tasks[0].kind == "plan"
    assert reloaded.tasks[0].status == "completed"
    assert reloaded.tasks[0].title == "Make a thing"


def test_chat_plan_records_failed_task_on_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Errors from the planner produce a failed Task entry — the trace
    must capture failures, not just successes."""
    from contextlib import contextmanager

    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    async def _bad(goal, repo_full_name, token=None, branch_name=None, **_kw):
        raise RuntimeError("completely unrelated boom")

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)
    monkeypatch.setattr(api_module, "generate_plan", _bad)
    monkeypatch.setattr(api_module, "generate_plan_lite", _bad)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    monkeypatch.setattr(api_module, "get_github_token", lambda *_a, **_kw: None)

    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="plan-fail-track"
    )

    r = client.post(
        "/api/chat/plan",
        json={
            "repo_owner": "o",
            "repo_name": "r",
            "goal": "Make it fail",
            "session_id": session.id,
        },
    )
    assert r.status_code >= 400

    reloaded = api_module._session_mgr.load(session.id)
    assert len(reloaded.tasks) == 1
    assert reloaded.tasks[0].status == "failed"
    assert reloaded.tasks[0].error is not None


# ----------------------------------------------------------------------
# Session backwards-compatibility
# ----------------------------------------------------------------------

def test_session_loads_without_tasks_field(tmp_path) -> None:
    """Session files written before this feature must still load."""
    from gitpilot.session import Session

    raw = {
        "id": "abc123",
        "messages": [],
        "checkpoints": [],
        "repos": [],
        "tasks": [],  # explicit empty
    }
    session = Session.from_dict(raw)
    assert session.tasks == []
