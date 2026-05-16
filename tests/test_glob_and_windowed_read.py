"""Tests for the Glob / windowed-Read tools (Batch B1).

Pin both:

* The pure-Python ``_glob_match`` semantics — `/`-aware, `**`-as-any.
  These mirror Claude Code / ripgrep / bash, NOT the looser ``fnmatch``
  default.
* The windowed-Read helpers (``_coerce_int`` and the default/max
  constants) so future refactors don't silently widen them.
"""
from __future__ import annotations

import pytest

from gitpilot.agent_tools import (
    GLOB_DEFAULT_MAX_RESULTS,
    GLOB_HARD_MAX_RESULTS,
    READ_DEFAULT_LIMIT,
    READ_MAX_LIMIT,
    _coerce_int,
    _glob_match,
    _glob_to_regex,
)


PATHS = [
    "README.md",
    "LICENSE",
    "src/main.py",
    "src/util/io.py",
    "src/util/__init__.py",
    "tests/test_main.py",
    "tests/unit/test_util.py",
    "docs/intro.md",
    "docs/guide/setup.md",
    ".github/workflows/ci.yml",
]


# ----------------------------------------------------------------------
# Glob semantics
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("**/*.py", [
            "src/main.py",
            "src/util/__init__.py",
            "src/util/io.py",
            "tests/test_main.py",
            "tests/unit/test_util.py",
        ]),
        ("src/**/*.py", [
            "src/main.py",
            "src/util/__init__.py",
            "src/util/io.py",
        ]),
        ("**/test_*.py", [
            "tests/test_main.py",
            "tests/unit/test_util.py",
        ]),
        ("*.md", ["README.md"]),                # top-level only
        ("**/*.md", ["README.md", "docs/guide/setup.md", "docs/intro.md"]),
        ("docs/*.md", ["docs/intro.md"]),       # immediate child of docs
        ("docs/**/*.md", ["docs/guide/setup.md", "docs/intro.md"]),
        ("README*", ["README.md"]),
        ("LICENSE", ["LICENSE"]),
        (".github/**/*.yml", [".github/workflows/ci.yml"]),
        ("nope/*.py", []),
    ],
)
def test_glob_match_matches_expected_paths(pattern: str, expected: list[str]) -> None:
    got = sorted(_glob_match(PATHS, pattern))
    assert got == sorted(expected), f"pattern={pattern!r}"


def test_star_does_not_cross_slash() -> None:
    """`*` must NOT consume `/` — this is the contract that
    distinguishes shell glob from fnmatch.  ``src/*`` therefore matches
    only direct children of ``src``, never nested ones."""
    got = sorted(_glob_match(PATHS, "src/*"))
    assert got == ["src/main.py"]


def test_double_star_crosses_slash() -> None:
    """`**` must consume any number of segments, including zero."""
    rx = _glob_to_regex("**/foo.py")
    assert rx.match("foo.py")          # zero-segment case
    assert rx.match("src/foo.py")
    assert rx.match("a/b/c/foo.py")
    assert not rx.match("foo.txt")


def test_character_class_passes_through() -> None:
    rx = _glob_to_regex("test_[ab].py")
    assert rx.match("test_a.py")
    assert rx.match("test_b.py")
    assert not rx.match("test_c.py")


def test_question_mark_matches_single_non_slash() -> None:
    rx = _glob_to_regex("a?.py")
    assert rx.match("ab.py")
    assert not rx.match("a/b.py")
    assert not rx.match("abc.py")


# ----------------------------------------------------------------------
# Defaults / bounds
# ----------------------------------------------------------------------

def test_constants_have_sensible_bounds() -> None:
    assert 1 <= GLOB_DEFAULT_MAX_RESULTS <= GLOB_HARD_MAX_RESULTS
    assert READ_DEFAULT_LIMIT < READ_MAX_LIMIT
    assert READ_MAX_LIMIT <= 100_000   # belt-and-braces ceiling


# ----------------------------------------------------------------------
# Coercion (CrewAI passes ints as strings, sometimes as schema dicts)
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, default, expected",
    [
        (5, 999, 5),
        ("5", 999, 5),
        ("  12 ", 999, 12),
        (None, 7, 7),
        ("nope", 7, 7),
        ({"description": "...", "type": "int"}, 7, 7),
        (True, 7, 7),         # bool must be ignored, not interpreted as 1
        (3.7, 7, 3),
    ],
)
def test_coerce_int_handles_crewai_quirks(value, default, expected) -> None:
    assert _coerce_int(value, default) == expected
