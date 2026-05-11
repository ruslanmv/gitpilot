"""Regression test for /api/chat/plan friendly-error surfacing.

When the planner raises one of the known friendly-error RuntimeErrors
from :mod:`gitpilot.agentic` (refusal, ValidationError, hallucination),
the API endpoint must:

1. Match the error message against ``_plan_parse_markers`` and try
   the Lite-mode planner as a fallback.
2. If the fallback also fails, return HTTP 502 with an actionable
   ``detail`` describing how to recover — NOT a generic HTTP 500
   carrying just the raw RuntimeError text.

Before this commit the friendly RuntimeError leaked through as 500
and the UI rendered "Error: The planner did not return a valid plan
structure ..." with no further guidance.  These tests pin every
known-bad message so the regression cannot reappear silently.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot import api as api_module


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Spin up the real FastAPI app with stubbed-out planners."""
    yield TestClient(api_module.app)


def _mount_failing_planners(
    monkeypatch: pytest.MonkeyPatch,
    *,
    main_error: str,
    lite_error: str | None = None,
) -> None:
    """Replace both planner entry points so we can drive the error path
    deterministically — no LLM calls, no GitHub network."""

    async def _bad_main(goal, repo_full_name, token=None, branch_name=None):
        raise RuntimeError(main_error)

    async def _bad_lite(goal, repo_full_name, token=None, branch_name=None):
        if lite_error is None:
            return {"goal": goal, "summary": "lite ok", "steps": []}
        raise RuntimeError(lite_error)

    monkeypatch.setattr(api_module, "generate_plan", _bad_main)
    monkeypatch.setattr(api_module, "generate_plan_lite", _bad_lite)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)

    # ``execution_context`` is a contextmanager that may require auth state;
    # stub to a no-op contextmanager so we never touch credentials in tests.
    from contextlib import contextmanager

    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)


# ----------------------------------------------------------------------
# Marker → fallback path (main planner fails, Lite recovers)
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "marker",
    [
        "The planner did not return a valid plan structure.  Re-run.",
        "The repository explorer did not return a usable result.  Re-run.",
        "The planner refused to produce a plan.  Re-run the request.",
        "The planner produced paths that do not match this repository.",
        "1 validation error for PlanResult\nsteps - Field required",
    ],
)
def test_marker_falls_back_to_lite_and_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    _mount_failing_planners(monkeypatch, main_error=marker, lite_error=None)
    resp = client.post(
        "/api/chat/plan",
        json={"goal": "do thing", "repo_owner": "x", "repo_name": "y", "branch_name": "main"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("summary") == "lite ok"


# ----------------------------------------------------------------------
# Both fail → friendly 502 (NOT a bare 500)
# ----------------------------------------------------------------------

def test_both_planners_fail_returns_friendly_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mount_failing_planners(
        monkeypatch,
        main_error="The planner did not return a valid plan structure.",
        lite_error="lite also broken",
    )
    resp = client.post(
        "/api/chat/plan",
        json={"goal": "do thing", "repo_owner": "x", "repo_name": "y", "branch_name": "main"},
    )
    assert resp.status_code == 502, resp.text
    detail = resp.json().get("detail", "")
    # The user-facing message must be actionable, not just an exception repr.
    assert "small-model" in detail.lower() or "switch to" in detail.lower()
    assert "ollama" in detail.lower() or "openai" in detail.lower()


# ----------------------------------------------------------------------
# Unknown error → wrapped 500 with the message in detail (not bare 500)
# ----------------------------------------------------------------------

def test_unknown_runtime_error_is_wrapped_as_500_with_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mount_failing_planners(monkeypatch, main_error="completely unrelated boom")
    resp = client.post(
        "/api/chat/plan",
        json={"goal": "do thing", "repo_owner": "x", "repo_name": "y", "branch_name": "main"},
    )
    assert resp.status_code == 500, resp.text
    detail = resp.json().get("detail", "")
    # The original message is preserved in ``detail`` so the UI can
    # surface something actionable, not the framework's default
    # "Internal Server Error" boilerplate.
    assert "completely unrelated boom" in detail


# ----------------------------------------------------------------------
# Happy path sanity — make sure we didn't break the success case
# ----------------------------------------------------------------------

def test_planner_success_passes_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ok(goal, repo_full_name, token=None, branch_name=None):
        return {"goal": goal, "summary": "real plan", "steps": []}

    monkeypatch.setattr(api_module, "generate_plan", _ok)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)

    from contextlib import contextmanager

    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)

    resp = client.post(
        "/api/chat/plan",
        json={"goal": "do thing", "repo_owner": "x", "repo_name": "y", "branch_name": "main"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"] == "real plan"
