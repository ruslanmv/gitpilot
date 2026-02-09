"""Tests for gitpilot.session module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitpilot.session import Checkpoint, Message, Session, SessionManager


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

class TestSession:
    def test_defaults(self):
        s = Session()
        assert s.id  # non-empty
        assert s.status == "active"
        assert s.messages == []
        assert s.checkpoints == []

    def test_add_message(self):
        s = Session()
        s.add_message("user", "hello")
        assert len(s.messages) == 1
        assert s.messages[0].role == "user"
        assert s.messages[0].content == "hello"

    def test_add_message_updates_timestamp(self):
        s = Session()
        old = s.updated_at
        s.add_message("assistant", "hi")
        assert s.updated_at >= old

    def test_to_dict_roundtrip(self):
        s = Session(repo_full_name="o/r", branch="main")
        s.add_message("user", "test")
        d = s.to_dict()
        restored = Session.from_dict(d)
        assert restored.id == s.id
        assert len(restored.messages) == 1
        assert restored.messages[0].content == "test"

    def test_from_dict_with_checkpoints(self):
        d = {
            "id": "abc123",
            "messages": [],
            "checkpoints": [
                {"id": "cp1", "message_index": 0, "description": "start",
                 "timestamp": "2024-01-01T00:00:00+00:00", "snapshot_path": None},
            ],
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "status": "active",
            "metadata": {},
        }
        s = Session.from_dict(d)
        assert s.id == "abc123"
        assert len(s.checkpoints) == 1
        assert s.checkpoints[0].id == "cp1"


# ---------------------------------------------------------------------------
# SessionManager CRUD
# ---------------------------------------------------------------------------

class TestSessionManagerCRUD:
    def test_create_and_load(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create(repo_full_name="o/r", branch="main", name="test")
        loaded = mgr.load(session.id)
        assert loaded.id == session.id
        assert loaded.repo_full_name == "o/r"

    def test_load_missing_raises(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        with pytest.raises(FileNotFoundError, match="Session not found"):
            mgr.load("nonexistent")

    def test_save_overwrites(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create(name="v1")
        session.name = "v2"
        mgr.save(session)
        loaded = mgr.load(session.id)
        assert loaded.name == "v2"

    def test_delete_existing(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create()
        assert mgr.delete(session.id) is True
        assert not (tmp_path / f"{session.id}.json").exists()

    def test_delete_nonexistent(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        assert mgr.delete("nope") is False


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_lists_all(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        mgr.create(name="one")
        mgr.create(name="two")
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_filter_by_repo(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        mgr.create(repo_full_name="o/a")
        mgr.create(repo_full_name="o/b")
        sessions = mgr.list_sessions(repo_full_name="o/a")
        assert len(sessions) == 1
        assert sessions[0]["repo"] == "o/a"

    def test_limit(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        for i in range(5):
            mgr.create(name=f"s{i}")
        sessions = mgr.list_sessions(limit=3)
        assert len(sessions) == 3


# ---------------------------------------------------------------------------
# fork
# ---------------------------------------------------------------------------

class TestFork:
    def test_fork_full(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        original = mgr.create(repo_full_name="o/r", name="orig")
        original.add_message("user", "msg1")
        original.add_message("assistant", "msg2")
        mgr.save(original)

        forked = mgr.fork(original.id)
        assert forked.id != original.id
        assert len(forked.messages) == 2
        assert forked.metadata["forked_from"] == original.id
        assert "Fork of" in forked.name

    def test_fork_at_message(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        original = mgr.create()
        original.add_message("user", "m0")
        original.add_message("assistant", "m1")
        original.add_message("user", "m2")
        mgr.save(original)

        forked = mgr.fork(original.id, at_message=1)
        assert len(forked.messages) == 2  # m0 and m1


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    def test_create_checkpoint_no_workspace(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create()
        session.add_message("user", "hello")
        mgr.save(session)

        cp = mgr.create_checkpoint(session, description="manual save")
        assert cp.message_index == 1
        assert cp.description == "manual save"
        assert cp.snapshot_path is None
        assert len(session.checkpoints) == 1

    def test_create_checkpoint_with_workspace(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create()
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "file.txt").write_text("data")

        cp = mgr.create_checkpoint(session, workspace_path=ws, description="snap")
        assert cp.snapshot_path is not None
        assert Path(cp.snapshot_path).exists()

    def test_rewind_to_checkpoint(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create()
        session.add_message("user", "m0")
        session.add_message("assistant", "m1")
        mgr.save(session)

        cp = mgr.create_checkpoint(session, description="before change")
        session.add_message("user", "m2")
        session.add_message("assistant", "m3")
        mgr.save(session)

        assert len(session.messages) == 4
        mgr.rewind_to_checkpoint(session, cp.id)
        assert len(session.messages) == 2  # m0 and m1

    def test_rewind_invalid_checkpoint(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create()
        with pytest.raises(ValueError, match="Checkpoint not found"):
            mgr.rewind_to_checkpoint(session, "bad-id")
