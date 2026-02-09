"""Tests for gitpilot.search_tools CrewAI tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gitpilot.search_tools import (
    search_code,
    search_code_global,
    search_issues,
    search_users,
    search_repositories,
    SEARCH_TOOLS,
)


def test_search_tools_exported():
    """All 5 search tools are exported."""
    assert len(SEARCH_TOOLS) == 5


class TestSearchCodeTool:
    @patch("gitpilot.search_tools.gs.search_code", new_callable=AsyncMock)
    def test_returns_results(self, mock_search, repo_context):
        mock_search.return_value = {
            "total_count": 2,
            "items": [
                {"path": "src/main.py", "score": 1.0, "html_url": "https://..."},
                {"path": "src/utils.py", "score": 0.8, "html_url": "https://..."},
            ],
        }
        result = search_code.run(query="def hello")
        assert "src/main.py" in result
        assert "2 total" in result

    @patch("gitpilot.search_tools.gs.search_code", new_callable=AsyncMock)
    def test_no_results(self, mock_search, repo_context):
        mock_search.return_value = {"total_count": 0, "items": []}
        result = search_code.run(query="nonexistent_xyz")
        assert "No code matches" in result


class TestSearchCodeGlobalTool:
    @patch("gitpilot.search_tools.gs.search_code", new_callable=AsyncMock)
    def test_global_search(self, mock_search, repo_context):
        mock_search.return_value = {
            "total_count": 1,
            "items": [
                {
                    "path": "lib/parser.py",
                    "repository": {"full_name": "other/repo"},
                    "html_url": "https://...",
                },
            ],
        }
        result = search_code_global.run(query="parse_config")
        assert "other/repo" in result


class TestSearchIssuesTool:
    @patch("gitpilot.search_tools.gs.search_issues", new_callable=AsyncMock)
    def test_returns_issues(self, mock_search, repo_context):
        mock_search.return_value = {
            "total_count": 1,
            "items": [
                {
                    "number": 5,
                    "state": "open",
                    "title": "Login bug",
                    "html_url": "https://...",
                },
            ],
        }
        result = search_issues.run(query="login bug")
        assert "#5" in result
        assert "Login bug" in result

    @patch("gitpilot.search_tools.gs.search_issues", new_callable=AsyncMock)
    def test_pr_filter(self, mock_search, repo_context):
        mock_search.return_value = {"total_count": 0, "items": []}
        search_issues.run(query="fix", is_pr="true")
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs.get("is_pr") is True


class TestSearchUsersTool:
    @patch("gitpilot.search_tools.gs.search_users", new_callable=AsyncMock)
    def test_returns_users(self, mock_search, repo_context):
        mock_search.return_value = {
            "total_count": 1,
            "items": [
                {"login": "alice", "type": "User", "html_url": "https://..."},
            ],
        }
        result = search_users.run(query="alice")
        assert "@alice" in result

    @patch("gitpilot.search_tools.gs.search_users", new_callable=AsyncMock)
    def test_no_users(self, mock_search, repo_context):
        mock_search.return_value = {"total_count": 0, "items": []}
        result = search_users.run(query="nonexistent")
        assert "No users" in result


class TestSearchRepositoriesTool:
    @patch("gitpilot.search_tools.gs.search_repositories", new_callable=AsyncMock)
    def test_returns_repos(self, mock_search, repo_context):
        mock_search.return_value = {
            "total_count": 1,
            "items": [
                {
                    "full_name": "org/awesome",
                    "stargazers_count": 1000,
                    "description": "Awesome project",
                    "html_url": "https://...",
                },
            ],
        }
        result = search_repositories.run(query="awesome")
        assert "org/awesome" in result
        assert "1000" in result
