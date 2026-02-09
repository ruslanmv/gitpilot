"""Tests for gitpilot.issue_tools CrewAI tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gitpilot.issue_tools import (
    list_issues,
    get_issue,
    create_issue,
    update_issue,
    add_issue_comment,
    list_issue_comments,
    ISSUE_TOOLS,
)


def test_issue_tools_exported():
    """All 6 issue tools are exported."""
    assert len(ISSUE_TOOLS) == 6


class TestListIssuesTool:
    @patch("gitpilot.issue_tools.gi.list_issues", new_callable=AsyncMock)
    def test_returns_formatted_issues(self, mock_list, repo_context):
        mock_list.return_value = [
            {
                "number": 1,
                "state": "open",
                "title": "Bug",
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "alice"}],
                "html_url": "https://github.com/o/r/issues/1",
            }
        ]
        result = list_issues.run()
        assert "#1" in result
        assert "Bug" in result
        assert "bug" in result

    @patch("gitpilot.issue_tools.gi.list_issues", new_callable=AsyncMock)
    def test_no_issues(self, mock_list, repo_context):
        mock_list.return_value = []
        result = list_issues.run()
        assert "No" in result


class TestGetIssueTool:
    @patch("gitpilot.issue_tools.gi.get_issue", new_callable=AsyncMock)
    def test_returns_details(self, mock_get, repo_context):
        mock_get.return_value = {
            "number": 42,
            "title": "Test",
            "state": "open",
            "created_at": "2025-01-01",
            "user": {"login": "bob"},
            "body": "Description here",
            "html_url": "https://github.com/o/r/issues/42",
        }
        result = get_issue.run(issue_number=42)
        assert "#42" in result
        assert "Test" in result
        assert "bob" in result


class TestCreateIssueTool:
    @patch("gitpilot.issue_tools.gi.create_issue", new_callable=AsyncMock)
    def test_creates_issue(self, mock_create, repo_context):
        mock_create.return_value = {
            "number": 99,
            "title": "New",
            "html_url": "https://github.com/o/r/issues/99",
        }
        result = create_issue.run(title="New", body="Body text")
        assert "#99" in result
        assert "Created" in result

    @patch("gitpilot.issue_tools.gi.create_issue", new_callable=AsyncMock)
    def test_parses_labels_csv(self, mock_create, repo_context):
        mock_create.return_value = {"number": 1, "title": "T", "html_url": ""}
        create_issue.run(title="T", labels="bug, enhancement")
        call_kwargs = mock_create.call_args
        labels_arg = call_kwargs.kwargs.get("labels") or call_kwargs[1].get("labels")
        assert labels_arg == ["bug", "enhancement"]


class TestUpdateIssueTool:
    @patch("gitpilot.issue_tools.gi.update_issue", new_callable=AsyncMock)
    def test_updates_issue(self, mock_update, repo_context):
        mock_update.return_value = {"number": 10, "title": "Fixed", "state": "closed"}
        result = update_issue.run(issue_number=10, state="closed")
        assert "Updated" in result


class TestCommentTools:
    @patch("gitpilot.issue_tools.gi.add_issue_comment", new_callable=AsyncMock)
    def test_add_comment(self, mock_add, repo_context):
        mock_add.return_value = {"html_url": "https://github.com/o/r/issues/5#comment"}
        result = add_issue_comment.run(issue_number=5, body="hello")
        assert "Comment added" in result

    @patch("gitpilot.issue_tools.gi.list_issue_comments", new_callable=AsyncMock)
    def test_list_comments(self, mock_list, repo_context):
        mock_list.return_value = [
            {"user": {"login": "alice"}, "body": "looks good"},
        ]
        result = list_issue_comments.run(issue_number=5)
        assert "alice" in result
        assert "looks good" in result

    @patch("gitpilot.issue_tools.gi.list_issue_comments", new_callable=AsyncMock)
    def test_no_comments(self, mock_list, repo_context):
        mock_list.return_value = []
        result = list_issue_comments.run(issue_number=5)
        assert "No comments" in result
