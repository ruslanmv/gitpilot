"""Git tools — Batch V4-A5, against a real git checkout."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

import gitpilot.local_tools as lt
from gitpilot.toolkit import (
    Effect,
    LocalWorkspace,
    RepoBinding,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
)
from gitpilot.toolkit import git as git_tools

from .parity.fixture_repo import build_fixture_repo
from .parity.harness import ParityCase, assert_parity, run_case


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    git_tools.register(reg)
    return reg


@pytest.fixture()
def local_repo(tmp_path):
    ws = build_fixture_repo(tmp_path / "repo")
    return ws, ToolExecutionContext(workspace=LocalWorkspace(root=ws.path))


def _run(registry, tool, arguments, ctx):
    return asyncio.run(registry.execute(ToolCall(id="t", tool=tool, arguments=arguments), ctx))


class TestReads:
    def test_status_on_a_clean_tree(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "git.status", {}, ctx)

        assert result.ok
        assert json.loads(result.content) == result.data

    def test_status_sees_an_edit(self, registry, local_repo):
        ws, ctx = local_repo
        (ws.path / "src/util.py").write_text("def helper():\n    return 99\n")

        result = _run(registry, "git.status", {}, ctx)

        assert "src/util.py" in result.content

    def test_diff_reports_no_changes_on_a_clean_tree(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "git.diff", {}, ctx)

        assert result.content == "No changes."
        assert result.data["empty"] is True

    def test_diff_shows_the_change(self, registry, local_repo):
        ws, ctx = local_repo
        (ws.path / "src/util.py").write_text("def helper():\n    return 99\n")

        result = _run(registry, "git.diff", {}, ctx)

        assert "-    return 1" in result.content
        assert "+    return 99" in result.content
        assert result.data["empty"] is False

    def test_log_returns_structured_commits(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "git.log", {"count": 5}, ctx)

        commits = result.data["commits"]
        assert commits[0]["message"] == "Fixture commit"
        assert commits[0]["author"] == "Parity Fixture"

    def test_log_count_bounds_enforced(self, registry, local_repo):
        _ws, ctx = local_repo
        assert _run(registry, "git.log", {"count": 0}, ctx).error == "invalid_arguments"
        assert _run(registry, "git.log", {"count": 500}, ctx).error == "invalid_arguments"


class TestMutations:
    def test_branch_creates_and_switches(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "git.branch", {"name": "feature/x"}, ctx)

        assert result.data["branch"] == "feature/x"
        status = _run(registry, "git.status", {}, ctx)
        assert "feature/x" in status.content

    def test_commit_returns_a_sha(self, registry, local_repo):
        ws, ctx = local_repo
        (ws.path / "new.txt").write_text("hi\n")

        result = _run(registry, "git.commit", {"message": "Add new.txt"}, ctx)

        assert len(result.data["sha"]) == 40
        assert result.data["message"] == "Add new.txt"

    def test_commit_can_stage_specific_files(self, registry, local_repo):
        ws, ctx = local_repo
        (ws.path / "one.txt").write_text("1\n")
        (ws.path / "two.txt").write_text("2\n")

        _run(registry, "git.commit", {"message": "Only one", "files": ["one.txt"]}, ctx)

        status = _run(registry, "git.status", {}, ctx)
        assert "two.txt" in status.content, "the unstaged file must still be pending"

    def test_commit_failure_is_reported_not_raised(self, registry, local_repo):
        """Nothing staged: git exits non-zero and the model needs to know why."""
        _ws, ctx = local_repo
        result = _run(registry, "git.commit", {"message": "empty"}, ctx)

        assert not result.ok and result.error == "git_failed"

    def test_stash_round_trip(self, registry, local_repo):
        ws, ctx = local_repo
        (ws.path / "src/util.py").write_text("def helper():\n    return 99\n")

        _run(registry, "git.stash", {}, ctx)
        assert _run(registry, "git.diff", {}, ctx).data["empty"] is True

        _run(registry, "git.stash", {"pop": True}, ctx)
        assert _run(registry, "git.diff", {}, ctx).data["empty"] is False


class TestNoLocalCheckout:
    def test_github_only_session_gets_a_clean_refusal(self, registry):
        """A plausible empty diff would mislead; 'no checkout' is actionable."""
        ctx = ToolExecutionContext(repo=RepoBinding(owner="o", repo="r", branch="main"))
        result = _run(registry, "git.diff", {}, ctx)

        assert not result.ok
        assert "no local workspace" in result.content


class TestRiskClassification:
    def test_reads_are_safe(self, registry):
        for tool in ("git.status", "git.diff", "git.log"):
            assert registry.spec(tool).risk is Risk.SAFE, tool

    def test_local_mutations_need_approval(self, registry):
        for tool in ("git.branch", "git.commit", "git.stash"):
            assert registry.spec(tool).risk is Risk.APPROVAL, tool

    def test_push_is_high_risk_and_declares_the_remote_effect(self, registry):
        spec = registry.spec("git.push")
        assert spec.risk is Risk.HIGH_RISK
        assert Effect.GIT_REMOTE in spec.effects

    def test_reads_share_one_capability_key(self, registry):
        """A topology grants 'git.read' once rather than three tools by name."""
        for tool in ("git.status", "git.diff", "git.log"):
            assert registry.spec(tool).capability == "git.read", tool


class TestPush:
    def test_push_passes_force_through(self, registry, local_repo):
        _ws, ctx = local_repo
        with patch("gitpilot.workspace.WorkspaceManager.push", new_callable=AsyncMock) as push:
            push.return_value = {"branch": "main", "status": "pushed"}
            result = _run(registry, "git.push", {"force": True}, ctx)

        assert push.await_args.kwargs["force"] is True
        assert result.data["force"] is True


PARITY_CASES = [
    ParityCase(
        name="git.status/clean",
        tool="git.status",
        arguments={},
        legacy=lambda ws: lt.git_status.func(),
    ),
    ParityCase(
        name="git.diff/clean",
        tool="git.diff",
        arguments={},
        legacy=lambda ws: lt.git_diff.func("false"),
    ),
    ParityCase(
        name="git.log/default",
        tool="git.log",
        arguments={},
        legacy=lambda ws: lt.git_log.func("10"),
    ),
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda c: c.name)
def test_registry_output_matches_the_crewai_tool(case, registry, tmp_path):
    ws = build_fixture_repo(tmp_path / "repo")
    lt.set_active_workspace(ws)
    try:
        outcome = run_case(
            case, ws, registry, ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
    finally:
        lt._current_workspace = None

    assert_parity(outcome)
