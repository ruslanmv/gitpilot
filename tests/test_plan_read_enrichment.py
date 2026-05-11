"""Tests for the auto-injection of READ entries into plan steps.

The Nuclear-Physics trace (Ollama llama3:8b, session 62668be7…)
produced a plan whose step description said *"Read the content of
README.md and create a new file …"* but whose ``files[]`` array only
contained the CREATE entry — the READ was dropped.  This was the
exact contract violation Step 1 of the v2 feasibility plan called out.

``enrich_plan_with_reads`` is a post-hoc guard that scans each step's
title + description for file references that exist in the real repo
but aren't yet listed in ``files[]``, and adds them with
``action="READ"``.  Tests below pin every variant we expect to see in
production:

* quoted refs (`` `README.md` ``, ``"README.md"``, ``'README.md'``)
* bareword refs (``README.md``)
* paths with subdirectories (``src/main.py``)
* the EXACT production payload from the failing trace
* no-op when the file is already listed
* no-op when the referenced file does not exist in the repo (so the
  guard never invents files)
* multi-step plans are processed independently
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from gitpilot.plan_guards import enrich_plan_with_reads


# ----------------------------------------------------------------------
# Lightweight plan stand-ins (no Pydantic dependency in tests)
# ----------------------------------------------------------------------

@dataclass
class _FakePlanFile:
    path: str
    action: str


@dataclass
class _FakePlanStep:
    title: str = ""
    description: str = ""
    files: List[_FakePlanFile] = field(default_factory=list)


@dataclass
class _FakePlan:
    steps: List[_FakePlanStep] = field(default_factory=list)


# ----------------------------------------------------------------------
# Production-payload regression
# ----------------------------------------------------------------------

def test_exact_production_payload_from_nuclear_physics_trace() -> None:
    """The plan llama3:8b produced for session 62668be7 — missing the
    README.md READ entry the description clearly implies."""
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="Analyze README.md and generate Python code",
            description=(
                "Read the content of README.md and create a new file "
                "with generated Python code"
            ),
            files=[_FakePlanFile("README_code.py", "CREATE")],
        ),
    ])
    added = enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert added == 1
    paths_with_actions = {(f.path, f.action) for f in plan.steps[0].files}
    assert ("README.md", "READ") in paths_with_actions
    assert ("README_code.py", "CREATE") in paths_with_actions


# ----------------------------------------------------------------------
# Reference-syntax coverage
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "description",
    [
        "Read the content of README.md",
        "Read the content of `README.md`",
        "Read the content of \"README.md\"",
        "Read the content of 'README.md'",
    ],
)
def test_quoted_and_bare_references_are_both_detected(description: str) -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step",
            description=description,
            files=[_FakePlanFile("out.py", "CREATE")],
        ),
    ])
    enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert any(f.path == "README.md" and f.action == "READ" for f in plan.steps[0].files)


def test_subdirectory_path_in_description_is_handled() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="Mirror src/main.py",
            description="Read src/main.py and write src/main_copy.py",
            files=[_FakePlanFile("src/main_copy.py", "CREATE")],
        ),
    ])
    enrich_plan_with_reads(plan, repo_files=["src/main.py", "README.md"])
    assert any(f.path == "src/main.py" and f.action == "READ" for f in plan.steps[0].files)


# ----------------------------------------------------------------------
# No-op safeguards
# ----------------------------------------------------------------------

def test_no_op_when_read_entry_already_present() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step",
            description="Read README.md and create demo.py",
            files=[
                _FakePlanFile("README.md", "READ"),
                _FakePlanFile("demo.py", "CREATE"),
            ],
        ),
    ])
    added = enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert added == 0
    assert len(plan.steps[0].files) == 2  # untouched


def test_does_not_invent_files_not_in_repo() -> None:
    """The description mentions a file that doesn't exist — the guard
    must NOT inject it (this is the same safety property assess_plan
    relies on to detect hallucinated stock plans)."""
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step",
            description="Read config.yaml and create demo.py",
            files=[_FakePlanFile("demo.py", "CREATE")],
        ),
    ])
    added = enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert added == 0
    assert not any(f.path == "config.yaml" for f in plan.steps[0].files)


def test_empty_repo_file_list_is_a_no_op() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step",
            description="Read README.md and create demo.py",
            files=[_FakePlanFile("demo.py", "CREATE")],
        ),
    ])
    assert enrich_plan_with_reads(plan, repo_files=[]) == 0


def test_empty_description_is_a_no_op() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="",
            description="",
            files=[_FakePlanFile("demo.py", "CREATE")],
        ),
    ])
    assert enrich_plan_with_reads(plan, repo_files=["README.md"]) == 0


def _path_of(file_entry):
    """Tolerant accessor: works on both dataclass / Pydantic objects
    (attribute access) and on the plain-dict fallback the helper uses
    when a step starts with an empty files list."""
    if isinstance(file_entry, dict):
        return file_entry.get("path"), file_entry.get("action")
    return getattr(file_entry, "path", None), getattr(file_entry, "action", None)


def test_multi_step_plan_processed_independently() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step 1",
            description="Read README.md to understand the project",
            files=[],
        ),
        _FakePlanStep(
            title="step 2",
            description="Generate demo.py based on the README content",
            files=[_FakePlanFile("demo.py", "CREATE")],
        ),
    ])
    added = enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert added == 1  # only step 1 mentions a real existing file
    step1_pairs = {_path_of(f) for f in plan.steps[0].files}
    assert ("README.md", "READ") in step1_pairs
    # Step 2's files are untouched (demo.py is not yet in repo, so not injected).
    assert plan.steps[1].files == [_FakePlanFile("demo.py", "CREATE")]


def test_title_can_also_carry_the_reference() -> None:
    """Some planners stuff the file mention into the step title."""
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="Analyse README.md",
            description="Build a high-level summary.",
            files=[_FakePlanFile("summary.md", "CREATE")],
        ),
    ])
    enrich_plan_with_reads(plan, repo_files=["README.md"])
    assert any(f.path == "README.md" and f.action == "READ" for f in plan.steps[0].files)


def test_canonical_path_from_repo_files_is_used() -> None:
    """If the description writes 'readme.md' (lowercase) but the actual
    file is 'README.md', we should use the canonical form."""
    plan = _FakePlan(steps=[
        _FakePlanStep(
            title="step",
            description="Read readme.md and create demo.py",
            files=[_FakePlanFile("demo.py", "CREATE")],
        ),
    ])
    enrich_plan_with_reads(plan, repo_files=["README.md"])
    paths = [f.path for f in plan.steps[0].files]
    assert "README.md" in paths
