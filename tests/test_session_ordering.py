"""The session picker is ordered by recency, and a new session is in it.

The sidebar used to list sessions in filename order. Filenames are random
hex ids, so "first in the list" meant "won the lottery" — and because the
``limit`` was applied while scanning the directory rather than after ordering,
on a machine with a long history a freshly created session could be cut from
the response entirely. It looked like the new session had not been stored.

These tests pin the two properties that make the list usable: the session you
just touched is the one at the top, and creating one puts it there.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot.session import SessionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_session(
    root: Path,
    session_id: str,
    *,
    updated_at: str | None = "2026-01-01T00:00:00+00:00",
    created_at: str | None = "2026-01-01T00:00:00+00:00",
    repo: str | None = None,
    messages: int = 0,
) -> Path:
    """Write a session file directly, so ids and timestamps are ours to choose."""
    data: dict = {
        "id": session_id,
        "name": session_id,
        "repo_full_name": repo,
        "messages": [{"role": "user", "content": "x", "timestamp": "", "metadata": {}}]
        * messages,
        "checkpoints": [],
        "status": "active",
    }
    if created_at is not None:
        data["created_at"] = created_at
    if updated_at is not None:
        data["updated_at"] = updated_at
    path = root / f"{session_id}.json"
    path.write_text(json.dumps(data))
    return path


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestNewestFirst:
    def test_recency_beats_filename(self, tmp_path):
        """The ids are deliberately in the opposite order to the timestamps."""
        _write_session(tmp_path, "aaa", updated_at="2026-01-01T00:00:00+00:00")
        _write_session(tmp_path, "mmm", updated_at="2026-06-01T00:00:00+00:00")
        _write_session(tmp_path, "zzz", updated_at="2026-03-01T00:00:00+00:00")

        assert _ids(SessionManager(root=tmp_path).list_sessions()) == ["mmm", "zzz", "aaa"]

    def test_a_new_session_is_first(self, tmp_path):
        """The complaint, reduced: create one, and there it is at the top."""
        mgr = SessionManager(root=tmp_path)
        for i in range(5):
            _write_session(tmp_path, f"old{i}", updated_at=f"2026-0{i + 1}-01T00:00:00+00:00")

        fresh = mgr.create(repo_full_name="o/r", name="just made this")
        assert _ids(mgr.list_sessions())[0] == fresh.id

    def test_working_in_an_old_session_brings_it_back_to_the_top(self, tmp_path):
        """Ordering follows attention, not birth order."""
        mgr = SessionManager(root=tmp_path)
        first = mgr.create(name="first")
        time.sleep(0.01)
        mgr.create(name="second")

        first.add_message("user", "still working here")
        mgr.save(first)

        assert _ids(mgr.list_sessions())[0] == first.id

    def test_a_session_with_no_updated_at_falls_back_to_created_at(self, tmp_path):
        _write_session(tmp_path, "old", updated_at=None, created_at="2020-01-01T00:00:00+00:00")
        _write_session(tmp_path, "new", updated_at="2026-01-01T00:00:00+00:00")

        assert _ids(SessionManager(root=tmp_path).list_sessions()) == ["new", "old"]

    def test_a_session_with_no_timestamps_sinks_to_its_file_age(self, tmp_path):
        """A hand-edited file must not sort as if written in 1970, nor as newest."""
        stamped = _write_session(tmp_path, "stamped", updated_at="2026-01-01T00:00:00+00:00")
        bare = _write_session(tmp_path, "bare", updated_at=None, created_at=None)

        # The bare file is a year older on disk than the stamped one claims.
        old = os.stat(stamped).st_mtime - 365 * 24 * 3600
        os.utime(bare, (old, old))

        assert _ids(SessionManager(root=tmp_path).list_sessions()) == ["stamped", "bare"]

    def test_an_unreadable_file_does_not_break_the_list(self, tmp_path):
        (tmp_path / "corrupt.json").write_text("{not json")
        _write_session(tmp_path, "good")

        assert _ids(SessionManager(root=tmp_path).list_sessions()) == ["good"]


# ---------------------------------------------------------------------------
# Limit and filter apply after ordering
# ---------------------------------------------------------------------------


class TestLimitAndFilter:
    def test_the_limit_keeps_the_newest_not_the_first_scanned(self, tmp_path):
        for i in range(6):
            _write_session(tmp_path, f"s{i}", updated_at=f"2026-0{i + 1}-01T00:00:00+00:00")

        rows = SessionManager(root=tmp_path).list_sessions(limit=2)
        assert _ids(rows) == ["s5", "s4"]

    def test_the_repo_filter_runs_before_the_limit(self, tmp_path):
        """Otherwise a busy neighbouring repo empties this repo's sidebar."""
        for i in range(60):
            _write_session(tmp_path, f"other{i:02d}", repo="o/other",
                           updated_at=f"2026-06-01T00:{i:02d}:00+00:00")
        _write_session(tmp_path, "mine", repo="o/mine",
                       updated_at="2026-01-01T00:00:00+00:00")

        rows = SessionManager(root=tmp_path).list_sessions(repo_full_name="o/mine")
        assert _ids(rows) == ["mine"]

    def test_the_cache_does_not_serve_one_query_the_answer_to_another(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        _write_session(tmp_path, "a", repo="o/a")
        _write_session(tmp_path, "b", repo="o/b")

        assert _ids(mgr.list_sessions(repo_full_name="o/a")) == ["a"]
        assert _ids(mgr.list_sessions(repo_full_name="o/b")) == ["b"]
        assert len(mgr.list_sessions()) == 2

    def test_the_cache_notices_a_new_session(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        mgr.create(name="first")
        assert len(mgr.list_sessions()) == 1

        second = mgr.create(name="second")
        rows = mgr.list_sessions()
        assert len(rows) == 2
        assert rows[0]["id"] == second.id


# ---------------------------------------------------------------------------
# The row shape
# ---------------------------------------------------------------------------


class TestSummaryShape:
    def test_summary_matches_a_list_row(self, tmp_path):
        """One shape, so a created session can be rendered by the list's code."""
        mgr = SessionManager(root=tmp_path)
        session = mgr.create(repo_full_name="o/r", branch="main", name="s")

        assert mgr.summary(session) == mgr.list_sessions()[0]

    def test_the_row_carries_what_the_sidebar_draws(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        session = mgr.create_local_git_session(str(tmp_path), branch="main")

        row = mgr.list_sessions()[0]
        # `mode` drives the Git/GH/Dir badge; `created_at` is the tie-break for
        # a session that has been opened but not yet typed into.
        assert row["mode"] == "local_git"
        assert row["created_at"] == session.created_at


# ---------------------------------------------------------------------------
# Over the API the frontend actually calls
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    isolated = tmp_path / "sessions"
    isolated.mkdir()
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))

    from gitpilot import _api_app, api as api_module

    mgr = SessionManager(root=isolated)
    monkeypatch.setattr(_api_app, "_session_mgr", mgr)
    monkeypatch.setattr(api_module, "_session_mgr", mgr, raising=False)

    yield TestClient(api_module.app)


def test_the_api_returns_newest_first(api_client: TestClient) -> None:
    ids = []
    for name in ("one", "two", "three"):
        resp = api_client.post("/api/sessions", json={"repo_full_name": "o/r", "name": name})
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["session_id"])
        time.sleep(0.01)

    listed = _ids(api_client.get("/api/sessions").json()["sessions"])
    assert listed == list(reversed(ids))


def test_creating_a_session_returns_the_row_to_render(api_client: TestClient) -> None:
    """So the new session appears immediately, not after the next poll."""
    resp = api_client.post("/api/sessions", json={"repo_full_name": "o/r", "name": "fresh"})
    row = resp.json()["session"]

    assert row["id"] == resp.json()["session_id"]
    assert row["name"] == "fresh"
    assert row["repo"] == "o/r"
    assert row["message_count"] == 0
    assert row == api_client.get("/api/sessions").json()["sessions"][0]


def test_the_api_filters_by_repo(api_client: TestClient) -> None:
    api_client.post("/api/sessions", json={"repo_full_name": "o/a", "name": "a"})
    api_client.post("/api/sessions", json={"repo_full_name": "o/b", "name": "b"})

    rows = api_client.get("/api/sessions", params={"repo": "o/a"}).json()["sessions"]
    assert [r["name"] for r in rows] == ["a"]


def test_a_created_session_is_immediately_workable(api_client: TestClient) -> None:
    """Stored, not just returned: it accepts a message and reads it back."""
    sid = api_client.post("/api/sessions", json={"repo_full_name": "o/r"}).json()["session_id"]

    assert api_client.post(
        f"/api/sessions/{sid}/message", json={"role": "user", "content": "hello"},
    ).status_code == 200

    messages = api_client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert [m["content"] for m in messages] == ["hello"]
    # ...and sending that message keeps it at the top of the list.
    assert api_client.get("/api/sessions").json()["sessions"][0]["id"] == sid
