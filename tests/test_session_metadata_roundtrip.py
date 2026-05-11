"""Regression test for the "state loss during hydration" bug.

Before this fix, assistant messages stored only their text — the
structured Action Plan / Execution Log / diff payload was dropped at
persist time.  When a session was reloaded, the History view rendered
the bare summary string and the user lost the Step buttons, Create
buttons, and diff affordances.

The frontend already round-trips ``metadata`` correctly (see
``normalizeBackendMessage`` in App.jsx), but the persist call was
sending only ``{role, content}``.  These tests pin both halves of the
contract so the bug cannot regress:

* The backend ``Message`` dataclass survives a save → load → save loop
  with its ``metadata`` intact, including nested objects (the shape
  the planner returns).
* The REST endpoints accept ``metadata`` on POST and echo it on GET.

These tests use the real backend through FastAPI's TestClient — no
mocks — so a regression anywhere along the pipe (POST payload schema,
``add_message`` keyword expansion, ``Session.to_dict``, GET response
shape) is caught.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot import session as session_module
from gitpilot.session import Message, Session, SessionManager


# ----------------------------------------------------------------------
# Dataclass-level round-trip (cheap, runs in milliseconds)
# ----------------------------------------------------------------------

class TestMessageMetadataRoundTrip:
    """``Message.metadata`` must survive every save → load cycle."""

    def test_plain_dataclass_keeps_metadata(self):
        msg = Message(role="assistant", content="Here is the plan.",
                      metadata={"plan": {"steps": [{"step_number": 1, "title": "x"}]}})
        assert msg.metadata["plan"]["steps"][0]["title"] == "x"

    def test_session_add_message_keeps_metadata_via_kwargs(self):
        s = Session()
        # The api.py shim calls ``add_message(role, content, **metadata)``.
        # That kwarg expansion must reach Message.metadata intact.
        s.add_message(
            "assistant",
            "Done.",
            plan={"steps": [{"step_number": 1, "title": "Edit README"}]},
            executionLog={"steps": [{"step_number": 1, "summary": "ok"}]},
            diff={"files_changed": 1, "additions": 4, "deletions": 0},
        )
        m = s.messages[-1]
        assert m.metadata["plan"]["steps"][0]["title"] == "Edit README"
        assert m.metadata["executionLog"]["steps"][0]["summary"] == "ok"
        assert m.metadata["diff"]["files_changed"] == 1

    def test_save_load_round_trip_preserves_nested_metadata(
        self, tmp_path: Path,
    ):
        mgr = SessionManager(root=tmp_path)
        s = Session(repo_full_name="owner/repo", branch="main")
        s.add_message(
            "assistant",
            "Plan ready.",
            plan={
                "goal": "create README demo",
                "summary": "Add demo.py",
                "steps": [
                    {
                        "step_number": 1,
                        "title": "Add demo.py",
                        "description": "Print the README contents.",
                        "files": [{"path": "demo.py", "action": "CREATE"}],
                        "risks": None,
                    },
                ],
            },
        )
        mgr.save(s)
        reloaded = mgr.load(s.id)
        assert len(reloaded.messages) == 1
        round_tripped = reloaded.messages[0].metadata
        assert round_tripped["plan"]["goal"] == "create README demo"
        assert round_tripped["plan"]["steps"][0]["files"][0]["path"] == "demo.py"
        assert round_tripped["plan"]["steps"][0]["files"][0]["action"] == "CREATE"


# ----------------------------------------------------------------------
# REST round-trip — proves the fix the frontend depends on
# ----------------------------------------------------------------------

@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """Spin up the real FastAPI app with an isolated session store."""
    # Make the global SessionManager write into ``tmp_path`` so the test
    # never touches the user's real config dir.
    isolated_dir = tmp_path / "sessions"
    isolated_dir.mkdir()
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))

    # ``gitpilot.api`` resolves the SessionManager at import time, so we
    # reach into the module after import and swap the root.
    from gitpilot import api as api_module
    new_mgr = SessionManager(root=isolated_dir)
    monkeypatch.setattr(api_module, "_session_mgr", new_mgr)

    client = TestClient(api_module.app)
    yield client


def _create_session(client: TestClient) -> str:
    resp = client.post(
        "/api/sessions",
        json={"name": "metadata-roundtrip", "repo_full_name": "owner/repo"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def test_post_message_with_metadata_round_trips(api_client: TestClient) -> None:
    sid = _create_session(api_client)
    plan = {
        "goal": "create README demo",
        "summary": "Add demo.py",
        "steps": [{
            "step_number": 1,
            "title": "Add demo.py",
            "description": "Print the README contents.",
            "files": [{"path": "demo.py", "action": "CREATE"}],
            "risks": None,
        }],
    }
    resp = api_client.post(
        f"/api/sessions/{sid}/message",
        json={
            "role": "assistant",
            "content": "Here is the plan.",
            "metadata": {"plan": plan},
        },
    )
    assert resp.status_code == 200, resp.text

    # GET the messages — metadata must round-trip byte-equivalent.
    resp = api_client.get(f"/api/sessions/{sid}/messages")
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "Here is the plan."
    assert messages[0]["metadata"]["plan"] == plan


def test_post_message_without_metadata_keeps_legacy_shape(
    api_client: TestClient,
) -> None:
    """Legacy callers that POST without metadata must still work."""
    sid = _create_session(api_client)
    resp = api_client.post(
        f"/api/sessions/{sid}/message",
        json={"role": "user", "content": "hi"},
    )
    assert resp.status_code == 200, resp.text
    msgs = api_client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert msgs[0]["content"] == "hi"
    assert msgs[0]["metadata"] == {}


def test_execution_log_and_diff_round_trip(api_client: TestClient) -> None:
    """The exact shape the History view depends on."""
    sid = _create_session(api_client)
    metadata = {
        "executionLog": {
            "steps": [
                {"step_number": 1, "summary": "Created demo.py"},
                {"step_number": 2, "summary": "Ran pytest — 3 passed"},
            ],
        },
        "diff": {"files_changed": 1, "additions": 12, "deletions": 0},
    }
    api_client.post(
        f"/api/sessions/{sid}/message",
        json={"role": "assistant", "content": "Done.", "metadata": metadata},
    )
    msgs = api_client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert msgs[0]["metadata"] == metadata


def test_metadata_handles_unicode_and_nested_lists(api_client: TestClient) -> None:
    sid = _create_session(api_client)
    metadata = {
        "plan": {
            "summary": "汉字 + emoji 🚀",
            "steps": [
                {"step_number": 1, "files": [{"path": "src/main.py", "action": "MODIFY"}]},
            ],
        },
    }
    api_client.post(
        f"/api/sessions/{sid}/message",
        json={"role": "assistant", "content": "ok", "metadata": metadata},
    )
    msgs = api_client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert msgs[0]["metadata"] == metadata
