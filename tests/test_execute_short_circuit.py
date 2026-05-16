"""Tests for the deterministic EXECUTE short-circuit.

The short-circuit replaces the LLM planner for unambiguous "run this
file" requests.  Two failure classes it has to prevent:

1. The small-LLM CrewAI planner treating ``EXECUTE`` as a tool name,
   failing, retrying, then downgrading the action to ``READ`` (the bug
   that motivated this code path).
2. The latency overhead of an LLM round-trip when the answer is
   deterministic from the routing decision + repo file list.

So the tests assert:
* Unambiguous requests produce an EXECUTE plan with the right path.
* Ambiguous, off-intent, or unrunnable requests fall through to the
  caller (return ``None``), so the LLM planner can disambiguate.
"""
from __future__ import annotations

import pytest

from gitpilot.agentic import (
    PlanResult,
    _is_runnable_path,
    _looks_like_matplotlib,
    try_execute_short_circuit,
)


@pytest.mark.parametrize(
    "goal,intent,targets,repo,expected_path",
    [
        # Named target — most common case ("execute demo.py")
        ("execute demo.py", "execute", ["demo.py"], ["demo.py", "README.md"], "demo.py"),
        # Unnamed but exactly one runnable file in the repo
        ("run the script",  "execute", [],          ["README.md", "tool.py"], "tool.py"),
        # Bash script
        ("run the shell",   "execute", ["build.sh"], ["build.sh"],            "build.sh"),
        # Node script
        ("execute main",    "execute", ["main.js"],  ["main.js"],             "main.js"),
    ],
)
def test_short_circuit_produces_execute_plan(goal, intent, targets, repo, expected_path) -> None:
    plan = try_execute_short_circuit(
        goal=goal, intent=intent, target_files=targets, repo_files=repo,
    )
    assert isinstance(plan, PlanResult)
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert len(step.files) == 1
    assert step.files[0].path == expected_path
    assert step.files[0].action == "EXECUTE"


@pytest.mark.parametrize(
    "intent,targets,repo,reason",
    [
        # Wrong intent — planner has to handle it
        ("find",    ["demo.py"],          ["demo.py"],            "intent != execute"),
        ("modify",  ["demo.py"],          ["demo.py"],            "intent != execute"),
        ("unknown", ["demo.py"],          ["demo.py"],            "intent != execute"),
        # Unrunnable target — planner has to ask or refuse
        ("execute", ["README.md"],        ["README.md"],          "not a runnable extension"),
        # Multiple runnable files but none named — operator must pick
        ("execute", [],                   ["a.py", "b.py"],       "ambiguous repo"),
        # No targets, empty repo — planner has to ask
        ("execute", [],                   [],                     "nothing to run"),
        # Multiple named targets — operator has to pick
        ("execute", ["a.py", "b.py"],     ["a.py", "b.py"],       "multiple targets"),
    ],
)
def test_short_circuit_falls_through(intent, targets, repo, reason) -> None:
    plan = try_execute_short_circuit(
        goal="...", intent=intent, target_files=targets, repo_files=repo,
    )
    assert plan is None, f"expected fallthrough ({reason}), got plan with {plan}"


def test_is_runnable_path_covers_expected_extensions() -> None:
    for runnable in ("foo.py", "bar.js", "baz.mjs", "x.cjs", "build.sh", "go.bash"):
        assert _is_runnable_path(runnable), runnable
    for non_runnable in ("README.md", "data.json", "image.png", "Dockerfile", "Makefile"):
        assert not _is_runnable_path(non_runnable), non_runnable


def test_matplotlib_detector_hits_common_idioms() -> None:
    # The actual matplotlib-using code patterns we want to catch.
    assert _looks_like_matplotlib("import matplotlib.pyplot as plt\nplt.show()")
    assert _looks_like_matplotlib("from matplotlib import pyplot")
    assert _looks_like_matplotlib("import matplotlib\nmatplotlib.use('Agg')")
    # Should NOT trip on unrelated code (false positives waste time but
    # are otherwise harmless; we still verify the common case).
    assert not _looks_like_matplotlib("print('hello world')")
    assert not _looks_like_matplotlib("import numpy as np\nx = np.zeros(10)")


def test_short_circuit_summary_mentions_target_and_outcome_contract() -> None:
    """UX contract: the plan summary the modal renders must name the
    file *and* signal that stdout/stderr/exit-code will come back —
    otherwise users don't understand what just got planned."""
    plan = try_execute_short_circuit(
        goal="execute demo.py", intent="execute",
        target_files=["demo.py"], repo_files=["demo.py"],
    )
    assert plan is not None
    assert "demo.py" in plan.summary
    assert "stdout" in plan.summary
    assert "exit code" in plan.summary
