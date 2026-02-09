"""CrewAI tools for GitHub Issue management.

These tools allow agents to create, list, update, and comment on GitHub issues.
They reuse the repo context mechanism from agent_tools.
"""
import asyncio
import json
from typing import Optional

from crewai.tools import tool

from .agent_tools import get_repo_context
from . import github_issues as gi


def _run_async(coro):
    """Run an async coroutine from a sync CrewAI tool."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fmt_issue(issue: dict) -> str:
    labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
    assignees = ", ".join(a.get("login", "") for a in issue.get("assignees", []))
    return (
        f"#{issue.get('number')} [{issue.get('state', 'open')}] "
        f"{issue.get('title', '')}\n"
        f"  Labels: {labels or 'none'} | Assignees: {assignees or 'none'}\n"
        f"  URL: {issue.get('html_url', '')}"
    )


@tool("List repository issues")
def list_issues(
    state: str = "open",
    labels: Optional[str] = None,
    per_page: int = 20,
) -> str:
    """Lists issues in the current repository. Optional filters: state (open/closed/all), labels (comma-separated), per_page."""
    try:
        owner, repo, token, _branch = get_repo_context()
        issues = _run_async(
            gi.list_issues(owner, repo, state=state, labels=labels, per_page=per_page, token=token)
        )
        if not issues:
            return f"No {state} issues found in {owner}/{repo}."
        header = f"Issues in {owner}/{repo} (state={state}):\n"
        return header + "\n".join(_fmt_issue(i) for i in issues)
    except Exception as e:
        return f"Error listing issues: {e}"


@tool("Get issue details")
def get_issue(issue_number: int) -> str:
    """Gets full details of a specific issue by number."""
    try:
        owner, repo, token, _branch = get_repo_context()
        issue = _run_async(gi.get_issue(owner, repo, issue_number, token=token))
        body = (issue.get("body") or "")[:500]
        return (
            f"Issue #{issue.get('number')}: {issue.get('title')}\n"
            f"State: {issue.get('state')} | Created: {issue.get('created_at')}\n"
            f"Author: {issue.get('user', {}).get('login', 'unknown')}\n"
            f"Body:\n{body}\n"
            f"URL: {issue.get('html_url', '')}"
        )
    except Exception as e:
        return f"Error getting issue: {e}"


@tool("Create a new issue")
def create_issue(
    title: str,
    body: str = "",
    labels: str = "",
    assignees: str = "",
) -> str:
    """Creates a new GitHub issue. labels and assignees are comma-separated strings."""
    try:
        owner, repo, token, _branch = get_repo_context()
        label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else None
        assignee_list = [a.strip() for a in assignees.split(",") if a.strip()] if assignees else None
        issue = _run_async(
            gi.create_issue(owner, repo, title, body=body or None, labels=label_list, assignees=assignee_list, token=token)
        )
        return f"Created issue #{issue.get('number')}: {issue.get('title')}\nURL: {issue.get('html_url', '')}"
    except Exception as e:
        return f"Error creating issue: {e}"


@tool("Update an issue")
def update_issue(
    issue_number: int,
    title: str = "",
    body: str = "",
    state: str = "",
    labels: str = "",
    assignees: str = "",
) -> str:
    """Updates an existing issue. Only non-empty fields are changed. labels/assignees are comma-separated."""
    try:
        owner, repo, token, _branch = get_repo_context()
        kwargs: dict = {}
        if title:
            kwargs["title"] = title
        if body:
            kwargs["body"] = body
        if state:
            kwargs["state"] = state
        if labels:
            kwargs["labels"] = [l.strip() for l in labels.split(",") if l.strip()]
        if assignees:
            kwargs["assignees"] = [a.strip() for a in assignees.split(",") if a.strip()]
        issue = _run_async(gi.update_issue(owner, repo, issue_number, token=token, **kwargs))
        return f"Updated issue #{issue.get('number')}: {issue.get('title')}\nState: {issue.get('state')}"
    except Exception as e:
        return f"Error updating issue: {e}"


@tool("Add a comment to an issue")
def add_issue_comment(issue_number: int, body: str) -> str:
    """Adds a comment to an existing issue."""
    try:
        owner, repo, token, _branch = get_repo_context()
        comment = _run_async(gi.add_issue_comment(owner, repo, issue_number, body, token=token))
        return f"Comment added to issue #{issue_number}\nURL: {comment.get('html_url', '')}"
    except Exception as e:
        return f"Error adding comment: {e}"


@tool("List issue comments")
def list_issue_comments(issue_number: int) -> str:
    """Lists all comments on an issue."""
    try:
        owner, repo, token, _branch = get_repo_context()
        comments = _run_async(gi.list_issue_comments(owner, repo, issue_number, token=token))
        if not comments:
            return f"No comments on issue #{issue_number}."
        lines = [f"Comments on issue #{issue_number}:"]
        for c in comments:
            author = c.get("user", {}).get("login", "unknown")
            body_preview = (c.get("body") or "")[:200]
            lines.append(f"  [{author}] {body_preview}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing comments: {e}"


# Export all issue tools
ISSUE_TOOLS = [
    list_issues,
    get_issue,
    create_issue,
    update_issue,
    add_issue_comment,
    list_issue_comments,
]
