"""Tests for the Grep backend (Batch B2).

Pin both:

* The in-memory ``grep`` (used by the GitHub-backed agent tool — files
  arrive as a dict because there's no local checkout).
* The ``grep_local`` ripgrep / Python-walk fallback (used by local-git
  + folder modes).  The fallback is tested without ripgrep installed
  by walking the temp directory directly; the ripgrep happy-path is
  only exercised when ``rg`` is available on $PATH.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gitpilot.agent_tools import _glob_to_regex
from gitpilot.grep_backend import (
    GREP_DEFAULT_MAX_RESULTS,
    GREP_HARD_MAX_RESULTS,
    GrepResult,
    format_result,
    grep,
    grep_local,
)


# ----------------------------------------------------------------------
# In-memory backend (GitHub mode)
# ----------------------------------------------------------------------

FILES = {
    "README.md": "GitPilot README\nSee CONTRIBUTING.md\n",
    "src/main.py": (
        "import asyncio\n"
        "def main():\n"
        "    print('hello')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
    "src/util/io.py": (
        "from typing import Any\n"
        "def read(path: str) -> str:\n"
        "    return ''\n"
    ),
    "tests/test_main.py": (
        "from src.main import main\n"
        "def test_main():\n"
        "    main()\n"
    ),
}


def test_grep_finds_basic_token() -> None:
    r = grep(FILES, "asyncio")
    assert not r.error
    assert r.backend == "python"
    assert len(r.hits) == 1
    assert r.hits[0].path == "src/main.py"
    assert r.hits[0].line == 1


def test_grep_returns_multiple_hits_sorted() -> None:
    r = grep(FILES, "def ")
    # def main (main.py), def read (io.py), def test_main (test_main.py)
    assert [h.path for h in r.hits] == [
        "src/main.py",
        "src/util/io.py",
        "tests/test_main.py",
    ]


def test_grep_case_insensitive_flag() -> None:
    assert grep(FILES, "ASYNC").hits == []
    r = grep(FILES, "ASYNC", case_insensitive=True)
    assert len(r.hits) == 1
    assert r.hits[0].path == "src/main.py"


def test_grep_invalid_regex_returns_error() -> None:
    r = grep(FILES, "[unterminated")
    assert r.error and "invalid regex" in r.error
    assert r.hits == []


def test_grep_path_filter_via_glob() -> None:
    rx = _glob_to_regex("**/*.py")
    r = grep(FILES, "import", path_filter=rx)
    assert all(h.path.endswith(".py") for h in r.hits)
    # README.md mentions CONTRIBUTING.md, not import — but the filter
    # would have excluded it anyway.  Belt-and-braces.


def test_grep_truncates_above_cap() -> None:
    big = {f"f{i}.txt": "needle\n" * 50 for i in range(20)}
    r = grep(big, "needle", max_results=10)
    assert r.truncated is True
    assert len(r.hits) == 10


def test_grep_respects_hard_cap_even_when_caller_asks_more() -> None:
    big = {f"f{i}.txt": "needle\n" * 10 for i in range(GREP_HARD_MAX_RESULTS + 50)}
    r = grep(big, "needle", max_results=10_000)
    # capped at GREP_HARD_MAX_RESULTS, regardless of the caller value.
    assert len(r.hits) == GREP_HARD_MAX_RESULTS
    assert r.truncated is True


def test_grep_empty_pattern_match_is_handled_gracefully() -> None:
    """A pattern that matches every line should still respect the cap
    rather than spinning on a multi-MB file."""
    big = {"big.txt": "x\n" * 10_000}
    r = grep(big, ".", max_results=5)
    assert len(r.hits) == 5
    assert r.truncated is True


def test_grep_skips_empty_files_quietly() -> None:
    r = grep({"empty.txt": "", "main.py": "import os\n"}, "import")
    assert [h.path for h in r.hits] == ["main.py"]


# ----------------------------------------------------------------------
# Local backend (folder / local-git mode)
# ----------------------------------------------------------------------

def _seed_local_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("docs\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import asyncio\nprint('hi')\n")
    (tmp_path / "src" / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_x():\n    assert True\n")
    return tmp_path


def test_grep_local_python_fallback_finds_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_local_repo(tmp_path)
    # Force the Python walk regardless of ripgrep availability.
    monkeypatch.setattr("gitpilot.grep_backend.shutil.which", lambda _: None)
    r = grep_local(str(tmp_path), "import")
    assert r.backend == "python"
    assert any(h.path == "src/main.py" and h.line == 1 for h in r.hits)


def test_grep_local_respects_glob_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_local_repo(tmp_path)
    monkeypatch.setattr("gitpilot.grep_backend.shutil.which", lambda _: None)
    r = grep_local(str(tmp_path), "def ", glob_filter="src/**/*.py")
    paths = {h.path for h in r.hits}
    assert paths == {"src/util.py"}


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_grep_local_uses_ripgrep_when_available(tmp_path: Path) -> None:
    _seed_local_repo(tmp_path)
    r = grep_local(str(tmp_path), "import")
    assert r.backend == "ripgrep"
    assert any(h.path == "src/main.py" for h in r.hits)


# ----------------------------------------------------------------------
# Formatter
# ----------------------------------------------------------------------

def test_format_result_no_hits() -> None:
    out = format_result(GrepResult(hits=[]), pattern="xyz")
    assert "No matches" in out


def test_format_result_with_hits_and_truncation() -> None:
    r = grep(FILES, "def ")
    out = format_result(r, pattern="def ")
    assert "def main" in out
    assert "src/main.py" in out


def test_format_result_emits_truncation_hint() -> None:
    big = {f"f{i}.txt": "needle\n" for i in range(10)}
    r = grep(big, "needle", max_results=3)
    out = format_result(r, pattern="needle")
    assert "truncated" in out.lower()


def test_grep_default_cap_is_reasonable() -> None:
    """The default cap must be small enough to fit in any model's
    context, big enough to be useful on realistic repos."""
    assert 50 <= GREP_DEFAULT_MAX_RESULTS <= 200
    assert GREP_HARD_MAX_RESULTS >= GREP_DEFAULT_MAX_RESULTS
