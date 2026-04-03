"""Tests for gitpilot.github_search module."""
from __future__ import annotations

import pytest

from gitpilot import github_search as gs


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_code_scoped(mock_github_request):
    mock_github_request.return_value = {
        "total_count": 1,
        "incomplete_results": False,
        "items": [{"path": "src/main.py"}],
    }
    result = await gs.search_code("def hello", owner="o", repo="r", token="tok")
    assert result["total_count"] == 1
    assert len(result["items"]) == 1
    params = mock_github_request.call_args.kwargs.get("params")
    assert "repo:o/r" in params["q"]


@pytest.mark.asyncio
async def test_search_code_with_language(mock_github_request):
    mock_github_request.return_value = {"total_count": 0, "items": []}
    await gs.search_code("import", language="python", token="tok")
    params = mock_github_request.call_args.kwargs.get("params")
    assert "language:python" in params["q"]


@pytest.mark.asyncio
async def test_search_code_normalise_none(mock_github_request):
    """When github returns None, should normalise to safe dict."""
    mock_github_request.return_value = None
    result = await gs.search_code("x", token="tok")
    assert result["total_count"] == 0
    assert result["items"] == []


# ---------------------------------------------------------------------------
# search_issues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_issues_basic(mock_github_request):
    mock_github_request.return_value = {
        "total_count": 2,
        "items": [{"number": 1}, {"number": 2}],
    }
    result = await gs.search_issues("bug", owner="o", repo="r", token="tok")
    assert result["total_count"] == 2


@pytest.mark.asyncio
async def test_search_issues_filters(mock_github_request):
    mock_github_request.return_value = {"total_count": 0, "items": []}
    await gs.search_issues(
        "crash", state="open", label="critical", is_pr=False, token="tok",
    )
    params = mock_github_request.call_args.kwargs.get("params")
    assert "state:open" in params["q"]
    assert "label:critical" in params["q"]
    assert "type:issue" in params["q"]


@pytest.mark.asyncio
async def test_search_issues_pr_filter(mock_github_request):
    mock_github_request.return_value = {"total_count": 0, "items": []}
    await gs.search_issues("fix", is_pr=True, token="tok")
    params = mock_github_request.call_args.kwargs.get("params")
    assert "type:pr" in params["q"]


# ---------------------------------------------------------------------------
# search_repositories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_repositories(mock_github_request):
    mock_github_request.return_value = {
        "total_count": 5,
        "items": [{"full_name": "o/r"}],
    }
    result = await gs.search_repositories("gitpilot", token="tok")
    assert result["total_count"] == 5


@pytest.mark.asyncio
async def test_search_repositories_with_sort(mock_github_request):
    mock_github_request.return_value = {"total_count": 0, "items": []}
    await gs.search_repositories("ai", sort="stars", language="python", token="tok")
    params = mock_github_request.call_args.kwargs.get("params")
    assert params["sort"] == "stars"
    assert "language:python" in params["q"]


# ---------------------------------------------------------------------------
# search_users
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_users(mock_github_request):
    mock_github_request.return_value = {
        "total_count": 1,
        "items": [{"login": "alice", "type": "User"}],
    }
    result = await gs.search_users("alice", token="tok")
    assert result["items"][0]["login"] == "alice"


@pytest.mark.asyncio
async def test_search_users_filters(mock_github_request):
    mock_github_request.return_value = {"total_count": 0, "items": []}
    await gs.search_users(
        "dev", type_filter="org", location="Berlin", language="python", token="tok",
    )
    params = mock_github_request.call_args.kwargs.get("params")
    assert "type:org" in params["q"]
    assert "location:Berlin" in params["q"]
    assert "language:python" in params["q"]


# ---------------------------------------------------------------------------
# _normalise_search_result
# ---------------------------------------------------------------------------

def test_normalise_none():
    assert gs._normalise_search_result(None) == {
        "total_count": 0,
        "incomplete_results": False,
        "items": [],
    }


def test_normalise_string():
    assert gs._normalise_search_result("bad") == {
        "total_count": 0,
        "incomplete_results": False,
        "items": [],
    }
