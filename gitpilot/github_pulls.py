# gitpilot/github_pulls.py
"""GitHub Pull Requests API wrapper.

Provides async functions for creating, listing, reviewing, and merging
pull requests.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .github_api import github_request


# ---------------------------------------------------------------------------
# PR CRUD
# ---------------------------------------------------------------------------

async def list_pull_requests(
    owner: str,
    repo: str,
    *,
    state: str = "open",
    sort: str = "created",
    direction: str = "desc",
    head: Optional[str] = None,
    base: Optional[str] = None,
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List pull requests with optional filters."""
    params: Dict[str, Any] = {
        "state": state,
        "sort": sort,
        "direction": direction,
        "per_page": min(per_page, 100),
        "page": page,
    }
    if head:
        params["head"] = head
    if base:
        params["base"] = base

    return await github_request(
        f"/repos/{owner}/{repo}/pulls",
        params=params,
        token=token,
    ) or []


async def get_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a single pull request."""
    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}",
        token=token,
    )


async def create_pull_request(
    owner: str,
    repo: str,
    *,
    title: str,
    head: str,
    base: str,
    body: Optional[str] = None,
    draft: bool = False,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new pull request."""
    payload: Dict[str, Any] = {
        "title": title,
        "head": head,
        "base": base,
        "draft": draft,
    }
    if body is not None:
        payload["body"] = body

    return await github_request(
        f"/repos/{owner}/{repo}/pulls",
        method="POST",
        json=payload,
        token=token,
    )


async def update_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    base: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing pull request."""
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if base is not None:
        payload["base"] = base

    if not payload:
        return await get_pull_request(owner, repo, pull_number, token=token)

    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}",
        method="PATCH",
        json=payload,
        token=token,
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

async def merge_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    *,
    commit_title: Optional[str] = None,
    commit_message: Optional[str] = None,
    merge_method: str = "merge",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge a pull request.

    merge_method: one of 'merge', 'squash', 'rebase'.
    """
    payload: Dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title
    if commit_message:
        payload["commit_message"] = commit_message

    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}/merge",
        method="PUT",
        json=payload,
        token=token,
    )


# ---------------------------------------------------------------------------
# PR Files & Diff
# ---------------------------------------------------------------------------

async def list_pr_files(
    owner: str,
    repo: str,
    pull_number: int,
    *,
    per_page: int = 100,
    page: int = 1,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List files changed in a pull request."""
    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}/files",
        params={"per_page": min(per_page, 100), "page": page},
        token=token,
    ) or []


# ---------------------------------------------------------------------------
# PR Reviews & Comments
# ---------------------------------------------------------------------------

async def list_pr_reviews(
    owner: str,
    repo: str,
    pull_number: int,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List reviews on a pull request."""
    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        token=token,
    ) or []


async def create_pr_review(
    owner: str,
    repo: str,
    pull_number: int,
    *,
    body: str,
    event: str = "COMMENT",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a review on a pull request.

    event: one of 'APPROVE', 'REQUEST_CHANGES', 'COMMENT'.
    """
    return await github_request(
        f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        method="POST",
        json={"body": body, "event": event},
        token=token,
    )


async def add_pr_comment(
    owner: str,
    repo: str,
    pull_number: int,
    body: str,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a general comment to a pull request (via issues API)."""
    return await github_request(
        f"/repos/{owner}/{repo}/issues/{pull_number}/comments",
        method="POST",
        json={"body": body},
        token=token,
    )
