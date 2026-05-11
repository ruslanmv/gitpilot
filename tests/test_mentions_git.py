"""Additional coverage for the git-backed @-mention expanders.

Uses a stub ``subprocess.run`` so the tests are independent of the local
git configuration and run in every CI environment.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from gitpilot import mentions
from gitpilot.mentions import MentionParser, expand


# ----------------------------------------------------------------------
# subprocess.run stub
# ----------------------------------------------------------------------

@dataclass
class _FakeProc:
    returncode: int
    stdout: str
    stderr: str = ""


def _stub_run(responses: dict[tuple[str, ...], _FakeProc]):
    def runner(args, **_kwargs):
        key = tuple(args)
        for k, v in responses.items():
            if key[: len(k)] == k:
                return v
        return _FakeProc(returncode=128, stdout="", stderr="unknown args")
    return runner


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ----------------------------------------------------------------------
# commit / diff expanders
# ----------------------------------------------------------------------

def test_commit_mention_returns_show_output(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        _stub_run({
            ("git", "show"): _FakeProc(0, "commit abc\nupdate greeting\n diff --git a b\n"),
        }),
    )
    result = MentionParser(workspace).parse("look at @commit:HEAD")
    exp = result.expansions[0]
    assert exp.kind == "commit"
    assert exp.error is None
    assert "update greeting" in exp.body


def test_diff_mention_returns_diff_output(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        _stub_run({
            ("git", "diff"): _FakeProc(0, "diff --git a b\n+hello world\n"),
        }),
    )
    result = MentionParser(workspace).parse("review @diff:HEAD~1..HEAD")
    exp = result.expansions[0]
    assert exp.kind == "diff"
    assert exp.error is None
    assert "hello world" in exp.body


def test_diff_with_unknown_revision_reports_error(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        _stub_run({("git", "diff"): _FakeProc(128, "", "bad revision")}),
    )
    result = MentionParser(workspace).parse("@diff:nope..nowhere")
    exp = result.expansions[0]
    assert exp.kind == "diff"
    assert exp.error == "git failed"


def test_git_subprocess_exception_reports_error(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_kw):
        raise OSError("git not found")
    monkeypatch.setattr(subprocess, "run", boom)
    result = MentionParser(workspace).parse("@commit:HEAD")
    assert result.expansions[0].error == "git failed"


# ----------------------------------------------------------------------
# non-git expanders that share the same file
# ----------------------------------------------------------------------

def test_pr_mention_is_placeholder(workspace: Path) -> None:
    result = expand("see @pr:42", workspace)
    exp = result.expansions[0]
    assert exp.kind == "pr"
    assert "42" in exp.body


def test_invalid_glob_returns_no_match_error(workspace: Path) -> None:
    result = MentionParser(workspace).parse("@glob:does/not/exist/*.py")
    exp = result.expansions[0]
    assert exp.error == "no matches"


def test_problems_unreadable_file(workspace: Path) -> None:
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "problems.json").write_text("{not json}")
    result = MentionParser(workspace).parse("@problems")
    exp = result.expansions[0]
    assert exp.error is not None


def test_problems_renders_empty_list(workspace: Path) -> None:
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "problems.json").write_text("[]")
    result = MentionParser(workspace).parse("@problems")
    exp = result.expansions[0]
    assert exp.error is None
    assert "no diagnostics" in exp.body


def test_selection_truncates_oversized_content(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITPILOT_SELECTION", "x" * 100_000)
    parser = MentionParser(workspace, max_file_bytes=128)
    result = parser.parse("@selection")
    exp = result.expansions[0]
    assert exp.kind == "selection"
    assert len(exp.body) <= 256
