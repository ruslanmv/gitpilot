"""Tests for gitpilot.github_pulls module."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gitpilot import github_pulls as gp


# ---------------------------------------------------------------------------
# list_pull_requests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_prs_default(mock_github_request):
    mock_github_request.return_value = [{"number": 1}, {"number": 2}]
    prs = await gp.list_pull_requests("o", "r", token="tok")
    assert len(prs) == 2


@pytest.mark.asyncio
async def test_list_prs_passes_filters(mock_github_request):
    mock_github_request.return_value = []
    await gp.list_pull_requests(
        "o", "r", state="closed", head="feat", base="main",
        per_page=5, page=3, token="tok",
    )
    params = mock_github_request.call_args.kwargs.get("params")
    assert params["state"] == "closed"
    assert params["head"] == "feat"
    assert params["base"] == "main"
    assert params["per_page"] == 5
    assert params["page"] == 3


@pytest.mark.asyncio
async def test_list_prs_none_returns_empty(mock_github_request):
    mock_github_request.return_value = None
    prs = await gp.list_pull_requests("o", "r", token="tok")
    assert prs == []


# ---------------------------------------------------------------------------
# get_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_pull_request(mock_github_request):
    mock_github_request.return_value = {"number": 10, "title": "My PR"}
    pr = await gp.get_pull_request("o", "r", 10, token="tok")
    assert pr["number"] == 10


# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_pr(mock_github_request):
    mock_github_request.return_value = {"number": 20, "html_url": "..."}
    result = await gp.create_pull_request(
        "o", "r", title="Feature", head="feat", base="main", token="tok",
    )
    assert result["number"] == 20
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["title"] == "Feature"
    assert call_json["head"] == "feat"
    assert call_json["base"] == "main"
    assert call_json["draft"] is False


@pytest.mark.asyncio
async def test_create_pr_draft(mock_github_request):
    mock_github_request.return_value = {"number": 21}
    await gp.create_pull_request(
        "o", "r", title="WIP", head="wip", base="main", draft=True, token="tok",
    )
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["draft"] is True


# ---------------------------------------------------------------------------
# update_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_pr(mock_github_request):
    mock_github_request.return_value = {"number": 10, "title": "Updated"}
    result = await gp.update_pull_request("o", "r", 10, title="Updated", token="tok")
    assert result["title"] == "Updated"


@pytest.mark.asyncio
async def test_update_pr_no_fields(mock_github_request):
    """Empty update -> just fetches the PR."""
    mock_github_request.return_value = {"number": 10}
    await gp.update_pull_request("o", "r", 10, token="tok")
    # Called with GET endpoint (no method kwarg)
    mock_github_request.assert_called_once_with(
        "/repos/o/r/pulls/10", token="tok",
    )


# ---------------------------------------------------------------------------
# merge_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_pr(mock_github_request):
    mock_github_request.return_value = {"sha": "abc123", "merged": True}
    result = await gp.merge_pull_request("o", "r", 10, merge_method="squash", token="tok")
    assert result["merged"] is True
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["merge_method"] == "squash"


# ---------------------------------------------------------------------------
# list_pr_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pr_files(mock_github_request):
    mock_github_request.return_value = [
        {"filename": "a.py", "status": "added", "additions": 10, "deletions": 0},
    ]
    files = await gp.list_pr_files("o", "r", 10, token="tok")
    assert len(files) == 1
    assert files[0]["filename"] == "a.py"


# ---------------------------------------------------------------------------
# Reviews & comments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_pr_review(mock_github_request):
    mock_github_request.return_value = {"id": 1}
    await gp.create_pr_review("o", "r", 10, body="LGTM", event="APPROVE", token="tok")
    call_json = mock_github_request.call_args.kwargs.get("json")
    assert call_json["body"] == "LGTM"
    assert call_json["event"] == "APPROVE"


@pytest.mark.asyncio
async def test_add_pr_comment(mock_github_request):
    mock_github_request.return_value = {"id": 2, "body": "Thanks"}
    result = await gp.add_pr_comment("o", "r", 10, "Thanks", token="tok")
    assert result["body"] == "Thanks"
