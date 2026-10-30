# tests/bench/test_answer_mentions.py
"""Read tasks are scored on whether the answer names the right files. On
Windows the tools render ``src\\app.py``, and a model that repeats that spelling
has still found the file — the first Windows benchmark run failed read cells on
exactly this, so the checker accepts either separator."""
from __future__ import annotations

from bench.tasks import _answer_mentions

WINDOWS_SPELLING = "src" + "\\" + "app.py"


def test_the_posix_spelling_passes():
    assert _answer_mentions("greet lives in src/app.py", "src/app.py").ok


def test_the_windows_spelling_passes_too():
    assert _answer_mentions(f"greet is defined in `{WINDOWS_SPELLING}`.", "src/app.py").ok


def test_every_needle_is_still_required():
    outcome = _answer_mentions(f"see {WINDOWS_SPELLING}", "src/app.py", "src/util.py")
    assert not outcome.ok
    assert outcome.detail == "the answer never mentioned src/util.py"


def test_an_unrelated_answer_still_fails():
    assert not _answer_mentions("it is in main.py", "src/app.py").ok
