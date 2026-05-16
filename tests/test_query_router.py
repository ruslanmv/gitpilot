"""Tests for the deterministic query router (Batch B9).

The router is the auto-strategy layer that picks tools / triggers
RAG before the LLM even runs.  Tests pin:

* Intent classification on representative dev prompts (fix / find /
  info / create / delete / modify / unknown).
* Target-file extraction: quoted, bareword, path-with-slash, with
  / without repo verification.
* Fuzzy-query detection (natural language vs. symbol-shaped).
* RAG / auto-index decisions: only fired for read-leaning intents
  on big enough repos.
* File-type policy: ``.py`` ⇒ surgical, ``.lock`` ⇒ reject.
* ``force_no_rag`` overrides the RAG recommendation.
* Hint rendering survives every combination without crashing.
"""
from __future__ import annotations

import pytest

from gitpilot.query_router import (
    DEFAULT_FUZZY_REPO_SIZE_FOR_RAG,
    RouterDecision,
    classify,
    render_planner_hint,
)


# A medium-size synthetic repo (60 files) — above the
# RAG-recommendation threshold.
REPO = (
    ["README.md", "pyproject.toml", "src/main.py", "src/auth.py",
     "src/util.py", "tests/test_main.py", "Dockerfile",
     "poetry.lock", "package-lock.json"]
    + [f"src/mod_{i:02d}.py" for i in range(60)]
)


# ----------------------------------------------------------------------
# Intent classification
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "goal, expected",
    [
        ("Fix the TypeError in src/auth.py", "fix"),
        ("fix bug in main()", "fix"),
        ("the app crashes on startup", "fix"),
        ("a regression happened after the last commit", "fix"),

        ("find calls to authenticate_user", "find"),
        ("where do we handle login?", "find"),
        ("search for the cache layer", "find"),

        ("what is this project about", "info"),
        ("explain how does the planner work", "info"),
        ("describe the agent pipeline", "info"),

        ("create a new helper for date formatting", "create"),
        ("add a CLI command for export", "create"),
        ("generate a Dockerfile for production", "create"),

        ("delete all the .lock files", "delete"),
        ("remove the unused vendor folder", "delete"),

        ("modify pyproject.toml dependencies", "modify"),
        ("update README.md installation steps", "modify"),
        ("rename foo_bar to baz_qux", "modify"),
        ("refactor the auth module", "modify"),

        ("plonk into snargs", "unknown"),     # nonsense
        ("", "unknown"),                       # empty
    ],
)
def test_intent_classification(goal: str, expected: str) -> None:
    d = classify(goal, repo_files=REPO)
    assert d.intent == expected, f"goal={goal!r}"


# ----------------------------------------------------------------------
# Target-file extraction
# ----------------------------------------------------------------------

def test_extracts_path_with_slash() -> None:
    d = classify("Fix bug in src/auth.py", repo_files=REPO)
    assert d.target_files == ["src/auth.py"]


def test_extracts_bareword_file_with_extension() -> None:
    d = classify("Modify pyproject.toml", repo_files=REPO)
    assert "pyproject.toml" in d.target_files


def test_extracts_quoted_path() -> None:
    d = classify("Fix bug in `src/util.py`", repo_files=REPO)
    assert "src/util.py" in d.target_files


def test_does_not_invent_files_absent_from_repo() -> None:
    """A user typo like ``src/aut.py`` must not survive verification."""
    d = classify("Fix bug in src/aut.py", repo_files=REPO)
    assert d.target_files == []


def test_no_repo_files_keeps_raw_candidates() -> None:
    """When the caller can't supply the repo file list (offline / first
    request) we surface the raw candidates so the planner can still
    use them as hints."""
    d = classify("Fix bug in src/auth.py", repo_files=None)
    assert "src/auth.py" in d.target_files


# ----------------------------------------------------------------------
# Fuzzy detection + RAG recommendation
# ----------------------------------------------------------------------

def test_fuzzy_query_with_big_repo_and_no_index_triggers_auto_index() -> None:
    d = classify(
        "where do we handle session tokens and auth refresh",
        repo_files=REPO,
        rag_index_exists=False,
    )
    assert d.intent == "find"
    assert d.rag_recommended is True
    assert d.auto_index_repo is True


def test_fuzzy_query_with_existing_index_recommends_rag_but_not_rebuild() -> None:
    d = classify(
        "where do we handle session tokens and auth refresh",
        repo_files=REPO,
        rag_index_exists=True,
    )
    assert d.rag_recommended is True
    assert d.auto_index_repo is False


def test_force_no_rag_suppresses_rag_recommendation() -> None:
    """The post-Reject retry path sets force_no_rag=True; router
    must respect it."""
    d = classify(
        "where do we handle session tokens",
        repo_files=REPO,
        force_no_rag=True,
    )
    assert d.rag_recommended is False
    assert d.auto_index_repo is False


def test_symbol_query_uses_grep_not_rag() -> None:
    """A query that names a real symbol (camelCase / snake_case /
    SCREAMING) is exact-search territory, not embeddings."""
    d = classify("find calls to authenticate_user", repo_files=REPO)
    assert d.rag_recommended is False
    assert "Search file contents" in d.tool_priority


def test_small_repo_skips_auto_index() -> None:
    small = ["README.md", "main.py"]
    d = classify(
        "where do we handle authentication tokens",
        repo_files=small,
    )
    assert d.repo_too_small_for_rag is True
    assert d.auto_index_repo is False


def test_info_intent_does_not_auto_index() -> None:
    """'What is this project' is answered from the repo map; no need
    to spin up the embedding pipeline."""
    d = classify("what does this project do", repo_files=REPO)
    assert d.intent == "info"
    assert d.auto_index_repo is False


def test_delete_intent_does_not_auto_index() -> None:
    d = classify("delete all the unused tests", repo_files=REPO)
    assert d.intent == "delete"
    assert d.auto_index_repo is False


# ----------------------------------------------------------------------
# File-type policy
# ----------------------------------------------------------------------

def test_python_target_marks_surgical_with_indentation_hint() -> None:
    d = classify("Fix bug in src/auth.py", repo_files=REPO)
    assert d.edit_strategy == "surgical"
    assert "indentation-sensitive" in d.file_policy_notes


def test_lockfile_target_marks_reject() -> None:
    """Generated lock files are off-limits — edit the manifest."""
    d = classify("Modify poetry.lock", repo_files=REPO)
    assert d.edit_strategy == "reject"
    assert "lock" in d.file_policy_notes.lower()


def test_markdown_target_marks_surgical() -> None:
    d = classify("Update README.md installation steps", repo_files=REPO)
    assert d.edit_strategy == "surgical"


# ----------------------------------------------------------------------
# Tool priority
# ----------------------------------------------------------------------

def test_fix_with_target_recommends_read_then_edit() -> None:
    d = classify("Fix bug in src/auth.py", repo_files=REPO)
    assert d.tool_priority[:2] == [
        "Read file content",
        "Edit a section of a file",
    ]


def test_fix_without_target_recommends_grep_first() -> None:
    """No specific file mentioned and not fuzzy → grep first."""
    d = classify("fix authenticate_user bug", repo_files=REPO)
    assert d.tool_priority[0] in (
        "Search file contents", "Find code by semantic search",
    )


def test_fuzzy_fix_without_target_prefers_semantic_search() -> None:
    d = classify(
        "fix the issue where login tokens expire too quickly",
        repo_files=REPO,
    )
    # Fuzzy + no target → RAG first.
    assert d.tool_priority[0] == "Find code by semantic search"


def test_delete_intent_uses_glob_first() -> None:
    d = classify("delete all the unused tests", repo_files=REPO)
    assert d.tool_priority[0] == "Find files matching a pattern"


def test_info_intent_recommends_read_only() -> None:
    d = classify("what does this project do", repo_files=REPO)
    # No write tools — info queries are read-only.
    write_tools = {"Edit a section of a file", "Write or update a file in the repository"}
    assert not (set(d.tool_priority) & write_tools)


# ----------------------------------------------------------------------
# Hint rendering
# ----------------------------------------------------------------------

def test_render_planner_hint_contains_intent_and_files() -> None:
    d = classify("Fix bug in src/auth.py", repo_files=REPO)
    hint = render_planner_hint(d)
    assert "Intent" in hint and "fix" in hint
    assert "src/auth.py" in hint


def test_render_planner_hint_mentions_index_step_when_auto_index() -> None:
    d = classify(
        "where do we handle session tokens",
        repo_files=REPO,
        rag_index_exists=False,
    )
    hint = render_planner_hint(d)
    assert "INDEX" in hint  # the planner must know to include the step
    assert "one-time" in hint


def test_render_planner_hint_skips_index_step_when_consent_exists() -> None:
    d = classify(
        "where do we handle session tokens",
        repo_files=REPO,
        rag_index_exists=True,
    )
    hint = render_planner_hint(d)
    assert "INDEX" not in hint


def test_router_decision_is_frozen() -> None:
    d = classify("info", repo_files=REPO)
    with pytest.raises(Exception):
        d.intent = "fix"  # type: ignore[misc]


# ----------------------------------------------------------------------
# DEFAULT_FUZZY_REPO_SIZE_FOR_RAG sanity
# ----------------------------------------------------------------------

def test_threshold_is_sensible() -> None:
    """The RAG threshold must be high enough to skip toy repos and
    low enough that mid-size projects benefit."""
    assert 10 <= DEFAULT_FUZZY_REPO_SIZE_FOR_RAG <= 200
