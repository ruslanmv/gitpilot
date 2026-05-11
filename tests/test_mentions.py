"""Tests for the @-mention parser."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gitpilot.mentions import MentionParser, expand


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def hello():\n    return 'hi'\n")
    (tmp_path / "src" / "b.ts").write_text("export const x = 1;\n")
    (tmp_path / "README.md").write_text("# Project\n\nSome notes.\n")
    (tmp_path / ".gitpilot").mkdir()
    (tmp_path / ".gitpilot" / "problems.json").write_text(
        '[{"severity":"error","file":"src/a.py","line":3,"message":"oops"}]'
    )
    return tmp_path


def test_file_mention_inlines_contents(workspace: Path) -> None:
    parser = MentionParser(workspace)
    result = parser.parse("Please @./src/a.py refactor")
    assert len(result.expansions) == 1
    exp = result.expansions[0]
    assert exp.kind == "file"
    assert "def hello" in exp.body
    assert "title=src/a.py" in exp.body


def test_glob_mention_lists_matches(workspace: Path) -> None:
    parser = MentionParser(workspace)
    result = parser.parse("scan @glob:src/*.py")
    exp = result.expansions[0]
    assert exp.kind == "glob"
    assert "src/a.py" in exp.body


def test_problems_mention_reads_diagnostics_file(workspace: Path) -> None:
    parser = MentionParser(workspace)
    result = parser.parse("look at @problems")
    exp = result.expansions[0]
    assert exp.kind == "problems"
    assert "src/a.py" in exp.body


def test_selection_uses_env_var(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SELECTION", "console.log('hi')")
    parser = MentionParser(workspace)
    result = parser.parse("inspect @selection")
    exp = result.expansions[0]
    assert exp.kind == "selection"
    assert "console.log" in exp.body


def test_unknown_token_errors_gracefully(workspace: Path) -> None:
    result = expand("hello @nonsense world", workspace)
    assert result.expansions[0].error == "unrecognised token"


def test_path_escape_is_blocked(workspace: Path) -> None:
    parser = MentionParser(workspace)
    result = parser.parse("read @../../../etc/passwd")
    exp = result.expansions[0]
    # Either rejected outright or reported as "not found" (both safe outcomes).
    assert exp.body == "" and exp.error is not None


def test_context_block_renders_only_when_mentions_exist(workspace: Path) -> None:
    parser = MentionParser(workspace)
    assert parser.parse("plain message").to_context_block() == ""
    block = parser.parse("@./src/a.py").to_context_block()
    assert "## Mentions" in block
