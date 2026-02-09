"""Tests for gitpilot.pr_tools CrewAI tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gitpilot.pr_tools import (
    list_pull_requests,
    get_pull_request,
    create_pull_request,
    merge_pull_request,
    list_pr_files,
    create_pr_review,
    add_pr_comment,
    PR_TOOLS,
)


def test_pr_tools_exported():
    """All 7 PR tools are exported."""
    assert len(PR_TOOLS) == 7


class TestListPRTool:
    @patch("gitpilot.pr_tools.gp.list_pull_requests", new_callable=AsyncMock)
    def test_returns_formatted(self, mock_list, repo_context):
        mock_list.return_value = [
            {
                "number": 1,
                "state": "open",
                "title": "Feature",
                "head": {"ref": "feat"},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
                "draft": False,
                "html_url": "https://github.com/o/r/pull/1",
            }
        ]
        result = list_pull_requests.run()
        assert "#1" in result
        assert "Feature" in result

    @patch("gitpilot.pr_tools.gp.list_pull_requests", new_callable=AsyncMock)
    def test_no_prs(self, mock_list, repo_context):
        mock_list.return_value = []
        result = list_pull_requests.run()
        assert "No" in result


class TestGetPRTool:
    @patch("gitpilot.pr_tools.gp.get_pull_request", new_callable=AsyncMock)
    def test_returns_details(self, mock_get, repo_context):
        mock_get.return_value = {
            "number": 10,
            "title": "My PR",
            "state": "open",
            "mergeable": True,
            "head": {"ref": "feat"},
            "base": {"ref": "main"},
            "user": {"login": "bob"},
            "additions": 50,
            "deletions": 10,
            "changed_files": 3,
            "body": "PR description",
            "html_url": "https://github.com/o/r/pull/10",
        }
        result = get_pull_request.run(pull_number=10)
        assert "#10" in result
        assert "bob" in result
        assert "50" in result


class TestCreatePRTool:
    @patch("gitpilot.pr_tools.gp.create_pull_request", new_callable=AsyncMock)
    def test_creates_pr(self, mock_create, repo_context):
        mock_create.return_value = {
            "number": 20,
            "title": "New Feature",
            "html_url": "https://github.com/o/r/pull/20",
        }
        result = create_pull_request.run(title="New Feature", head="feat", base="main")
        assert "Created PR #20" in result


class TestMergePRTool:
    @patch("gitpilot.pr_tools.gp.merge_pull_request", new_callable=AsyncMock)
    def test_merges_pr(self, mock_merge, repo_context):
        mock_merge.return_value = {"sha": "abc123"}
        result = merge_pull_request.run(pull_number=10)
        assert "merged" in result.lower()
        assert "abc123" in result


class TestListPRFilesTool:
    @patch("gitpilot.pr_tools.gp.list_pr_files", new_callable=AsyncMock)
    def test_lists_files(self, mock_files, repo_context):
        mock_files.return_value = [
            {"filename": "src/main.py", "status": "modified", "additions": 5, "deletions": 2},
        ]
        result = list_pr_files.run(pull_number=10)
        assert "src/main.py" in result
        assert "modified" in result

    @patch("gitpilot.pr_tools.gp.list_pr_files", new_callable=AsyncMock)
    def test_no_files(self, mock_files, repo_context):
        mock_files.return_value = []
        result = list_pr_files.run(pull_number=10)
        assert "No files" in result


class TestReviewTool:
    @patch("gitpilot.pr_tools.gp.create_pr_review", new_callable=AsyncMock)
    def test_creates_review(self, mock_review, repo_context):
        mock_review.return_value = {"html_url": "https://github.com/o/r/pull/10#review"}
        result = create_pr_review.run(pull_number=10, body="LGTM", event="APPROVE")
        assert "Review submitted" in result


class TestCommentTool:
    @patch("gitpilot.pr_tools.gp.add_pr_comment", new_callable=AsyncMock)
    def test_adds_comment(self, mock_comment, repo_context):
        mock_comment.return_value = {"html_url": "https://github.com/o/r/pull/10#comment"}
        result = add_pr_comment.run(pull_number=10, body="Nice work")
        assert "Comment added" in result
