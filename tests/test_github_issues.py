"""Tests for gitpilot.github_issues module."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gitpilot import github_issues as gi


# ---------------------------------------------------------------------------
# list_issues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_issues_filters_out_prs(mock_github_request):
    """list_issues should exclude items that have a pull_request key."""
    mock_github_request.return_value = [
        {"number": 1, "title": "Bug report"},
        {"number": 2, "title": "Feature PR", "pull_request": {"url": "..."}},
        {"number": 3, "title": "Another issue"},
    ]

    issues = await gi.list_issues("owner", "repo", token="tok")

    assert len(issues) == 2
    assert issues[0]["number"] == 1
    assert issues[1]["number"] == 3


@pytest.mark.asyncio
async def test_list_issues_empty_repo(mock_github_request):
    mock_github_request.return_value = []
    issues = await gi.list_issues("owner", "repo", token="tok")
    assert issues == []


@pytest.mark.asyncio
async def test_list_issues_passes_params(mock_github_request):
    mock_github_request.return_value = []
    await gi.list_issues(
        "o", "r", state="closed", labels="bug,ui", assignee="alice",
        per_page=10, page=2, token="tok",
    )
    _call = mock_github_request.call_args
    params = _call.kwargs.get("params") or _call[1].get("params")
    assert params["state"] == "closed"
    assert params["labels"] == "bug,ui"
    assert params["assignee"] == "alice"
    assert params["per_page"] == 10
    assert params["page"] == 2


# ---------------------------------------------------------------------------
# get_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_issue(mock_github_request):
    mock_github_request.return_value = {"number": 42, "title": "Test"}
    issue = await gi.get_issue("o", "r", 42, token="tok")
    assert issue["number"] == 42
    mock_github_request.assert_called_once_with(
        "/repos/o/r/issues/42", token="tok",
    )


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_issue_minimal(mock_github_request):
    mock_github_request.return_value = {"number": 99, "title": "New issue"}
    result = await gi.create_issue("o", "r", "New issue", token="tok")
    assert result["number"] == 99
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["title"] == "New issue"
    assert "body" not in call_json


@pytest.mark.asyncio
async def test_create_issue_full(mock_github_request):
    mock_github_request.return_value = {"number": 100}
    await gi.create_issue(
        "o", "r", "Title", body="desc", labels=["bug"], assignees=["alice"],
        milestone=5, token="tok",
    )
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["body"] == "desc"
    assert call_json["labels"] == ["bug"]
    assert call_json["assignees"] == ["alice"]
    assert call_json["milestone"] == 5


# ---------------------------------------------------------------------------
# update_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_issue_partial(mock_github_request):
    mock_github_request.return_value = {"number": 10, "title": "Updated"}
    result = await gi.update_issue("o", "r", 10, title="Updated", token="tok")
    assert result["title"] == "Updated"
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json == {"title": "Updated"}


@pytest.mark.asyncio
async def test_update_issue_no_fields_returns_get(mock_github_request):
    """When no fields are given, update_issue should just get the issue."""
    mock_github_request.return_value = {"number": 10}
    await gi.update_issue("o", "r", 10, token="tok")
    # Should have called GET (no method arg = GET by default)
    mock_github_request.assert_called_once_with(
        "/repos/o/r/issues/10", token="tok",
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_issue_comments(mock_github_request):
    mock_github_request.return_value = [{"id": 1, "body": "hello"}]
    comments = await gi.list_issue_comments("o", "r", 5, token="tok")
    assert len(comments) == 1


@pytest.mark.asyncio
async def test_add_issue_comment(mock_github_request):
    mock_github_request.return_value = {"id": 2, "body": "reply"}
    result = await gi.add_issue_comment("o", "r", 5, "reply", token="tok")
    assert result["body"] == "reply"
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json == {"body": "reply"}


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_labels(mock_github_request):
    mock_github_request.return_value = [{"name": "bug"}]
    result = await gi.add_labels("o", "r", 1, ["bug"], token="tok")
    assert result[0]["name"] == "bug"


@pytest.mark.asyncio
async def test_remove_label(mock_github_request):
    mock_github_request.return_value = None
    await gi.remove_label("o", "r", 1, "bug", token="tok")
    mock_github_request.assert_called_once()
