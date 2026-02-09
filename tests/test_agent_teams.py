"""Tests for gitpilot.agent_teams module."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.agent_teams import AgentTeam, SubTask, SubTaskStatus, TeamResult


# ---------------------------------------------------------------------------
# SubTask dataclass
# ---------------------------------------------------------------------------

class TestSubTask:
    def test_default_fields(self):
        st = SubTask()
        assert st.title == ""
        assert st.description == ""
        assert st.status == SubTaskStatus.PENDING
        assert st.result == ""
        assert st.error is None
        assert st.worktree_path is None
        assert st.started_at is None
        assert st.completed_at is None
        assert len(st.id) == 8

    def test_custom_fields(self):
        st = SubTask(
            id="abc12345",
            title="Build API",
            description="Create REST endpoints",
            assigned_agent="agent_0",
            files=["api.py"],
            status=SubTaskStatus.RUNNING,
            result="done",
        )
        assert st.id == "abc12345"
        assert st.title == "Build API"
        assert st.files == ["api.py"]
        assert st.status == SubTaskStatus.RUNNING
        assert st.result == "done"

    def test_to_dict(self):
        st = SubTask(id="x", title="T", description="D", assigned_agent="a")
        d = st.to_dict()
        assert d["id"] == "x"
        assert d["title"] == "T"
        assert d["status"] == "pending"
        assert d["error"] is None

    def test_unique_ids(self):
        ids = {SubTask().id for _ in range(20)}
        assert len(ids) == 20  # All IDs should be unique


# ---------------------------------------------------------------------------
# TeamResult dataclass
# ---------------------------------------------------------------------------

class TestTeamResult:
    def test_default_fields(self):
        tr = TeamResult(task="test")
        assert tr.task == "test"
        assert tr.subtasks == []
        assert tr.merge_status == "pending"
        assert tr.conflicts == []
        assert tr.summary == ""

    def test_all_completed_true(self):
        s1 = SubTask(status=SubTaskStatus.COMPLETED)
        s2 = SubTask(status=SubTaskStatus.COMPLETED)
        tr = TeamResult(task="t", subtasks=[s1, s2])
        assert tr.all_completed is True
        assert tr.any_failed is False

    def test_all_completed_false_when_pending(self):
        s1 = SubTask(status=SubTaskStatus.COMPLETED)
        s2 = SubTask(status=SubTaskStatus.PENDING)
        tr = TeamResult(task="t", subtasks=[s1, s2])
        assert tr.all_completed is False

    def test_any_failed_true(self):
        s1 = SubTask(status=SubTaskStatus.COMPLETED)
        s2 = SubTask(status=SubTaskStatus.FAILED)
        tr = TeamResult(task="t", subtasks=[s1, s2])
        assert tr.any_failed is True
        assert tr.all_completed is False

    def test_to_dict(self):
        s1 = SubTask(id="a", status=SubTaskStatus.COMPLETED)
        tr = TeamResult(task="deploy", subtasks=[s1], merge_status="merged", summary="ok")
        d = tr.to_dict()
        assert d["task"] == "deploy"
        assert d["merge_status"] == "merged"
        assert d["summary"] == "ok"
        assert d["all_completed"] is True
        assert d["any_failed"] is False
        assert len(d["subtasks"]) == 1

    def test_to_dict_empty_subtasks(self):
        tr = TeamResult(task="empty")
        d = tr.to_dict()
        assert d["subtasks"] == []
        assert d["all_completed"] is True  # vacuous truth
        assert d["any_failed"] is False


# ---------------------------------------------------------------------------
# AgentTeam.plan_and_split
# ---------------------------------------------------------------------------

class TestPlanAndSplit:
    def test_auto_split_default_4_agents(self):
        team = AgentTeam()
        subtasks = team.plan_and_split("Add auth")
        assert len(subtasks) == 4
        for st in subtasks:
            assert "Add auth" in st.description
            assert st.status == SubTaskStatus.PENDING

    def test_auto_split_custom_num_agents(self):
        team = AgentTeam()
        subtasks = team.plan_and_split("Refactor DB", num_agents=2)
        assert len(subtasks) == 2

    def test_auto_split_capped_at_8(self):
        team = AgentTeam()
        subtasks = team.plan_and_split("Big task", num_agents=20)
        assert len(subtasks) == 8

    def test_custom_subtask_descriptions(self):
        team = AgentTeam()
        descs = [
            {"title": "Models", "description": "Create models", "agent": "alice", "files": ["models.py"]},
            {"title": "Routes", "description": "Create routes", "agent": "bob", "files": ["routes.py"]},
        ]
        subtasks = team.plan_and_split("Build app", subtask_descriptions=descs)
        assert len(subtasks) == 2
        assert subtasks[0].title == "Models"
        assert subtasks[0].assigned_agent == "alice"
        assert subtasks[0].files == ["models.py"]
        assert subtasks[1].title == "Routes"

    def test_custom_descriptions_missing_keys(self):
        team = AgentTeam()
        descs = [{"description": "Just a desc"}]
        subtasks = team.plan_and_split("Task", subtask_descriptions=descs)
        assert subtasks[0].title == "Subtask 1"
        assert subtasks[0].assigned_agent == "agent_0"


# ---------------------------------------------------------------------------
# AgentTeam.execute_parallel
# ---------------------------------------------------------------------------

class TestExecuteParallel:
    @pytest.mark.asyncio
    async def test_default_executor(self):
        team = AgentTeam()
        subtasks = [SubTask(title="A"), SubTask(title="B")]
        result = await team.execute_parallel(subtasks)
        assert result.task == "parallel_execution"
        for st in result.subtasks:
            assert st.status == SubTaskStatus.COMPLETED
            assert "Completed:" in st.result
            assert st.started_at is not None
            assert st.completed_at is not None

    @pytest.mark.asyncio
    async def test_custom_executor(self):
        async def my_executor(subtask):
            return f"Result for {subtask.title}"

        team = AgentTeam()
        subtasks = [SubTask(title="X")]
        result = await team.execute_parallel(subtasks, executor_fn=my_executor)
        assert result.subtasks[0].result == "Result for X"
        assert result.subtasks[0].status == SubTaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_executor_failure(self):
        async def failing_executor(subtask):
            raise RuntimeError("Agent crashed")

        team = AgentTeam()
        subtasks = [SubTask(title="Fail")]
        result = await team.execute_parallel(subtasks, executor_fn=failing_executor)
        assert result.subtasks[0].status == SubTaskStatus.FAILED
        assert result.subtasks[0].error == "Agent crashed"
        assert result.subtasks[0].completed_at is not None


# ---------------------------------------------------------------------------
# AgentTeam.merge_results
# ---------------------------------------------------------------------------

class TestMergeResults:
    @pytest.mark.asyncio
    async def test_merge_no_conflicts(self):
        team = AgentTeam()
        s1 = SubTask(status=SubTaskStatus.COMPLETED, assigned_agent="a", files=["f1.py"])
        s2 = SubTask(status=SubTaskStatus.COMPLETED, assigned_agent="b", files=["f2.py"])
        tr = TeamResult(task="t", subtasks=[s1, s2])
        merged = await team.merge_results(tr)
        assert merged.merge_status == "merged"
        assert merged.conflicts == []
        assert "2 subtasks completed" in merged.summary

    @pytest.mark.asyncio
    async def test_merge_with_conflicts(self):
        team = AgentTeam()
        s1 = SubTask(status=SubTaskStatus.COMPLETED, assigned_agent="a", files=["shared.py"])
        s2 = SubTask(status=SubTaskStatus.COMPLETED, assigned_agent="b", files=["shared.py"])
        tr = TeamResult(task="t", subtasks=[s1, s2])
        merged = await team.merge_results(tr)
        assert merged.merge_status == "conflict"
        assert "shared.py" in merged.conflicts
        assert "Manual review" in merged.summary

    @pytest.mark.asyncio
    async def test_merge_with_failed_subtask(self):
        team = AgentTeam()
        s1 = SubTask(title="Good", status=SubTaskStatus.COMPLETED)
        s2 = SubTask(title="Bad", status=SubTaskStatus.FAILED, error="timeout")
        tr = TeamResult(task="t", subtasks=[s1, s2])
        merged = await team.merge_results(tr)
        assert merged.merge_status == "failed"
        assert "1 subtask(s) failed" in merged.summary
        assert "timeout" in merged.summary


# ---------------------------------------------------------------------------
# AgentTeam.setup_worktrees / cleanup_worktrees
# ---------------------------------------------------------------------------

class TestWorktrees:
    @pytest.mark.asyncio
    async def test_setup_worktrees_no_workspace(self):
        team = AgentTeam(workspace_path=None)
        subtasks = [SubTask()]
        await team.setup_worktrees(subtasks)
        # Should do nothing without workspace_path
        assert subtasks[0].worktree_path is None

    @pytest.mark.asyncio
    async def test_cleanup_worktrees_no_workspace(self):
        team = AgentTeam(workspace_path=None)
        # Should not raise
        await team.cleanup_worktrees()
        assert team._worktrees == []

    @pytest.mark.asyncio
    async def test_setup_worktrees_calls_git(self, tmp_path):
        team = AgentTeam(workspace_path=tmp_path)
        subtask = SubTask(id="abc123")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await team.setup_worktrees([subtask])

        assert mock_exec.called
        call_args = mock_exec.call_args
        assert "git" in call_args[0]
        assert "worktree" in call_args[0]
        assert subtask.worktree_path is not None
        assert len(team._worktrees) == 1

    @pytest.mark.asyncio
    async def test_cleanup_worktrees_calls_git(self, tmp_path):
        team = AgentTeam(workspace_path=tmp_path)
        team._worktrees = [tmp_path / "wt1", tmp_path / "wt2"]

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await team.cleanup_worktrees()

        assert mock_exec.call_count == 2
        assert team._worktrees == []
