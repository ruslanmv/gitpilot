"""Checkpoint and rewind over HTTP.

These cover the wiring rather than the store: that the routes exist at all (the
create route used to raise TypeError on every call) and that a rewind puts both
the files and the conversation back.

**The automatic-snapshot tests moved in Batch V4-D4** to
``tests/agent/test_checkpointing_hooks.py``, because the thing they exercised
moved. They drove ``ApprovalGate.on_checkpoint`` — a hook on a gate whose
``check()`` has never had a caller, so the behaviour they proved correct has
never actually run in production. The loop owns snapshots now, which is also what
stops the ``ask`` path from taking two of them. The same intents are asserted
there against the real owner: a mutating call snapshots, a read does not, a
failure never blocks the tool, no workspace means no snapshots, and the
transcript is the current one.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_app
from gitpilot.session import SessionManager


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git binary required")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "app.py").write_text("original\n")
    return ws


@pytest.fixture()
def session_mgr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionManager:
    """Point the live app at a throwaway session store."""
    mgr = SessionManager(root=tmp_path / "state" / "sessions")
    monkeypatch.setattr(api_app, "_session_mgr", mgr)
    return mgr


@pytest.fixture()
def session(session_mgr: SessionManager, workspace: Path):
    s = session_mgr.create(name="rewind test")
    s.mode = "local_git"
    s.repo_root = str(workspace)
    s.add_message("user", "change the greeting")
    session_mgr.save(s)
    return s


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api_app.app)


# ── The routes exist and agree with each other ───────────────────────────


def test_creating_a_checkpoint_returns_a_usable_record(client, session):
    """This route used to raise TypeError on every call: it passed `label=`,
    which create_checkpoint has never accepted, then read cp.label."""
    res = client.post(
        f"/api/sessions/{session.id}/checkpoint", json={"description": "before edit"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["id"]
    assert body["description"] == "before edit"
    assert body["has_files"] is True
    assert body["message_index"] == 1


def test_checkpoints_are_listed_newest_first(client, session):
    for label in ("first", "second"):
        client.post(f"/api/sessions/{session.id}/checkpoint", json={"description": label})

    body = client.get(f"/api/sessions/{session.id}/checkpoints").json()

    assert [c["description"] for c in body["checkpoints"]] == ["second", "first"]
    assert body["message_count"] == 1


def test_rewind_restores_files_and_conversation_together(
    client, session, session_mgr, workspace
):
    created = client.post(
        f"/api/sessions/{session.id}/checkpoint", json={"description": "before edit"}
    ).json()

    # The agent edits a file, invents another, and says so.
    (workspace / "app.py").write_text("edited by the agent\n")
    (workspace / "invented.py").write_text("should not exist\n")
    live = session_mgr.load(session.id)
    live.add_message("assistant", "done")
    session_mgr.save(live)

    res = client.post(
        f"/api/sessions/{session.id}/rewind", json={"checkpoint_id": created["id"]}
    )

    assert res.status_code == 200
    assert (workspace / "app.py").read_text() == "original\n"
    assert not (workspace / "invented.py").exists()
    assert len(res.json()["messages"]) == 1, "the transcript must rewind too"
    assert len(session_mgr.load(session.id).messages) == 1


def test_rewinding_to_an_unknown_checkpoint_is_a_404(client, session):
    res = client.post(
        f"/api/sessions/{session.id}/rewind", json={"checkpoint_id": "nope"}
    )
    assert res.status_code == 404


def test_rewind_without_a_checkpoint_id_is_a_400(client, session):
    assert client.post(f"/api/sessions/{session.id}/rewind", json={}).status_code == 400


def test_checkpoint_routes_404_on_an_unknown_session(client, session_mgr):
    assert client.get("/api/sessions/missing/checkpoints").status_code == 404
    assert client.post("/api/sessions/missing/checkpoint", json={}).status_code == 404
    assert (
        client.post("/api/sessions/missing/rewind", json={"checkpoint_id": "x"}).status_code
        == 404
    )


def test_a_session_with_no_workspace_still_checkpoints_the_conversation(
    client, session_mgr
):
    """GitHub-mode sessions have no local tree; they must not guess at a path."""
    s = session_mgr.create(name="github mode")
    s.repo_full_name = "owner/repo"
    s.add_message("user", "hello")
    session_mgr.save(s)

    body = client.post(f"/api/sessions/{s.id}/checkpoint", json={}).json()

    assert body["has_files"] is False
    assert body["message_index"] == 1


# ── Where the automatic snapshots went ───────────────────────────────────


def test_the_gate_no_longer_owns_checkpointing():
    """A guard on the move, not a behaviour test.

    If ``ApprovalGate`` regrows an ``on_checkpoint`` hook, there are two owners
    again and the ``ask`` path can snapshot twice — which is the bug Batch V4-D4
    removed. The loop-owned checkpointer is tested in
    ``tests/agent/test_checkpointing_hooks.py``.
    """
    from gitpilot.approval_protocol import ApprovalGate

    assert "on_checkpoint" not in ApprovalGate.__init__.__code__.co_varnames
    assert not hasattr(api_app, "_auto_checkpoint_hook"), (
        "the API module's gate hook should be gone with its only caller"
    )
