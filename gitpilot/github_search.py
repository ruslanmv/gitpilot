# gitpilot/github_search.py
"""GitHub Search API wrapper.

Provides async functions for searching code, repositories, issues, and users
via GitHub's Search API.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .github_api import github_request


async def search_code(
    query: str,
    *,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    language: Optional[str] = None,
    path: Optional[str] = None,
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for code across GitHub repositories.

    Builds a qualified search query string from the parameters.
    Returns: {total_count, incomplete_results, items[...]}.
    """
    parts = [query]
    if owner and repo:
        parts.append(f"repo:{owner}/{repo}")
    elif owner:
        parts.append(f"user:{owner}")
    if language:
        parts.append(f"language:{language}")
    if path:
        parts.append(f"path:{path}")

    q = " ".join(parts)

    result = await github_request(
        "/search/code",
        params={"q": q, "per_page": min(per_page, 100), "page": page},
        token=token,
    )
    return _normalise_search_result(result)


async def search_issues(
    query: str,
    *,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    state: Optional[str] = None,
    label: Optional[str] = None,
    is_pr: Optional[bool] = None,
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search issues and pull requests."""
    parts = [query]
    if owner and repo:
        parts.append(f"repo:{owner}/{repo}")
    elif owner:
        parts.append(f"user:{owner}")
    if state:
        parts.append(f"state:{state}")
    if label:
        parts.append(f"label:{label}")
    if is_pr is True:
        parts.append("type:pr")
    elif is_pr is False:
        parts.append("type:issue")

    q = " ".join(parts)

    result = await github_request(
        "/search/issues",
        params={"q": q, "per_page": min(per_page, 100), "page": page},
        token=token,
    )
    return _normalise_search_result(result)


async def search_repositories(
    query: str,
    *,
    language: Optional[str] = None,
    sort: Optional[str] = None,
    order: str = "desc",
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for repositories."""
    parts = [query]
    if language:
        parts.append(f"language:{language}")

    q = " ".join(parts)

    params: Dict[str, Any] = {
        "q": q,
        "per_page": min(per_page, 100),
        "page": page,
        "order": order,
    }
    if sort:
        params["sort"] = sort

    result = await github_request("/search/repositories", params=params, token=token)
    return _normalise_search_result(result)


async def search_users(
    query: str,
    *,
    type_filter: Optional[str] = None,
    location: Optional[str] = None,
    language: Optional[str] = None,
    per_page: int = 30,
    page: int = 1,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for users and organizations.

    type_filter: 'user' or 'org' to narrow results.
    """
    parts = [query]
    if type_filter:
        parts.append(f"type:{type_filter}")
    if location:
        parts.append(f"location:{location}")
    if language:
        parts.append(f"language:{language}")

    q = " ".join(parts)

    result = await github_request(
        "/search/users",
        params={"q": q, "per_page": min(per_page, 100), "page": page},
        token=token,
    )
    return _normalise_search_result(result)


def _normalise_search_result(result: Any) -> Dict[str, Any]:
    """Ensure consistent shape even if GitHub returns None."""
    if not isinstance(result, dict):
        return {"total_count": 0, "incomplete_results": False, "items": []}
    return {
        "total_count": result.get("total_count", 0),
        "incomplete_results": result.get("incomplete_results", False),
        "items": result.get("items", []),
    }
