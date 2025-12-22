from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

GITHUB_API_BASE = "https://api.github.com"

# Context variable to store the GitHub token for the current request/execution scope
_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_token", default=None)


@contextmanager
def execution_context(token: Optional[str]):
    """
    Context manager to set the GitHub token for the current execution scope.
    Usage:
        with execution_context(token):
            # Code here can access the token via _github_token()
    """
    token_var = _request_token.set(token)
    try:
        yield
    finally:
        _request_token.reset(token_var)


def _github_token(provided_token: Optional[str] = None) -> str:
    """
    Get GitHub token from:
    1. Explicit argument
    2. Request Context (set via execution_context)
    3. Environment variables (Fallback)

    Args:
        provided_token: Optional token from Authorization header

    Returns:
        GitHub access token

    Raises:
        HTTPException: If no token is available
    """
    if provided_token:
        return provided_token

    ctx_token = _request_token.get()
    if ctx_token:
        return ctx_token

    token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "GitHub authentication required. "
                "Please log in via the UI or set GITPILOT_GITHUB_TOKEN in your environment."
            ),
        )
    return token


async def github_request(
    path: str,
    *,
    method: str = "GET",
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Any:
    github_token = _github_token(token)

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitpilot",
    }

    async with httpx.AsyncClient(base_url=GITHUB_API_BASE, headers=headers) as client:
        resp = await client.request(method, path, json=json, params=params)

    if resp.status_code >= 400:
        try:
            data = resp.json()
            msg = data.get("message") or resp.text
        except Exception:
            msg = resp.text
        
        if resp.status_code == 401:
             msg = "GitHub Token Expired or Invalid. Please refresh your login."
             
        raise HTTPException(status_code=resp.status_code, detail=msg)

    if resp.status_code == 204:
        return None
    return resp.json()


async def list_user_repos(query: str | None = None, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Legacy function - fetches first 100 repos.
    Use list_user_repos_paginated() for pagination support.
    """
    params = {
        "per_page": 100,
        "affiliation": "owner,collaborator,organization_member",
        "sort": "updated",
        "direction": "desc",
    }
    data = await github_request("/user/repos", params=params, token=token)
    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "owner": r["owner"]["login"],
        }
        for r in data
    ]

    if query:
        q = query.lower()
        repos = [r for r in repos if q in r["full_name"].lower()]
    return repos


async def list_user_repos_paginated(
    page: int = 1,
    per_page: int = 100,
    token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch user repositories with pagination support.
    
    Returns:
        {
            "repositories": [...],
            "page": int,
            "per_page": int,
            "has_more": bool,
        }
    """
    params = {
        "page": page,
        "per_page": min(per_page, 100),  # GitHub max is 100
        "affiliation": "owner,collaborator,organization_member",
        "sort": "updated",
        "direction": "desc",
    }
    
    # Make request and get response with headers
    github_token = _github_token(token)
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitpilot",
    }
    
    async with httpx.AsyncClient(base_url=GITHUB_API_BASE, headers=headers) as client:
        resp = await client.get("/user/repos", params=params)
    
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    
    data = resp.json()
    
    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "owner": r["owner"]["login"],
        }
        for r in data
    ]
    
    # Check Link header for pagination
    link_header = resp.headers.get("Link", "")
    has_more = 'rel="next"' in link_header
    
    return {
        "repositories": repos,
        "page": page,
        "per_page": per_page,
        "has_more": has_more,
    }


async def search_user_repos(
    query: str,
    page: int = 1,
    per_page: int = 100,
    token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Search across ALL user repositories, then return paginated results.
    
    This function:
    1. Fetches ALL repositories (may make multiple API calls)
    2. Filters them by the search query
    3. Returns a paginated subset of filtered results
    
    Args:
        query: Search string (searches in repo name and full_name)
        page: Page number for results
        per_page: Results per page
        token: GitHub token
    
    Returns:
        {
            "repositories": [...],  # Paginated filtered results
            "page": int,
            "per_page": int,
            "total_count": int,    # Total matching repos
            "has_more": bool,       # More pages available
        }
    """
    # Fetch ALL repositories (across all pages)
    all_repos = []
    fetch_page = 1
    max_pages = 15  # Safety limit: max 1500 repos
    
    while fetch_page <= max_pages:
        result = await list_user_repos_paginated(
            page=fetch_page,
            per_page=100,
            token=token
        )
        
        all_repos.extend(result["repositories"])
        
        if not result["has_more"]:
            break
        
        fetch_page += 1
    
    # Filter repositories by query
    query_lower = query.lower()
    filtered_repos = [
        r for r in all_repos
        if query_lower in r["name"].lower() or query_lower in r["full_name"].lower()
    ]
    
    total_count = len(filtered_repos)
    
    # Calculate pagination for filtered results
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    
    paginated_repos = filtered_repos[start_idx:end_idx]
    has_more = end_idx < total_count
    
    return {
        "repositories": paginated_repos,
        "page": page,
        "per_page": per_page,
        "total_count": total_count,
        "has_more": has_more,
    }


async def get_repo(owner: str, repo: str, token: Optional[str] = None) -> dict[str, Any]:
    """
    Get repository information including default branch.

    Returns:
        Repository data including default_branch field
    """
    return await github_request(f"/repos/{owner}/{repo}", token=token)


async def create_branch(
    owner: str,
    repo: str,
    new_branch: str,
    from_ref: str = "HEAD",
    token: Optional[str] = None,
) -> str:
    """
    Create a new branch in the repository.

    Args:
        owner: Repository owner
        repo: Repository name
        new_branch: Name of the new branch to create
        from_ref: Reference to branch from (default: HEAD = default branch)
        token: GitHub token

    Returns:
        The ref string of the created branch

    Raises:
        HTTPException: If branch creation fails
    """
    # 1. Resolve from_ref to a SHA
    if from_ref == "HEAD":
        repo_data = await get_repo(owner, repo, token=token)
        base_branch = repo_data.get("default_branch", "main")
    else:
        base_branch = from_ref

    # Get the commit SHA of the base branch
    ref_data = await github_request(
        f"/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
        token=token,
    )
    base_sha = ref_data["object"]["sha"]

    # 2. Create new branch ref
    body = {
        "ref": f"refs/heads/{new_branch}",
        "sha": base_sha,
    }
    new_ref = await github_request(
        f"/repos/{owner}/{repo}/git/refs",
        method="POST",
        json=body,
        token=token,
    )
    return new_ref["ref"]


async def get_repo_tree(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    ref: str = "HEAD",
) -> list[dict[str, str]]:
    """
    Get the file tree of a repository at a specific ref.

    Args:
        owner: Repository owner
        repo: Repository name
        token: GitHub token
        ref: Git reference (branch name, tag, or SHA). Defaults to HEAD.

    Returns:
        List of files with their paths and types
    """
    data = await github_request(
        f"/repos/{owner}/{repo}/git/trees/{ref}",
        params={"recursive": 1},
        token=token,
    )
    return [
        {"path": item["path"], "type": item["type"]}
        for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]


async def get_file(
    owner: str,
    repo: str,
    path: str,
    token: Optional[str] = None,
    ref: Optional[str] = None,
) -> str:
    """
    Get file content from a repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: File path
        token: GitHub token
        ref: Optional git reference (branch, tag, or commit SHA)

    Returns:
        File content as string
    """
    from base64 import b64decode

    params = {"ref": ref} if ref else None
    data = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        params=params,
        token=token,
    )
    content_b64 = data.get("content") or ""
    return b64decode(content_b64.encode("utf-8")).decode("utf-8", errors="replace")


async def put_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    token: Optional[str] = None,
    branch: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create or update a file in the repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: File path
        content: File content
        message: Commit message
        token: GitHub token
        branch: Optional branch name to commit to (defaults to repository's default branch)

    Uses the user's OAuth token. If the user doesn't have write access,
    they need to install the GitPilot GitHub App on the repository.
    """
    from base64 import b64encode

    sha: str | None = None
    try:
        # When checking for existing file, use the branch if provided
        params = {"ref": branch} if branch else None
        existing = await github_request(
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
            token=token,
        )
        sha = existing.get("sha")
    except HTTPException:
        sha = None

    body: dict[str, Any] = {
        "message": message,
        "content": b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch

    result = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        method="PUT",
        json=body,
        token=token,
    )
    commit = result.get("commit", {})
    return {
        "path": path,
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url"),
    }


async def delete_file(
    owner: str,
    repo: str,
    path: str,
    message: str,
    token: Optional[str] = None,
    branch: Optional[str] = None,
) -> dict[str, Any]:
    """
    Delete a file from the repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: File path
        message: Commit message
        token: GitHub token
        branch: Optional branch name to delete from (defaults to repository's default branch)

    Uses the user's OAuth token. If the user doesn't have write access,
    they need to install the GitPilot GitHub App on the repository.
    """
    # Get the file SHA, optionally from a specific branch
    params = {"ref": branch} if branch else None
    existing = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        params=params,
        token=token,
    )
    sha = existing.get("sha")

    if not sha:
        raise HTTPException(
            status_code=404,
            detail=f"File {path} not found or has no SHA"
        )

    body = {
        "message": message,
        "sha": sha,
    }
    if branch:
        body["branch"] = branch

    result = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        method="DELETE",
        json=body,
        token=token,
    )

    commit = result.get("commit", {}) if result else {}
    return {
        "path": path,
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url"),
    }