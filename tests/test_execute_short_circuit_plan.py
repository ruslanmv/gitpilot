"""Tests for the executor short-circuit attaching an ExecutionPlan.

When the router detects an unambiguous RUN_FILE intent, the short
circuit returns a PlanResult whose ``execution_plan`` field carries
the approval-card data the chat UI renders.
"""

from __future__ import annotations

from gitpilot.agentic import try_execute_short_circuit


def test_short_circuit_attaches_execution_plan() -> None:
    plan = try_execute_short_circuit(
        goal="please run demo.py",
        intent="execute",
        target_files=["demo.py"],
        repo_files=["demo.py", "README.md"],
    )
    assert plan is not None
    assert plan.execution_plan is not None
    ep = plan.execution_plan
    assert ep["file"] == "demo.py"
    assert ep["command"] == ["python", "demo.py"]
    assert ep["requires_approval"] is True
    assert "safety" in ep and "checks" in ep["safety"]


def test_short_circuit_no_target_no_plan() -> None:
    plan = try_execute_short_circuit(
        goal="run something",
        intent="execute",
        target_files=[],
        repo_files=["README.md"],  # no runnable file
    )
    assert plan is None


def test_short_circuit_picks_unique_runnable() -> None:
    plan = try_execute_short_circuit(
        goal="run the script",
        intent="execute",
        target_files=[],
        repo_files=["main.py", "README.md"],
    )
    assert plan is not None
    assert plan.execution_plan is not None
    assert plan.execution_plan["file"] == "main.py"


def test_short_circuit_not_execute_intent() -> None:
    plan = try_execute_short_circuit(
        goal="explain demo.py",
        intent="info",
        target_files=["demo.py"],
        repo_files=["demo.py"],
    )
    assert plan is None


def test_short_circuit_extracts_unverified_path_from_goal() -> None:
    """The most common 'empty plan' failure: user just created demo.py
    on the branch; the router's tree fetch raced or was cached without
    it, so target_files is empty. We must still produce a plan from the
    explicit goal text instead of falling through to the LLM."""
    plan = try_execute_short_circuit(
        goal="run demo.py",
        intent="execute",
        target_files=[],
        repo_files=["README.md", "src/main.py", "src/helpers.py"],
    )
    assert plan is not None
    assert plan.execution_plan is not None
    assert plan.execution_plan["file"] == "demo.py"
    # The unverified-file warning must be present so the user sees
    # honest context, but approval is still possible.
    warnings = plan.execution_plan["safety"]["warnings"]
    assert any("not in cached repo tree" in w["label"].lower() for w in warnings)
    # And the "File exists" check is marked unverified, not falsely green.
    checks = plan.execution_plan["safety"]["checks"]
    file_check = next((c for c in checks if "demo.py" in c["label"]), None)
    assert file_check is not None
    assert file_check["ok"] is False


def test_short_circuit_extracts_path_from_natural_phrasing() -> None:
    """The exact phrasing from the user's bug report — 'please execute
    this demo.py code in the sandbox' — must short-circuit, not return
    an empty plan."""
    plan = try_execute_short_circuit(
        goal="please execute this demo.py code in the sandbox",
        intent="execute",
        target_files=[],
        repo_files=["README.md"],
    )
    assert plan is not None
    assert plan.execution_plan is not None
    assert plan.execution_plan["file"] == "demo.py"


def test_short_circuit_extracts_first_when_multiple_mentioned() -> None:
    """Bareword extractor preserves goal order; the short-circuit
    requires exactly one candidate, so multiple distinct filenames
    fall through to the LLM (which can disambiguate)."""
    plan = try_execute_short_circuit(
        goal="run a.py and then b.py",
        intent="execute",
        target_files=[],
        repo_files=[],
    )
    assert plan is None  # ambiguous — let the LLM ask


def test_extract_runnable_paths_dedup_and_extensions() -> None:
    from gitpilot.agentic import _extract_runnable_paths_from_goal

    out = _extract_runnable_paths_from_goal("run demo.py and demo.py again")
    assert out == ["demo.py"]
    out = _extract_runnable_paths_from_goal("execute scripts/build.sh please")
    assert out == ["scripts/build.sh"]
    # Non-runnable extensions are not extracted.
    assert _extract_runnable_paths_from_goal("explain README.md") == []
