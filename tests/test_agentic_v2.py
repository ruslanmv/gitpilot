"""Tests for the v2 additions to gitpilot.agentic module.

Tests the agent builders, dispatch_request, and create_pr_after_execution
without invoking real LLMs -- everything is mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.agentic import (
    _build_issue_agent,
    _build_pr_agent,
    _build_search_agent,
    _build_code_review_agent,
    _build_learning_agent,
    _build_task_description,
    _get_agent,
    create_pr_after_execution,
    dispatch_request,
    get_flow_definition,
)
from gitpilot.agent_router import AgentType, RequestCategory, WorkflowPlan


# ---------------------------------------------------------------------------
# Agent builder smoke tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_crewai_llm():
    """Prevent CrewAI from initialising a real LLM (needs API keys)."""
    with patch("crewai.agent.core.create_llm", return_value=MagicMock()):
        yield


class TestAgentBuilders:
    """Verify each builder returns an Agent with expected properties."""

    def _fake_llm(self):
        # CrewAI Agent validates that llm must be a non-empty string
        return "gpt-4o-mini"

    def test_build_issue_agent(self):
        agent = _build_issue_agent(self._fake_llm())
        assert agent.role == "GitHub Issue Management Specialist"
        assert len(agent.tools) > 0

    def test_build_pr_agent(self):
        agent = _build_pr_agent(self._fake_llm())
        assert agent.role == "Pull Request Management Specialist"

    def test_build_search_agent(self):
        agent = _build_search_agent(self._fake_llm())
        assert agent.role == "Search & Discovery Specialist"

    def test_build_code_review_agent(self):
        agent = _build_code_review_agent(self._fake_llm())
        assert "Code Review" in agent.role

    def test_build_learning_agent(self):
        agent = _build_learning_agent(self._fake_llm())
        assert "Learning" in agent.role


class TestGetAgent:
    def test_all_agent_types(self):
        llm = "gpt-4o-mini"
        for agent_type in AgentType:
            agent = _get_agent(agent_type, llm)
            assert agent is not None
            assert agent.role  # non-empty role

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown agent type"):
            _get_agent("nonexistent", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# Task description builder
# ---------------------------------------------------------------------------

class TestBuildTaskDescription:
    def test_issue_management(self):
        wf = WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="Create issue",
            metadata={"action": "create"},
        )
        desc = _build_task_description(wf, "create issue about bug", "o/r", "main")
        assert "ISSUE MANAGEMENT" in desc
        assert "create" in desc
        assert "o/r" in desc

    def test_pr_management(self):
        wf = WorkflowPlan(
            category=RequestCategory.PR_MANAGEMENT,
            agents=[AgentType.PR_MANAGER],
            description="Create PR",
            metadata={"action": "create"},
        )
        desc = _build_task_description(wf, "create pr", "o/r", None)
        assert "PULL REQUEST" in desc

    def test_code_search(self):
        wf = WorkflowPlan(
            category=RequestCategory.CODE_SEARCH,
            agents=[AgentType.SEARCH],
            description="Search code",
            metadata={"search_type": "code"},
        )
        desc = _build_task_description(wf, "find func", "o/r", None)
        assert "SEARCH" in desc
        assert "code" in desc

    def test_entity_number_included(self):
        wf = WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="Update issue",
            entity_number=42,
            metadata={"action": "update"},
        )
        desc = _build_task_description(wf, "update issue #42", "o/r", None)
        assert "#42" in desc


# ---------------------------------------------------------------------------
# create_pr_after_execution
# ---------------------------------------------------------------------------

class TestCreatePrAfterExecution:
    @pytest.mark.asyncio
    @patch("gitpilot.github_pulls.create_pull_request", new_callable=AsyncMock)
    @patch("gitpilot.github_api.get_repo", new_callable=AsyncMock)
    async def test_creates_pr(self, mock_repo, mock_pr):
        mock_repo.return_value = {"default_branch": "main"}
        mock_pr.return_value = {"number": 1, "html_url": "https://github.com/o/r/pull/1"}

        result = await create_pr_after_execution(
            "o/r", "gitpilot-branch", "My goal",
            {"steps": [{"summary": "Step 1: done"}]},
            token="tok",
        )
        assert result is not None
        assert result["number"] == 1
        mock_pr.assert_called_once()
        call_kwargs = mock_pr.call_args.kwargs
        assert call_kwargs["head"] == "gitpilot-branch"
        assert call_kwargs["base"] == "main"

    @pytest.mark.asyncio
    @patch("gitpilot.github_pulls.create_pull_request", new_callable=AsyncMock)
    @patch("gitpilot.github_api.get_repo", new_callable=AsyncMock)
    async def test_returns_none_on_failure(self, mock_repo, mock_pr):
        mock_repo.return_value = {"default_branch": "main"}
        mock_pr.side_effect = Exception("API error")

        result = await create_pr_after_execution(
            "o/r", "branch", "goal", {}, token="tok",
        )
        assert result is None

    @pytest.mark.asyncio
    @patch("gitpilot.github_pulls.create_pull_request", new_callable=AsyncMock)
    @patch("gitpilot.github_api.get_repo", new_callable=AsyncMock)
    async def test_falls_back_to_main(self, mock_repo, mock_pr):
        """If get_repo fails, default_branch should be 'main'."""
        mock_repo.side_effect = Exception("not found")
        mock_pr.return_value = {"number": 2, "html_url": ""}

        result = await create_pr_after_execution(
            "o/r", "branch", "goal", {}, token="tok",
        )
        assert result is not None
        assert mock_pr.call_args.kwargs["base"] == "main"

    @pytest.mark.asyncio
    @patch("gitpilot.github_pulls.create_pull_request", new_callable=AsyncMock)
    @patch("gitpilot.github_api.get_repo", new_callable=AsyncMock)
    async def test_title_truncation(self, mock_repo, mock_pr):
        mock_repo.return_value = {"default_branch": "main"}
        mock_pr.return_value = {"number": 3, "html_url": ""}
        long_goal = "x" * 300

        await create_pr_after_execution("o/r", "b", long_goal, {}, token="tok")
        title = mock_pr.call_args.kwargs["title"]
        assert len(title) <= 256


# ---------------------------------------------------------------------------
# Flow definition
# ---------------------------------------------------------------------------

class TestFlowDefinition:
    @pytest.mark.asyncio
    async def test_has_router_node(self):
        flow = await get_flow_definition()
        node_ids = [n["id"] for n in flow["nodes"]]
        assert "router" in node_ids

    @pytest.mark.asyncio
    async def test_has_all_agent_nodes(self):
        flow = await get_flow_definition()
        node_ids = {n["id"] for n in flow["nodes"]}
        expected = {
            "router", "repo_explorer", "planner", "code_writer",
            "reviewer", "issue_manager", "pr_manager",
            "search_agent", "learning_agent", "github_tools",
        }
        assert expected.issubset(node_ids)

    @pytest.mark.asyncio
    async def test_edges_reference_valid_nodes(self):
        flow = await get_flow_definition()
        node_ids = {n["id"] for n in flow["nodes"]}
        for edge in flow["edges"]:
            assert edge["source"] in node_ids, f"edge {edge['id']} source {edge['source']} not in nodes"
            assert edge["target"] in node_ids, f"edge {edge['id']} target {edge['target']} not in nodes"
