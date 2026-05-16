"""Surgical edit operations for the executor (Batch B8).

Pure text-in / text-out functions — no GitHub / disk I/O.  The agent
tool wrappers in :mod:`gitpilot.agent_tools` are responsible for
fetching the current file bytes (GitHub mode) or reading from disk
(local mode), passing them through these helpers, and then writing
the result back.

Two operations:

* :func:`apply_edit` — exact-string find-and-replace with
  *strict occurrence validation*.  The model passes a small
  ``old_string`` and a small ``new_string``; we refuse to apply
  unless ``old_string`` occurs exactly the expected number of times.
  Inspired by Claude Code's ``Edit`` tool — the contract that makes
  fixing line 1 482 of a 2 000-line file reliable across any model.

* :func:`apply_unified_diff` — parse a minimal subset of unified
  diff and apply it by *matching the leading context lines* rather
  than trusting the line numbers in the hunk header.  Line numbers
  drift the moment another edit lands; context survives.  This is
  the same trick Codex's ``apply_patch`` uses internally.

Both functions raise :class:`EditError` with a precise, actionable
message rather than silently mis-editing.  The executor must surface
that error to the user and refuse to commit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


class EditError(ValueError):
    """Raised when an edit cannot be applied safely.

    The message is user-facing — keep it concrete: file path, what
    failed, what the caller can do about it.  Never log a stack trace
    in place of a clear sentence.
    """


@dataclass(frozen=True)
class EditReport:
    """Returned alongside the new content so callers can record a
    Task row with concrete numbers."""
    occurrences_replaced: int
    bytes_before: int
    bytes_after: int


# ----------------------------------------------------------------------
# apply_edit — exact-string find-and-replace
# ----------------------------------------------------------------------

def apply_edit(
    content: str,
    *,
    old_string: str,
    new_string: str,
    expected_occurrences: int = 1,
) -> Tuple[str, EditReport]:
    """Replace ``old_string`` with ``new_string`` in ``content``.

    ``expected_occurrences`` is a *contract*: we will only apply the
    edit when ``old_string`` appears in ``content`` exactly this many
    times.  Any deviation raises :class:`EditError` — agents must
    disambiguate by widening ``old_string`` or specifying the right
    count.

    Pass ``expected_occurrences=-1`` to allow any positive number of
    matches; useful for "rename this identifier everywhere".

    The function is pure: same inputs → same outputs, no I/O.
    """
    if old_string is None:
        raise EditError("apply_edit: old_string is required")
    if new_string is None:
        raise EditError("apply_edit: new_string is required (use empty string to delete)")
    if old_string == new_string:
        raise EditError(
            "apply_edit: old_string and new_string are identical — "
            "no edit would be applied.  This is almost always a bug "
            "in the planner; refuse rather than commit a no-op."
        )

    # Count occurrences without regex — old_string is treated as
    # literal text, including whitespace and newlines.
    if old_string == "":
        raise EditError("apply_edit: old_string must not be empty")

    n = content.count(old_string)
    if n == 0:
        # Provide a short hint about why nothing matched: indentation
        # mismatch is by far the most common cause on Python files.
        hint = ""
        if old_string.strip() and old_string.strip() in content:
            hint = (
                "  Hint: a stripped form of old_string IS present — "
                "the indentation in your edit does not match the file. "
                "Re-read the surrounding lines and copy them exactly."
            )
        raise EditError(
            "apply_edit: old_string was not found in the file." + hint
        )

    if expected_occurrences == -1:
        # "replace all" mode — at least one match suffices.
        pass
    elif n != expected_occurrences:
        raise EditError(
            f"apply_edit: old_string occurs {n} time(s) in the file, "
            f"but expected_occurrences was {expected_occurrences}. "
            "Widen old_string to include more surrounding context, or "
            "set expected_occurrences to the correct number."
        )

    new_content = content.replace(old_string, new_string)
    return new_content, EditReport(
        occurrences_replaced=n,
        bytes_before=len(content),
        bytes_after=len(new_content),
    )


# ----------------------------------------------------------------------
# apply_unified_diff — minimal patch parser + context-match applier
# ----------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)


@dataclass
class _Hunk:
    """One hunk extracted from a unified diff."""
    old_start: int          # 1-indexed line number from the @@ header
    new_start: int
    lines: List[str]        # raw lines including the leading char


def _parse_unified_diff(diff: str) -> List[_Hunk]:
    """Tolerant parser — accepts diffs with or without file headers
    (``--- a/file`` / ``+++ b/file``).  We only care about the hunks.
    """
    hunks: List[_Hunk] = []
    current: Optional[_Hunk] = None
    for raw in diff.splitlines():
        m = _HUNK_HEADER_RE.match(raw)
        if m:
            if current is not None:
                hunks.append(current)
            current = _Hunk(
                old_start=int(m.group("old_start")),
                new_start=int(m.group("new_start")),
                lines=[],
            )
            continue
        if current is None:
            # Pre-hunk preamble (--- / +++ / diff --git) — ignore.
            continue
        if not raw:
            # An empty line inside a hunk represents a context blank
            # line (some tools emit a bare "\n" with no leading space).
            current.lines.append(" ")
            continue
        prefix = raw[0]
        if prefix in (" ", "+", "-"):
            current.lines.append(raw)
        elif prefix == "\\":
            # "\ No newline at end of file" — silently skip.
            continue
        else:
            # Foreign line inside a hunk — fail loudly so we never
            # silently corrupt the file.
            raise EditError(
                f"apply_unified_diff: malformed hunk line: {raw!r}.  "
                "Lines must start with ' ', '+' or '-'."
            )
    if current is not None:
        hunks.append(current)
    if not hunks:
        raise EditError(
            "apply_unified_diff: no @@ hunks found.  The diff appears empty "
            "or only contains file headers."
        )
    return hunks


def _hunk_old_block(hunk: _Hunk) -> List[str]:
    """Return the contiguous list of pre-edit lines (the ones with
    ``' '`` or ``'-'`` prefix).  These are what we match against."""
    out: List[str] = []
    for ln in hunk.lines:
        if not ln:
            out.append("")
            continue
        if ln[0] in (" ", "-"):
            out.append(ln[1:])
    return out


def _hunk_new_block(hunk: _Hunk) -> List[str]:
    """Return the contiguous list of post-edit lines (``' '`` or
    ``'+'`` prefix)."""
    out: List[str] = []
    for ln in hunk.lines:
        if not ln:
            out.append("")
            continue
        if ln[0] in (" ", "+"):
            out.append(ln[1:])
    return out


def _find_block(haystack: Sequence[str], needle: Sequence[str], near: int) -> int:
    """Locate ``needle`` inside ``haystack`` as a contiguous slice.

    Returns the 0-indexed start position.  Prefers a match near
    ``near`` (1-indexed line from the hunk header, translated by the
    caller) so when the file has several identical blocks the patch
    lands close to where it was authored.  Raises if no exact match.
    """
    if not needle:
        raise EditError("apply_unified_diff: empty hunk")
    matches: List[int] = []
    for i in range(0, len(haystack) - len(needle) + 1):
        if list(haystack[i : i + len(needle)]) == list(needle):
            matches.append(i)
    if not matches:
        raise EditError(
            "apply_unified_diff: could not locate the hunk's context in "
            "the file — the surrounding lines have drifted.  Re-read the "
            "file and regenerate the diff."
        )
    if len(matches) == 1:
        return matches[0]
    # Multiple identical blocks — pick the one nearest to the hunk header.
    target = max(0, near - 1)
    return min(matches, key=lambda pos: abs(pos - target))


def apply_unified_diff(content: str, diff: str) -> Tuple[str, EditReport]:
    """Apply a unified diff to ``content`` by matching context lines
    rather than trusting hunk line numbers.

    Limitations (documented, intentional):

    * Single-file diffs only.  If ``diff`` looks like a multi-file
      patch (``diff --git`` separator with more than one file), the
      caller must split it.
    * No fuzz matching.  Context must match byte-for-byte.  Drift
      caused by another concurrent edit raises :class:`EditError`
      with an actionable message rather than silently mis-editing.
    """
    if diff is None or not diff.strip():
        raise EditError("apply_unified_diff: diff is empty")

    # Detect multi-file diffs BEFORE parsing so the parser doesn't
    # trip on the second file's ``diff --git`` header.
    gits = diff.count("\ndiff --git ") + (1 if diff.startswith("diff --git ") else 0)
    if gits > 1:
        raise EditError(
            "apply_unified_diff: multi-file diff detected; this helper "
            "applies to one file at a time.  Split the patch first."
        )

    hunks = _parse_unified_diff(diff)

    lines = content.splitlines(keepends=True)
    # Drop the keepends so block matching is line-exact; we'll
    # reassemble the line endings from the original where possible.
    raw_lines = [ln.rstrip("\n") for ln in lines]
    # Preserve the original trailing newline state so we don't
    # accidentally drop or add one.
    had_trailing_newline = content.endswith("\n")

    output: List[str] = list(raw_lines)
    total_replacements = 0

    # Apply hunks in order, tracking a running offset so the second
    # hunk's context match accounts for earlier hunks' line-count
    # changes.
    offset = 0
    for hunk in hunks:
        old_block = _hunk_old_block(hunk)
        new_block = _hunk_new_block(hunk)
        near = hunk.old_start + offset
        pos = _find_block(output, old_block, near=near)
        output[pos : pos + len(old_block)] = new_block
        offset += len(new_block) - len(old_block)
        total_replacements += 1

    new_content = "\n".join(output)
    if had_trailing_newline and not new_content.endswith("\n"):
        new_content += "\n"
    elif not had_trailing_newline and new_content.endswith("\n"):
        # Preserve "no newline at end of file" if the original lacked
        # one — only when the diff didn't add it.
        new_content = new_content.rstrip("\n")

    return new_content, EditReport(
        occurrences_replaced=total_replacements,
        bytes_before=len(content),
        bytes_after=len(new_content),
    )


__all__ = [
    "EditError",
    "EditReport",
    "apply_edit",
    "apply_unified_diff",
]
