# gitpilot/github_issues.py
"""GitHub Issues API wrapper.

Provides async functions for creating, reading, updating, and managing
GitHub issues including labels, assignees, milestones, and comments.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .github_api import github_request


# ---------------------------------------------------------------------------
# Issue CRUD
# ---------------------------------------------------------------------------

async def list_issues(
    owner: str,
    repo: str,
    *,
    state: str = "open",
    labels: Optional[str] = None,
    assignee: Optional[str] = None,
    milestone: Optional[str] = None,
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List issues for a repository with optional filters."""
    params: Dict[str, Any] = {
        "state": state,
        "sort": sort,
        "direction": direction,
        "per_page": min(per_page, 100),
        "page": page,
    }
    if labels:
        params["labels"] = labels
    if assignee:
        params["assignee"] = assignee
    if milestone:
        params["milestone"] = milestone

    data = await github_request(
        f"/repos/{owner}/{repo}/issues",
        params=params,
        token=token,
    )
    # GitHub's issues endpoint also returns PRs; filter them out
    return [i for i in (data or []) if "pull_request" not in i]


async def get_issue(
    owner: str,
    repo: str,
    issue_number: int,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a single issue by number."""
    return await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        token=token,
    )


async def create_issue(
    owner: str,
    repo: str,
    title: str,
    *,
    body: Optional[str] = None,
    labels: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    milestone: Optional[int] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new issue."""
    payload: Dict[str, Any] = {"title": title}
    if body is not None:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    if milestone is not None:
        payload["milestone"] = milestone

    return await github_request(
        f"/repos/{owner}/{repo}/issues",
        method="POST",
        json=payload,
        token=token,
    )


async def update_issue(
    owner: str,
    repo: str,
    issue_number: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    state: Optional[str] = None,
    labels: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    milestone: Optional[int] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing issue (title, body, state, labels, assignees, milestone)."""
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if assignees is not None:
        payload["assignees"] = assignees
    if milestone is not None:
        payload["milestone"] = milestone

    if not payload:
        return await get_issue(owner, repo, issue_number, token=token)

    return await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        method="PATCH",
        json=payload,
        token=token,
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

async def list_issue_comments(
    owner: str,
    repo: str,
    issue_number: int,
    *,
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List comments on an issue."""
    return await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        params={"per_page": min(per_page, 100), "page": page},
        token=token,
    ) or []


async def add_issue_comment(
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a comment to an issue."""
    return await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        method="POST",
        json={"body": body},
        token=token,
    )


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

async def add_labels(
    owner: str,
    repo: str,
    issue_number: int,
    labels: List[str],
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Add labels to an issue."""
    return await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
        method="POST",
        json={"labels": labels},
        token=token,
    ) or []


async def remove_label(
    owner: str,
    repo: str,
    issue_number: int,
    label: str,
    token: Optional[str] = None,
) -> None:
    """Remove a single label from an issue."""
    await github_request(
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
        method="DELETE",
        token=token,
    )


# ---------------------------------------------------------------------------
# Assignees
# ---------------------------------------------------------------------------

async def set_assignees(
    owner: str,
    repo: str,
    issue_number: int,
    assignees: List[str],
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Replace assignees on an issue."""
    return await update_issue(
        owner, repo, issue_number, assignees=assignees, token=token,
    )
