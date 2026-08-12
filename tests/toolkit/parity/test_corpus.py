"""The whole parity corpus in one run — Batch V4-A2.

``make test-parity`` runs this with ``GITPILOT_PARITY_RECORD=1``, which rewrites
``corpus.json`` next to this file: a reviewable record of every string the
registry now produces where a CrewAI tool used to.  Without that variable the
corpus is only compared, never written, so an ordinary ``make test`` cannot
quietly bless a change.

The gate itself is the per-case comparison in the tool test modules; this
module's job is coverage — every canonical tool that has a legacy counterpart
must appear here, so a tool cannot ship without a parity case.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import gitpilot.agent_tools as at
import gitpilot.local_tools as lt
from gitpilot.toolkit import LocalWorkspace, RepoBinding, ToolExecutionContext, ToolRegistry
from gitpilot.toolkit import fs as fs_tools
from gitpilot.toolkit import git as git_tools
from gitpilot.toolkit import terminal as terminal_tools

from ..test_fs import PARITY_CASES as FS_LOCAL_CASES
from ..test_fs_github_parity import PARITY_CASES as FS_GITHUB_CASES
from ..test_git import PARITY_CASES as GIT_CASES
from ..test_terminal import PARITY_CASES as TERMINAL_CASES
from .fixture_github import BRANCH, OWNER, REPO, fake_github
from .fixture_repo import build_fixture_repo
from .harness import ParityCase, ParityOutcome, record_corpus, run_case

CORPUS_PATH = Path(__file__).parent / "corpus.json"
RECORD_ENV = "GITPILOT_PARITY_RECORD"

LOCAL_CASES = [*FS_LOCAL_CASES, *TERMINAL_CASES, *GIT_CASES]


def _full_registry() -> ToolRegistry:
    reg = ToolRegistry()
    fs_tools.register(reg)
    terminal_tools.register(reg)
    git_tools.register(reg)
    return reg


def _run_local(case: ParityCase, tmp_path: Path, index: int) -> ParityOutcome:
    # A fresh checkout per case (indexed, not hashed — PYTHONHASHSEED varies)
    # so a mutating case cannot influence the next one.
    ws = build_fixture_repo(tmp_path / f"local-{index}")
    lt.set_active_workspace(ws)
    try:
        return run_case(
            case, ws, _full_registry(),
            ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
    finally:
        lt._current_workspace = None


def _run_github(case: ParityCase, tmp_path: Path) -> ParityOutcome:
    ws = build_fixture_repo(tmp_path / "shape-only")
    ctx = ToolExecutionContext(
        repo=RepoBinding(owner=OWNER, repo=REPO, token="fixture-token", branch=BRANCH),
    )
    with fake_github():
        at.set_repo_context(OWNER, REPO, token="fixture-token", branch=BRANCH)
        legacy_output = case.legacy(ws)
    with fake_github():
        return run_case(
            ParityCase(
                name=case.name,
                tool=case.tool,
                arguments=case.arguments,
                legacy=lambda _ws, captured=legacy_output: captured,
                normalise=case.normalise,
            ),
            ws, _full_registry(), ctx,
        )


def test_every_case_matches_and_the_corpus_is_recorded(tmp_path):
    outcomes = [_run_local(case, tmp_path, i) for i, case in enumerate(LOCAL_CASES)]
    outcomes += [_run_github(case, tmp_path) for case in FS_GITHUB_CASES]

    if os.environ.get(RECORD_ENV) == "1":
        record_corpus(outcomes, CORPUS_PATH)

    mismatched = [o.name for o in outcomes if not o.matched]
    assert not mismatched, f"parity lost for: {mismatched}"


def test_committed_corpus_covers_every_case():
    """A tool must not be able to ship without appearing in the record."""
    assert CORPUS_PATH.exists(), f"run `make test-parity` to record {CORPUS_PATH.name}"

    recorded = {entry["name"] for entry in json.loads(CORPUS_PATH.read_text())}
    expected = {case.name for case in (*LOCAL_CASES, *FS_GITHUB_CASES)}

    assert expected - recorded == set(), (
        "cases missing from the committed corpus — run `make test-parity`: "
        f"{sorted(expected - recorded)}"
    )


@pytest.mark.parametrize("tool_id", [
    "fs.read", "fs.list", "fs.glob", "fs.grep", "fs.write", "fs.edit", "fs.delete",
    "terminal.run",
    "git.status", "git.diff", "git.log",
])
def test_every_tool_with_a_legacy_counterpart_has_a_case(tool_id):
    covered = {case.tool for case in (*LOCAL_CASES, *FS_GITHUB_CASES)}
    assert tool_id in covered, f"{tool_id} has no parity case"
