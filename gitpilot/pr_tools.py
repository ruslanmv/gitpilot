"""CrewAI tools for GitHub Pull Request management.

These tools allow agents to create, list, review, and merge pull requests.
"""
import asyncio
from typing import Optional

from crewai.tools import tool

from .agent_tools import get_repo_context
from . import github_pulls as gp


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fmt_pr(pr: dict) -> str:
    return (
        f"#{pr.get('number')} [{pr.get('state')}] {pr.get('title')}\n"
        f"  {pr.get('head', {}).get('ref', '?')} -> {pr.get('base', {}).get('ref', '?')}\n"
        f"  Author: {pr.get('user', {}).get('login', 'unknown')} | "
        f"Draft: {pr.get('draft', False)}\n"
        f"  URL: {pr.get('html_url', '')}"
    )


@tool("List pull requests")
def list_pull_requests(state: str = "open", per_page: int = 20) -> str:
    """Lists pull requests in the current repository. state: open/closed/all."""
    try:
        owner, repo, token, _branch = get_repo_context()
        prs = _run_async(
            gp.list_pull_requests(owner, repo, state=state, per_page=per_page, token=token)
        )
        if not prs:
            return f"No {state} pull requests in {owner}/{repo}."
        header = f"Pull requests in {owner}/{repo} (state={state}):\n"
        return header + "\n".join(_fmt_pr(p) for p in prs)
    except Exception as e:
        return f"Error listing PRs: {e}"


@tool("Get pull request details")
def get_pull_request(pull_number: int) -> str:
    """Gets full details of a pull request by number."""
    try:
        owner, repo, token, _branch = get_repo_context()
        pr = _run_async(gp.get_pull_request(owner, repo, pull_number, token=token))
        body = (pr.get("body") or "")[:500]
        return (
            f"PR #{pr.get('number')}: {pr.get('title')}\n"
            f"State: {pr.get('state')} | Mergeable: {pr.get('mergeable')}\n"
            f"Head: {pr.get('head', {}).get('ref')} -> Base: {pr.get('base', {}).get('ref')}\n"
            f"Author: {pr.get('user', {}).get('login', 'unknown')}\n"
            f"Additions: {pr.get('additions', 0)} | Deletions: {pr.get('deletions', 0)} | "
            f"Changed files: {pr.get('changed_files', 0)}\n"
            f"Body:\n{body}\n"
            f"URL: {pr.get('html_url', '')}"
        )
    except Exception as e:
        return f"Error getting PR: {e}"


@tool("Create a pull request")
def create_pull_request(
    title: str,
    head: str,
    base: str,
    body: str = "",
    draft: bool = False,
) -> str:
    """Creates a new pull request. head=source branch, base=target branch."""
    try:
        owner, repo, token, _branch = get_repo_context()
        pr = _run_async(
            gp.create_pull_request(
                owner, repo, title=title, head=head, base=base,
                body=body or None, draft=draft, token=token,
            )
        )
        return (
            f"Created PR #{pr.get('number')}: {pr.get('title')}\n"
            f"URL: {pr.get('html_url', '')}"
        )
    except Exception as e:
        return f"Error creating PR: {e}"


@tool("Merge a pull request")
def merge_pull_request(
    pull_number: int,
    merge_method: str = "merge",
    commit_title: str = "",
) -> str:
    """Merges a pull request. merge_method: merge, squash, or rebase."""
    try:
        owner, repo, token, _branch = get_repo_context()
        result = _run_async(
            gp.merge_pull_request(
                owner, repo, pull_number,
                merge_method=merge_method,
                commit_title=commit_title or None,
                token=token,
            )
        )
        sha = result.get("sha", "unknown") if isinstance(result, dict) else "unknown"
        return f"PR #{pull_number} merged successfully. Merge commit: {sha}"
    except Exception as e:
        return f"Error merging PR: {e}"


@tool("List files changed in a pull request")
def list_pr_files(pull_number: int) -> str:
    """Lists all files changed in a pull request with status and patch info."""
    try:
        owner, repo, token, _branch = get_repo_context()
        files = _run_async(gp.list_pr_files(owner, repo, pull_number, token=token))
        if not files:
            return f"No files changed in PR #{pull_number}."
        lines = [f"Files changed in PR #{pull_number}:"]
        for f in files:
            lines.append(
                f"  [{f.get('status', '?')}] {f.get('filename', '?')} "
                f"(+{f.get('additions', 0)} -{f.get('deletions', 0)})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing PR files: {e}"


@tool("Add a review to a pull request")
def create_pr_review(
    pull_number: int,
    body: str,
    event: str = "COMMENT",
) -> str:
    """Adds a review to a PR. event: APPROVE, REQUEST_CHANGES, or COMMENT."""
    try:
        owner, repo, token, _branch = get_repo_context()
        review = _run_async(
            gp.create_pr_review(owner, repo, pull_number, body=body, event=event, token=token)
        )
        return f"Review submitted on PR #{pull_number} (event={event})\nURL: {review.get('html_url', '')}"
    except Exception as e:
        return f"Error creating review: {e}"


@tool("Comment on a pull request")
def add_pr_comment(pull_number: int, body: str) -> str:
    """Adds a general comment to a pull request."""
    try:
        owner, repo, token, _branch = get_repo_context()
        comment = _run_async(gp.add_pr_comment(owner, repo, pull_number, body, token=token))
        return f"Comment added to PR #{pull_number}\nURL: {comment.get('html_url', '')}"
    except Exception as e:
        return f"Error commenting on PR: {e}"


# Export all PR tools
PR_TOOLS = [
    list_pull_requests,
    get_pull_request,
    create_pull_request,
    merge_pull_request,
    list_pr_files,
    create_pr_review,
    add_pr_comment,
]
