"""Tests for the post-hoc planner guards (refusal + plausibility)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pytest

from gitpilot.plan_guards import (
    PlanHallucinationError,
    assess_plan,
    detect_refusal,
    ensure_plan_grounded,
)


# ----------------------------------------------------------------------
# Refusal detection
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "I cannot provide information or guidance on illegal or harmful activities.",
        "i can't assist with that",
        "As an AI language model, I cannot help.",
        "I am unable to help with this request.",
        "This goes against my guidelines.",
        # Tool-loop hallucinations (Nuclear-Physics session 83c77335…):
        "I cannot continue with this task because you didn't provide the "
        "output of 'Get repository summary' and 'List all files in repository'. "
        "Please provide them before proceeding.",
        "I cannot proceed with this task without the repository summary "
        "and list of files. Please provide them before I can create a plan.",
        "I cannot create a plan without more information.",
    ],
)
def test_detect_refusal_known_phrases(text: str) -> None:
    assert detect_refusal(text) is not None


def test_detect_refusal_exact_production_payload_from_explorer() -> None:
    """The exact final-answer string the Repository Explorer produced
    in the failing Nuclear-Physics session.  Without detection this
    falls through to the planner, which then refuses too, and the
    frontend renders a half-state (Approve buttons but no Action Plan).
    """
    payload = (
        "I cannot continue with this task because you didn't provide the output of "
        "\"Get repository summary\" and \"List all files in repository\". Please "
        "provide them before proceeding."
    )
    assert detect_refusal(payload) is not None


def test_detect_refusal_exact_production_payload_from_planner() -> None:
    payload = (
        "I cannot proceed with this task without the repository summary and list "
        "of files. Please provide them before I can create a plan."
    )
    assert detect_refusal(payload) is not None


def test_detect_refusal_returns_none_for_benign_text() -> None:
    assert detect_refusal("Plan created with 3 steps.") is None
    assert detect_refusal("") is None
    assert detect_refusal(None) is None


def test_detect_refusal_handles_crewai_like_output() -> None:
    @dataclass
    class FakeTaskOutput:
        raw: str = ""

    @dataclass
    class FakeCrewOutput:
        raw: str = ""
        tasks_output: List[FakeTaskOutput] = field(default_factory=list)

    out = FakeCrewOutput(
        raw="",
        tasks_output=[
            FakeTaskOutput(raw="exploration complete"),
            FakeTaskOutput(raw="I cannot provide information or guidance on illegal..."),
        ],
    )
    assert detect_refusal(out) is not None


# ----------------------------------------------------------------------
# Plan assessment
# ----------------------------------------------------------------------

@dataclass
class _FakePlanFile:
    path: str
    action: str


@dataclass
class _FakePlanStep:
    files: List[_FakePlanFile] = field(default_factory=list)


@dataclass
class _FakePlan:
    steps: List[_FakePlanStep] = field(default_factory=list)


def test_assess_plan_recognises_real_paths() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[
            _FakePlanFile("README.md", "MODIFY"),
            _FakePlanFile("src/main.py", "MODIFY"),
        ]),
    ])
    result = assess_plan(plan, ["README.md", "src/main.py", "tests/test_x.py"])
    assert result.hits_in_repo == 2
    assert result.misses_in_repo == 0
    assert result.hit_ratio == 1.0
    assert result.hallucinated is False


def test_assess_plan_flags_pure_create_as_not_hallucinated() -> None:
    """Creating new demo files is legitimate even if they're not in the repo."""
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[
            _FakePlanFile("demo.py", "CREATE"),
            _FakePlanFile("examples/sample.py", "CREATE"),
        ]),
    ])
    result = assess_plan(plan, ["README.md"])
    assert result.hits_in_repo == 0
    assert result.misses_in_repo == 0  # CREATE paths don't count as misses
    assert result.hallucinated is False  # no suspicious tokens


def test_assess_plan_detects_classic_hallucination() -> None:
    """The exact failure mode from the trace: stock 'process' plan applied
    to a repo that only contains README.md."""
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[
            _FakePlanFile("/process/documents/new-process-document.pdf", "CREATE"),
            _FakePlanFile("/process/manual/handbook.pdf", "CREATE"),
        ]),
    ])
    result = assess_plan(plan, ["README.md"])
    assert len(result.suspicious_paths) >= 2
    assert result.hallucinated is True


def test_assess_plan_treats_modify_misses_as_strong_signal() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[
            _FakePlanFile("/process/documents/sla.pdf", "MODIFY"),
        ]),
    ])
    result = assess_plan(plan, ["README.md"])
    assert result.misses_in_repo == 1
    assert result.hits_in_repo == 0
    assert result.hit_ratio == 0.0
    assert result.hallucinated is True


def test_assess_plan_handles_path_with_leading_dot_slash() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[_FakePlanFile("./README.md", "MODIFY")]),
    ])
    result = assess_plan(plan, ["README.md"])
    assert result.hits_in_repo == 1


def test_assess_plan_tolerates_missing_steps_field() -> None:
    @dataclass
    class _Bare:
        pass

    result = assess_plan(_Bare(), ["README.md"])
    assert result.total_files == 0
    assert result.hallucinated is False


# ----------------------------------------------------------------------
# Convenience raise-helper
# ----------------------------------------------------------------------

def test_ensure_plan_grounded_passes_for_real_plan() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[_FakePlanFile("README.md", "MODIFY")]),
    ])
    ensure_plan_grounded(plan, ["README.md"])  # must not raise


def test_ensure_plan_grounded_raises_for_hallucinated_plan() -> None:
    plan = _FakePlan(steps=[
        _FakePlanStep(files=[
            _FakePlanFile("/process/documents/new-process-document.pdf", "CREATE"),
        ]),
    ])
    with pytest.raises(PlanHallucinationError) as exc:
        ensure_plan_grounded(plan, ["README.md"])
    assert exc.value.assessment.hallucinated is True
    assert "process" in str(exc.value).lower()
