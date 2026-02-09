"""CrewAI tools for GitHub Search operations.

Provides tools for searching code, issues, repositories, and users
across GitHub.
"""
import asyncio
from typing import Optional

from crewai.tools import tool

from .agent_tools import get_repo_context
from . import github_search as gs


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@tool("Search code in repository")
def search_code(
    query: str,
    language: str = "",
    path: str = "",
    per_page: int = 20,
) -> str:
    """Searches for code by keywords, symbols, or patterns. Scoped to the current repository by default. Optional: language filter, path filter."""
    try:
        owner, repo, token, _branch = get_repo_context()
        result = _run_async(
            gs.search_code(
                query,
                owner=owner,
                repo=repo,
                language=language or None,
                path=path or None,
                per_page=per_page,
                token=token,
            )
        )
        items = result.get("items", [])
        total = result.get("total_count", 0)
        if not items:
            return f"No code matches found for '{query}' in {owner}/{repo}."
        lines = [f"Code search results for '{query}' in {owner}/{repo} ({total} total):"]
        for item in items:
            lines.append(
                f"  {item.get('path', '?')} "
                f"(score: {item.get('score', '?')})\n"
                f"    URL: {item.get('html_url', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching code: {e}"


@tool("Search code globally")
def search_code_global(
    query: str,
    language: str = "",
    per_page: int = 10,
) -> str:
    """Searches for code across ALL of GitHub (not scoped to a repo). Use for finding examples or patterns globally."""
    try:
        _owner, _repo, token, _branch = get_repo_context()
        result = _run_async(
            gs.search_code(query, language=language or None, per_page=per_page, token=token)
        )
        items = result.get("items", [])
        total = result.get("total_count", 0)
        if not items:
            return f"No global code matches found for '{query}'."
        lines = [f"Global code search for '{query}' ({total} total):"]
        for item in items:
            repo_name = item.get("repository", {}).get("full_name", "?")
            lines.append(
                f"  [{repo_name}] {item.get('path', '?')}\n"
                f"    URL: {item.get('html_url', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching code globally: {e}"


@tool("Search issues and pull requests")
def search_issues(
    query: str,
    state: str = "",
    label: str = "",
    is_pr: str = "",
    per_page: int = 20,
) -> str:
    """Searches issues/PRs by keywords. Scoped to current repo. is_pr: 'true' for PRs only, 'false' for issues only, empty for both."""
    try:
        owner, repo, token, _branch = get_repo_context()
        pr_flag = None
        if is_pr.lower() == "true":
            pr_flag = True
        elif is_pr.lower() == "false":
            pr_flag = False
        result = _run_async(
            gs.search_issues(
                query,
                owner=owner,
                repo=repo,
                state=state or None,
                label=label or None,
                is_pr=pr_flag,
                per_page=per_page,
                token=token,
            )
        )
        items = result.get("items", [])
        total = result.get("total_count", 0)
        if not items:
            return f"No matching issues/PRs for '{query}' in {owner}/{repo}."
        lines = [f"Issue/PR search for '{query}' ({total} total):"]
        for item in items:
            kind = "PR" if "pull_request" in item else "Issue"
            lines.append(
                f"  [{kind}] #{item.get('number')} [{item.get('state')}] {item.get('title')}\n"
                f"    URL: {item.get('html_url', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching issues: {e}"


@tool("Search GitHub users and organizations")
def search_users(
    query: str,
    type_filter: str = "",
    location: str = "",
    language: str = "",
    per_page: int = 10,
) -> str:
    """Searches for GitHub users or organizations. type_filter: 'user' or 'org'. Optional: location, language."""
    try:
        _owner, _repo, token, _branch = get_repo_context()
        result = _run_async(
            gs.search_users(
                query,
                type_filter=type_filter or None,
                location=location or None,
                language=language or None,
                per_page=per_page,
                token=token,
            )
        )
        items = result.get("items", [])
        total = result.get("total_count", 0)
        if not items:
            return f"No users/orgs found for '{query}'."
        lines = [f"User search for '{query}' ({total} total):"]
        for item in items:
            lines.append(
                f"  @{item.get('login', '?')} ({item.get('type', '?')})\n"
                f"    URL: {item.get('html_url', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching users: {e}"


@tool("Search repositories")
def search_repositories(
    query: str,
    language: str = "",
    sort: str = "",
    per_page: int = 10,
) -> str:
    """Searches for repositories across GitHub. Optional: language filter, sort (stars/forks/updated)."""
    try:
        _owner, _repo, token, _branch = get_repo_context()
        result = _run_async(
            gs.search_repositories(
                query,
                language=language or None,
                sort=sort or None,
                per_page=per_page,
                token=token,
            )
        )
        items = result.get("items", [])
        total = result.get("total_count", 0)
        if not items:
            return f"No repositories found for '{query}'."
        lines = [f"Repository search for '{query}' ({total} total):"]
        for item in items:
            lines.append(
                f"  {item.get('full_name', '?')} "
                f"({item.get('stargazers_count', 0)} stars)\n"
                f"    {item.get('description', 'No description')[:100]}\n"
                f"    URL: {item.get('html_url', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching repositories: {e}"


# Export all search tools
SEARCH_TOOLS = [
    search_code,
    search_code_global,
    search_issues,
    search_users,
    search_repositories,
]
