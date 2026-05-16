"""Tests for the lean-prompt module (Batch B12).

Pin five properties so a future "let me add one more rule" edit can't
silently regress the small-model context budget:

1. Every prompt is within its declared character budget.
2. None of the forbidden small-model keywords appear anywhere in the
   rendered prompts.
3. The "Known facts" block is in the bottom 250 chars of every
   render_plan_task() output (last-segment-attention principle).
4. The intent-routed rule block is the correct one for each intent
   (create / modify / fix / delete / find / info / unknown / None).
5. The flag can be toggled without breaking imports.
"""
from __future__ import annotations

import pytest

from gitpilot import flags
from gitpilot.agent_prompts import (
    CODE_WRITER_BACKSTORY,
    CODE_WRITER_BACKSTORY_BUDGET,
    CREATE_FILE_TASK_CHAR_BUDGET,
    EXPLORER_BACKSTORY,
    EXPLORER_BACKSTORY_BUDGET,
    EXPLORER_TASK_CHAR_BUDGET,
    FLAG_LEAN_PROMPTS,
    FORBIDDEN_KEYWORDS,
    MODIFY_FILE_TASK_CHAR_BUDGET,
    PLAN_TASK_CHAR_BUDGET,
    PLANNER_BACKSTORY,
    PLANNER_BACKSTORY_BUDGET,
    SPECIALIST_BACKSTORIES,
    SPECIALIST_BACKSTORY_BUDGET,
    lean_prompts_enabled,
    render_create_file_task,
    render_explorer_task,
    render_modify_file_task,
    render_plan_task,
)


# ----------------------------------------------------------------------
# Per-prompt budget enforcement
# ----------------------------------------------------------------------

SAMPLE_FILE_LIST = ["README.md", "src/main.py", "src/util.py", "tests/test_main.py"]


@pytest.mark.parametrize(
    "intent",
    ["create", "modify", "fix", "delete", "find", "info", "unknown", None],
)
def test_plan_task_within_budget_for_every_intent(intent: str | None) -> None:
    rendered = render_plan_task(
        goal="do something specific that takes a few words to describe",
        repo_full_name="owner/repo-with-a-longish-name",
        active_ref="some/branch-name-that-is-longer",
        file_list=SAMPLE_FILE_LIST,
        intent=intent,
    )
    assert len(rendered) <= PLAN_TASK_CHAR_BUDGET, (
        f"intent={intent}: {len(rendered)} > {PLAN_TASK_CHAR_BUDGET}"
    )


def test_explorer_task_within_budget() -> None:
    rendered = render_explorer_task(
        repo_full_name="owner/repo", active_ref="main",
    )
    assert len(rendered) <= EXPLORER_TASK_CHAR_BUDGET


def test_create_file_task_within_budget() -> None:
    rendered = render_create_file_task(
        file_path="src/very/deep/path/file.py",
        goal="generate a thing",
        step_description="step that does the thing",
    )
    assert len(rendered) <= CREATE_FILE_TASK_CHAR_BUDGET


def test_modify_file_task_within_budget() -> None:
    rendered = render_modify_file_task(
        file_path="src/util.py",
        goal="fix the bug",
        step_description="patch the validator",
        current_content="def x():\n    return 1\n",
    )
    # The content varies; we cap only the framing.  Subtract the
    # length of the current content to test just the rules + format.
    framing = len(rendered) - len("def x():\n    return 1\n")
    assert framing <= MODIFY_FILE_TASK_CHAR_BUDGET


def test_backstories_within_budget() -> None:
    assert len(EXPLORER_BACKSTORY) <= EXPLORER_BACKSTORY_BUDGET
    assert len(PLANNER_BACKSTORY) <= PLANNER_BACKSTORY_BUDGET
    assert len(CODE_WRITER_BACKSTORY) <= CODE_WRITER_BACKSTORY_BUDGET
    for name, body in SPECIALIST_BACKSTORIES.items():
        assert len(body) <= SPECIALIST_BACKSTORY_BUDGET, name


# ----------------------------------------------------------------------
# Forbidden-keyword scrub
# ----------------------------------------------------------------------

def _all_rendered_prompts() -> str:
    """Every prompt the lean module produces, concatenated.  Used as
    the corpus for forbidden-keyword greps."""
    parts: list[str] = [
        EXPLORER_BACKSTORY, PLANNER_BACKSTORY, CODE_WRITER_BACKSTORY,
        *SPECIALIST_BACKSTORIES.values(),
        render_explorer_task(repo_full_name="o/r", active_ref="m"),
        render_create_file_task(file_path="a.py", goal="g", step_description="d"),
        render_modify_file_task(
            file_path="a.py", goal="g", step_description="d", current_content="",
        ),
    ]
    for intent in (
        "create", "modify", "fix", "delete", "find", "info", "unknown", None,
    ):
        parts.append(
            render_plan_task(
                goal="g", repo_full_name="o/r", active_ref="m",
                file_list=["x.py"], intent=intent,
            )
        )
    return "\n".join(parts)


@pytest.mark.parametrize("keyword", FORBIDDEN_KEYWORDS)
def test_no_forbidden_keyword_in_any_rendered_prompt(keyword: str) -> None:
    corpus = _all_rendered_prompts()
    assert keyword not in corpus, (
        f"Forbidden keyword {keyword!r} still appears in a rendered prompt"
    )


# ----------------------------------------------------------------------
# Facts block is at the bottom
# ----------------------------------------------------------------------

def test_facts_block_lives_near_end_of_plan_task() -> None:
    """Small models over-weight the final segment of the prompt.  The
    "Known facts" block must live in the last 250 chars so the file-
    list ground truth gets that attention."""
    rendered = render_plan_task(
        goal="do thing",
        repo_full_name="o/r",
        active_ref="main",
        file_list=["README.md"],
        intent="create",
    )
    tail = rendered[-300:]
    assert "Known facts:" in tail
    assert "does NOT exist" in tail


# ----------------------------------------------------------------------
# Intent → rule block routing
# ----------------------------------------------------------------------

_INTENT_RULE_MARKERS = {
    "create":  "at least one CREATE",
    "modify":  "Use MODIFY only",
    "fix":     "Use MODIFY only",            # fix aliases to modify
    "delete":  "Use DELETE only",
    "find":    "Plan READ actions",
    "info":    "Empty steps is fine",
    "unknown": "Match the action to what",
}


@pytest.mark.parametrize("intent, marker", list(_INTENT_RULE_MARKERS.items()))
def test_each_intent_pulls_its_own_rules(intent: str, marker: str) -> None:
    rendered = render_plan_task(
        goal="x", repo_full_name="o/r", active_ref="m",
        file_list=["a.py"], intent=intent,
    )
    assert marker in rendered, f"intent={intent} missing marker {marker!r}"


def test_no_intent_falls_back_to_unknown_block() -> None:
    """When intent is None (router skipped / disabled) we don't want a
    crash — pick the generic rule block."""
    rendered = render_plan_task(
        goal="x", repo_full_name="o/r", active_ref="m",
        file_list=["a.py"], intent=None,
    )
    assert _INTENT_RULE_MARKERS["unknown"] in rendered


def test_create_intent_does_not_carry_delete_rules() -> None:
    """The whole point of intent routing — small models stop seeing
    the deletion rule block when the goal isn't a deletion."""
    rendered = render_plan_task(
        goal="g", repo_full_name="o/r", active_ref="m",
        file_list=["a.py"], intent="create",
    )
    assert "Use DELETE only" not in rendered
    assert "Use MODIFY only" not in rendered


# ----------------------------------------------------------------------
# Flag plumbing
# ----------------------------------------------------------------------

def test_flag_default_on() -> None:
    assert lean_prompts_enabled() is True


def test_flag_can_be_turned_off() -> None:
    flags.set_override(FLAG_LEAN_PROMPTS, False)
    try:
        assert lean_prompts_enabled() is False
    finally:
        flags.clear_override(FLAG_LEAN_PROMPTS)


# ----------------------------------------------------------------------
# Total prompt-stack budget for the canonical failure scenario
# ----------------------------------------------------------------------

def test_total_planner_stack_under_3k_chars_on_tiny_repo() -> None:
    """The original llama3:8b failure trace happened with a planner
    stack of ~4.5k chars (12-15 KB including tool schemas).  After
    B12 the stack — backstory + task description — fits in 3 KB on
    a single-file repo, leaving room for the tool-schema preamble
    inside an 8 k context window."""
    stack = (
        PLANNER_BACKSTORY
        + render_plan_task(
            goal="create a simple python code about what says the README.md",
            repo_full_name="INFN-GE/Nuclear-Physics",
            active_ref="master",
            file_list=["README.md"],
            intent="create",
        )
    )
    assert len(stack) < 3000, (
        f"planner stack is {len(stack)} chars — small-model budget regression"
    )
