"""Checkpoint and rewind over HTTP, plus the gate that takes them automatically.

These cover the wiring rather than the store: that the routes exist at all
(the create route used to raise TypeError on every call), that a rewind puts
both the files and the conversation back, and that a snapshot is taken before
each mutating tool without ever blocking the tool itself.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_app
from gitpilot.agent_events import AgentEventBus
from gitpilot.approval_protocol import ApprovalGate
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


# ── The gate snapshots before every write ────────────────────────────────


def _check(gate: ApprovalGate, tool: str, args: dict) -> bool:
    return asyncio.run(gate.check(tool, args))


def test_auto_mode_checkpoints_before_a_mutating_tool(session, session_mgr, workspace):
    """Auto mode is the one that most needs an undo: nobody saw the edit coming."""
    gate = ApprovalGate(
        AgentEventBus(), mode="auto", on_checkpoint=api_app._auto_checkpoint_hook(session)
    )

    assert _check(gate, "write_file", {"path": "app.py"}) is True

    checkpoints = session_mgr.load(session.id).checkpoints
    assert len(checkpoints) == 1
    assert checkpoints[0].tool_name == "write_file"
    assert checkpoints[0].target_path == "app.py"
    assert "app.py" in checkpoints[0].description


def test_reading_does_not_checkpoint(session, session_mgr):
    gate = ApprovalGate(
        AgentEventBus(), mode="auto", on_checkpoint=api_app._auto_checkpoint_hook(session)
    )

    assert _check(gate, "read_file", {"path": "app.py"}) is True
    assert session_mgr.load(session.id).checkpoints == [], (
        "snapshotting before a read would fill the history with nothing"
    )


def test_plan_mode_blocks_and_does_not_checkpoint(session, session_mgr):
    gate = ApprovalGate(
        AgentEventBus(), mode="plan", on_checkpoint=api_app._auto_checkpoint_hook(session)
    )

    assert _check(gate, "write_file", {"path": "app.py"}) is False
    assert session_mgr.load(session.id).checkpoints == []


def test_a_failing_checkpoint_never_blocks_the_tool():
    """A safety net that stops the show is worse than one with a hole in it."""

    def explode(tool_name: str, args: dict) -> None:
        raise RuntimeError("disk full")

    gate = ApprovalGate(AgentEventBus(), mode="auto", on_checkpoint=explode)

    assert _check(gate, "write_file", {"path": "app.py"}) is True


def test_a_session_without_a_workspace_gets_no_hook(session_mgr):
    """Recording checkpoints that could never be restored helps nobody."""
    s = session_mgr.create(name="no workspace")
    session_mgr.save(s)

    assert api_app._auto_checkpoint_hook(s) is None
    assert api_app._auto_checkpoint_hook(None) is None


def test_the_checkpoint_carries_the_conversation_as_it_is_now(
    session, session_mgr, workspace
):
    """The hook must reload the session: a checkpoint's value is its transcript."""
    gate = ApprovalGate(
        AgentEventBus(), mode="auto", on_checkpoint=api_app._auto_checkpoint_hook(session)
    )

    live = session_mgr.load(session.id)
    live.add_message("assistant", "I'll edit app.py")
    session_mgr.save(live)

    _check(gate, "write_file", {"path": "app.py"})

    checkpoints = session_mgr.load(session.id).checkpoints
    assert checkpoints[0].message_index == 2, (
        "the gate was built before this message, so a stale session would say 1"
    )
