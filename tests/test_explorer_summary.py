"""Tests for the explorer-report compressor (Batch B5).

Pin the contract the planner relies on:

* Header is preserved so the planner's existing prompt template
  matches.
* Every file path the explorer listed survives compression OR is
  replaced by an honest "…N more files" marker.
* Compression is a strict no-op when:
    - the flag is off
    - the report is already under budget
    - the report is empty
* On a pathologically large input, the compressed output is always
  ≤ the budget plus a small slack — never an order of magnitude over.
"""
from __future__ import annotations

import pytest

from gitpilot import flags
from gitpilot.context_budget import estimate_tokens
from gitpilot.explorer_summary import (
    DEFAULT_TOKEN_BUDGET,
    FLAG_SUBAGENT_EXPLORER,
    MAX_FILES_LISTED,
    compress_exploration_report,
)


SHORT_REPORT = """\
REPOSITORY EXPLORATION REPORT
=============================

Files Found:
  - README.md
  - src/main.py
  - tests/test_main.py

Key Files:
  - README.md

Directory Structure:
README.md
src/main.py
tests/test_main.py

File Types: md=1, py=2
"""


def _make_large_report(n_files: int) -> str:
    """Synthesise an explorer report listing N files under src/."""
    files = [f"  - src/mod_{i:04d}/file_{j}.py" for i in range(n_files // 5) for j in range(5)]
    return (
        "REPOSITORY EXPLORATION REPORT\n"
        "=============================\n"
        "\n"
        "Files Found:\n"
        + "\n".join(files)
        + "\n\nKey Files:\n  - README.md\n  - src/__init__.py\n"
    )


# ----------------------------------------------------------------------
# No-op paths
# ----------------------------------------------------------------------

def test_short_report_passes_through_unchanged() -> None:
    out, metrics = compress_exploration_report(SHORT_REPORT)
    assert out == SHORT_REPORT
    assert metrics.reason == "under budget"
    assert metrics.compressed_tokens == metrics.original_tokens


def test_empty_report_is_returned_as_is() -> None:
    out, metrics = compress_exploration_report("")
    assert out == ""
    assert metrics.reason == "empty report"


def test_flag_off_is_a_noop() -> None:
    big = _make_large_report(500)
    flags.set_override(FLAG_SUBAGENT_EXPLORER, False)
    try:
        out, metrics = compress_exploration_report(big)
    finally:
        flags.clear_override(FLAG_SUBAGENT_EXPLORER)
    assert out == big
    assert metrics.reason == "flag off"


# ----------------------------------------------------------------------
# Actual compression
# ----------------------------------------------------------------------

def test_large_report_shrinks_under_budget() -> None:
    big = _make_large_report(500)
    out, metrics = compress_exploration_report(big, token_budget=800)
    # Token count must drop, hard.
    assert metrics.original_tokens > metrics.compressed_tokens
    # Budget honoured (with a tiny safety slack — the loop trims more
    # if we overshoot, so the final result is at most the budget).
    assert metrics.compressed_tokens <= 900


def test_compressed_report_preserves_header_for_planner() -> None:
    """The planner template injects the report under a fixed header.
    Compression must keep that header so the template still works."""
    big = _make_large_report(500)
    out, _ = compress_exploration_report(big, token_budget=800)
    assert "REPOSITORY EXPLORATION REPORT" in out
    assert "Files Found:" in out


def test_compression_caps_files_listed() -> None:
    big = _make_large_report(500)   # 500 files
    out, metrics = compress_exploration_report(big, token_budget=800)
    # Should never list more than the documented hard cap under the
    # "Files Found:" section (which is what the planner counts).
    # Filter to lines that look like the synthetic ``mod_XXXX/file_X.py``
    # entries so the Key Files entry (``src/__init__.py``) doesn't
    # accidentally inflate the count.
    listed = [ln for ln in out.splitlines() if "mod_" in ln]
    assert len(listed) <= MAX_FILES_LISTED
    assert metrics.truncated is True
    assert metrics.files_in_original == 500
    # Honest "N more files" marker present.
    assert "more files" in out


def test_compression_keeps_first_files() -> None:
    """Order should be preserved when we trim — first N files survive
    so the planner sees the "earliest discovered" ones (typically the
    most important: top-level, then breadth-first by directory)."""
    big = _make_large_report(500)
    out, _ = compress_exploration_report(big, token_budget=800)
    # First file in the synthetic list is src/mod_0000/file_0.py
    assert "src/mod_0000/file_0.py" in out


def test_compression_preserves_key_files_section() -> None:
    """Key Files is the planner's anchor — it must survive even under
    aggressive compression."""
    big = _make_large_report(500)
    out, _ = compress_exploration_report(big, token_budget=400)
    assert "Key Files:" in out


def test_pathological_input_does_not_explode() -> None:
    """A 10 000-file report should still produce a bounded output.
    No quadratic blow-ups, no recursion errors."""
    big = _make_large_report(10_000)
    out, metrics = compress_exploration_report(big, token_budget=800)
    assert metrics.compressed_tokens <= 1_200   # generous slack
    assert "more files" in out


def test_default_budget_matches_documented_value() -> None:
    assert DEFAULT_TOKEN_BUDGET == 800
