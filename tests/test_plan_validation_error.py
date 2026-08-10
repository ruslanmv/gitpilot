"""Regression test for the production failure where the planner's
Final Answer was a ReAct-format Thought/Action block instead of JSON,
and Pydantic raised ``ValidationError: 3 validation errors for
PlanResult`` while validating an empty ``{}`` extracted from the
output.

Before this fix the ValidationError escaped through the FastAPI
handler as an HTTP 500.  After the fix ``generate_plan`` catches it
and re-raises a clear ``RuntimeError`` carrying the user-facing
message the UI already knows how to render.

We don't drive the full agent pipeline here — we patch
``_guarded_agent_call`` so the validation error fires at the exact
boundary the bug occurred on.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from gitpilot import agentic


class _Inner(BaseModel):
    goal: str
    summary: str
    steps: list


def _make_validation_error() -> ValidationError:
    """Build the exact ValidationError shape Pydantic raises when
    PlanResult is validated against an empty dict."""
    try:
        _Inner.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError from empty payload")


async def _drive_generate_plan(
    monkeypatch: pytest.MonkeyPatch,
    fail_on: str,
) -> RuntimeError:
    """Run ``generate_plan`` with ``_guarded_agent_call`` configured to
    raise a Pydantic ValidationError on the named label.  Returns the
    captured RuntimeError for the caller to inspect.

    ``fail_on`` must be one of ``"explore_repo"`` (explorer agent) or
    ``"generate_plan"`` (planner agent) — the two boundaries where
    CrewAI's schema conversion can fire.
    """
    validation_error = _make_validation_error()

    async def _maybe_raise(ctx: Any, fn: Any, *, label: str = "") -> Any:
        if label == fail_on:
            raise validation_error
        # Other calls succeed with a benign result so the function can
        # progress to the named failure site.
        class _Stub:
            raw = "explorer ok"
            pydantic = None
            tasks_output = []
        return _Stub()

    monkeypatch.setattr(agentic, "_guarded_agent_call", _maybe_raise)

    # Avoid importing CrewAI / LLMs by stubbing the heavy helpers
    # ``generate_plan`` calls before reaching the guarded section.
    monkeypatch.setattr(agentic, "_build_llm", lambda: object())
    monkeypatch.setattr(
        agentic,
        "_crewai",
        lambda: {
            "Agent": lambda **kw: object(),
            "Task": lambda **kw: object(),
            "Crew": lambda **kw: type("C", (), {"kickoff": lambda self, inputs=None: None})(),
            "Process": type("P", (), {"sequential": object()})(),
        },
    )
    # ``_tools`` is consulted both at planner setup (``set_repo_context``)
    # and during the post-hoc plausibility check (which never runs because
    # we raise before reaching it).  A no-op stub for the setup call is
    # enough to let ``generate_plan`` reach the guarded section.
    async def _no_files_summary(*_a: Any, **_kw: Any) -> dict:
        return {"all_files": []}

    monkeypatch.setattr(
        agentic,
        "_tools",
        lambda: {
            "set_repo_context": lambda *a, **kw: None,
            "get_repository_context_summary": _no_files_summary,
            # The planner agent is built with PLANNER_TOOLS, not
            # REPOSITORY_TOOLS. The key arrived with that split and the stub
            # was not updated, so generate_plan died on KeyError before it
            # could reach the guarded section this test is about.
            "PLANNER_TOOLS": [],
            "REPOSITORY_TOOLS": [],
            "WRITE_TOOLS": [],
            "ISSUE_TOOLS": [],
            "PR_TOOLS": [],
            "SEARCH_TOOLS": [],
            "LOCAL_TOOLS": [],
            "LOCAL_FILE_TOOLS": [],
            "LOCAL_GIT_TOOLS": [],
            "LOCAL_SHELL_TOOLS": [],
        },
    )

    with pytest.raises(RuntimeError) as exc:
        await agentic.generate_plan(
            goal="create a python script",
            repo_full_name="owner/repo",
            token=None,
            branch_name="master",
        )
    return exc.value


@pytest.mark.asyncio
async def test_planner_validation_error_translates_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner emits malformed JSON => ``generate_plan`` raises a clear
    RuntimeError, never a Pydantic ValidationError."""
    err = await _drive_generate_plan(monkeypatch, fail_on="generate_plan")
    msg = str(err)
    assert "did not return a valid plan structure" in msg
    assert "stronger LLM" in msg
    # Original cause preserved for observability.
    assert err.__cause__ is not None
    assert err.__cause__.__class__.__name__ == "ValidationError"


@pytest.mark.asyncio
async def test_explorer_validation_error_translates_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explorer emits malformed output => ``generate_plan`` raises a
    clear RuntimeError before even reaching the planner."""
    err = await _drive_generate_plan(monkeypatch, fail_on="explore_repo")
    msg = str(err)
    assert "did not return a usable result" in msg
    assert "stronger LLM" in msg
    assert err.__cause__ is not None
    assert err.__cause__.__class__.__name__ == "ValidationError"


def test_validation_error_message_count_is_correct() -> None:
    """Sanity: the fixture above produces three field-missing errors,
    matching the exact production payload (goal / summary / steps)."""
    err = _make_validation_error()
    fields = {e["loc"][0] for e in err.errors()}
    assert fields == {"goal", "summary", "steps"}
    assert len(err.errors()) == 3
