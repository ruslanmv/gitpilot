"""Tests for the surgical edit backend (Batch B8).

Pin every safety property the executor relies on:

* ``apply_edit`` refuses ambiguous matches by default (the contract
  that makes Claude Code's ``Edit`` tool reliable across models).
* Zero matches → clear error with a stripped-form hint when the only
  difference is indentation.
* Identical old/new → refused so the planner can't accidentally
  commit a no-op masquerading as a fix.
* Unified diffs apply by *context match*, so stale line numbers
  don't matter.
* Multi-hunk diffs track a running offset so the second hunk lands
  in the right place even after the first changed the file's length.
* Multi-file diffs (more than one ``diff --git`` header) are
  refused — single file at a time.
* Trailing newline state is preserved in both directions.
* Pathologically big inputs (2 000-line file, one-line edit) apply
  in well under a second.
"""
from __future__ import annotations

import time

import pytest

from gitpilot.edit_backend import (
    EditError,
    EditReport,
    apply_edit,
    apply_unified_diff,
)


# ----------------------------------------------------------------------
# apply_edit — happy paths
# ----------------------------------------------------------------------

def test_apply_edit_single_match() -> None:
    src = "alpha\nbeta\ngamma\n"
    new, rpt = apply_edit(src, old_string="beta", new_string="BETA")
    assert new == "alpha\nBETA\ngamma\n"
    assert rpt.occurrences_replaced == 1
    assert rpt.bytes_before == len(src)
    assert rpt.bytes_after == len(new)


def test_apply_edit_multiline_with_indentation() -> None:
    src = (
        "def foo():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    new, rpt = apply_edit(
        src,
        old_string="    x = 1\n    y = 2",
        new_string="    x = 10\n    y = 20",
    )
    assert "x = 10" in new and "y = 20" in new
    assert rpt.occurrences_replaced == 1


def test_apply_edit_deletes_with_empty_new_string() -> None:
    src = "keep\nremove\nkeep\n"
    new, rpt = apply_edit(src, old_string="remove\n", new_string="")
    assert new == "keep\nkeep\n"
    assert rpt.occurrences_replaced == 1


def test_apply_edit_expected_occurrences_n() -> None:
    src = "a\na\nb\n"
    new, rpt = apply_edit(src, old_string="a", new_string="X", expected_occurrences=2)
    assert new == "X\nX\nb\n"
    assert rpt.occurrences_replaced == 2


def test_apply_edit_expected_minus_one_replace_all() -> None:
    src = "x\nx\nx\nx\n"
    new, rpt = apply_edit(src, old_string="x", new_string="Y", expected_occurrences=-1)
    assert new == "Y\nY\nY\nY\n"
    assert rpt.occurrences_replaced == 4


# ----------------------------------------------------------------------
# apply_edit — refusal paths
# ----------------------------------------------------------------------

def test_apply_edit_refuses_zero_matches() -> None:
    with pytest.raises(EditError, match="not found"):
        apply_edit("a\nb\nc\n", old_string="DOESNOTEXIST", new_string="X")


def test_apply_edit_zero_match_hint_on_indentation_mismatch() -> None:
    """Most common cause of a missing match in Python: the agent
    copied the line WITH the wrong leading indentation.  The error
    must hint at that so the agent can recover.

    Here the file uses 8 spaces but the agent's edit uses 4 — the
    bare substring isn't in the file, but stripped(old) is.
    """
    # File uses spaces for indentation; agent's edit accidentally
    # used a tab — substring no longer matches, but stripped form does.
    src = "def foo():\n    return value\n"
    try:
        apply_edit(
            src,
            old_string="\treturn value",
            new_string="\treturn new_value",
        )
    except EditError as e:
        assert "indentation" in str(e).lower()
    else:
        pytest.fail("expected EditError")


def test_apply_edit_refuses_ambiguous_match() -> None:
    with pytest.raises(EditError, match="occurs 3 time"):
        apply_edit("a\na\na\nb\n", old_string="a", new_string="X")


def test_apply_edit_refuses_identical_old_new() -> None:
    with pytest.raises(EditError, match="identical"):
        apply_edit("x\n", old_string="x", new_string="x")


def test_apply_edit_refuses_empty_old_string() -> None:
    with pytest.raises(EditError, match="empty"):
        apply_edit("x\n", old_string="", new_string="y")


def test_apply_edit_unexpected_count_at_expected_2_but_3_present() -> None:
    with pytest.raises(EditError, match="occurs 3 time"):
        apply_edit("a\na\na\n", old_string="a", new_string="X", expected_occurrences=2)


# ----------------------------------------------------------------------
# apply_edit — performance on big files
# ----------------------------------------------------------------------

def test_apply_edit_on_2000_line_file_is_fast() -> None:
    big = "\n".join(f"line {i}" for i in range(1, 2001)) + "\n"
    t0 = time.perf_counter()
    new, rpt = apply_edit(big, old_string="line 1482", new_string="line FIXED")
    elapsed = time.perf_counter() - t0
    assert rpt.occurrences_replaced == 1
    assert "line FIXED" in new
    assert "line 1481" in new and "line 1483" in new
    # Must be near-instant; if this ever drifts to seconds the algo
    # has regressed.
    assert elapsed < 0.5


# ----------------------------------------------------------------------
# apply_unified_diff — happy paths
# ----------------------------------------------------------------------

def test_apply_unified_diff_single_hunk() -> None:
    content = "line A\nline B\nline C\nline D\n"
    diff = (
        "@@ -1,3 +1,3 @@\n"
        " line A\n"
        "-line B\n"
        "+line BB\n"
        " line C\n"
    )
    new, rpt = apply_unified_diff(content, diff)
    assert new == "line A\nline BB\nline C\nline D\n"
    assert rpt.occurrences_replaced == 1


def test_apply_unified_diff_multiple_hunks_with_offset_tracking() -> None:
    content = (
        "header\n"
        "alpha\n"
        "beta\n"
        "gamma\n"
        "delta\n"
        "epsilon\n"
        "footer\n"
    )
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " header\n"
        " alpha\n"
        "+inserted-1\n"
        " beta\n"
        "@@ -5,3 +6,3 @@\n"
        " delta\n"
        "-epsilon\n"
        "+EPSILON\n"
        " footer\n"
    )
    new, rpt = apply_unified_diff(content, diff)
    assert "inserted-1" in new
    assert "EPSILON" in new
    assert rpt.occurrences_replaced == 2


def test_apply_unified_diff_tolerates_stale_line_numbers() -> None:
    """The whole point of context-match: if some earlier edit moved
    the target lines, the hunk header's line numbers are wrong, but
    the context is still correct.  We match by context, not numbers."""
    content = "line A\nline B\nline C\n"
    diff = (
        "@@ -999,2 +999,2 @@\n"
        " line A\n"
        "-line B\n"
        "+line BB\n"
    )
    new, rpt = apply_unified_diff(content, diff)
    assert new == "line A\nline BB\nline C\n"


def test_apply_unified_diff_picks_nearest_match_when_ambiguous() -> None:
    """When the context appears twice, we land near the line number
    the hunk advertised."""
    content = (
        "X\n"   # 1
        "Y\n"   # 2
        "X\n"   # 3
        "Y\n"   # 4
        "X\n"   # 5
        "Y\n"   # 6
        "X\n"   # 7
    )
    diff = (
        "@@ -5,3 +5,3 @@\n"
        " X\n"
        "-Y\n"
        "+Z\n"
        " X\n"
    )
    new, _ = apply_unified_diff(content, diff)
    # The hunk targeted lines 5-7 ("X Y X" near line 5), so the
    # third occurrence wins.  Lines 5,6,7 become X,Z,X — earlier
    # occurrences are untouched.
    assert new.splitlines() == ["X", "Y", "X", "Y", "X", "Z", "X"]


def test_apply_unified_diff_with_file_headers_ignored() -> None:
    """Real-world diffs from git often come with --- / +++ headers
    before the @@ hunks; we must ignore the preamble."""
    content = "line A\nline B\n"
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " line A\n"
        "-line B\n"
        "+line BB\n"
    )
    new, _ = apply_unified_diff(content, diff)
    assert new == "line A\nline BB\n"


def test_apply_unified_diff_preserves_trailing_newline() -> None:
    """No-newline-at-EOF state must be preserved."""
    a = "x\ny\nz"   # no trailing newline
    diff = "@@ -1,3 +1,3 @@\n x\n-y\n+Y\n z\n"
    new, _ = apply_unified_diff(a, diff)
    assert not new.endswith("\n")


# ----------------------------------------------------------------------
# apply_unified_diff — refusal paths
# ----------------------------------------------------------------------

def test_apply_unified_diff_refuses_missing_context() -> None:
    content = "alpha\nbeta\n"
    diff = (
        "@@ -1,2 +1,2 @@\n"
        " WRONG_CONTEXT\n"
        "-beta\n"
        "+BETA\n"
    )
    with pytest.raises(EditError, match="locate the hunk"):
        apply_unified_diff(content, diff)


def test_apply_unified_diff_refuses_empty_diff() -> None:
    with pytest.raises(EditError, match="empty"):
        apply_unified_diff("x\n", "")


def test_apply_unified_diff_refuses_multi_file_patch() -> None:
    """Two ``diff --git`` headers → caller must split the patch."""
    diff = (
        "diff --git a/x b/x\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/y b/y\n"
        "@@ -1,1 +1,1 @@\n"
        "-a\n"
        "+b\n"
    )
    with pytest.raises(EditError, match="multi-file"):
        apply_unified_diff("x\n", diff)


def test_apply_unified_diff_refuses_malformed_hunk_line() -> None:
    diff = (
        "@@ -1,2 +1,2 @@\n"
        " alpha\n"
        "GARBAGE_LINE_NO_PREFIX\n"
        "-beta\n"
        "+BETA\n"
    )
    with pytest.raises(EditError, match="malformed hunk"):
        apply_unified_diff("alpha\nbeta\n", diff)


def test_apply_unified_diff_refuses_no_hunks() -> None:
    diff = "--- a/foo\n+++ b/foo\n"
    with pytest.raises(EditError, match="no @@ hunks"):
        apply_unified_diff("x\n", diff)


# ----------------------------------------------------------------------
# EditReport shape
# ----------------------------------------------------------------------

def test_edit_report_is_frozen_dataclass() -> None:
    rpt = EditReport(occurrences_replaced=1, bytes_before=10, bytes_after=12)
    with pytest.raises(Exception):
        rpt.occurrences_replaced = 99  # type: ignore[misc]
