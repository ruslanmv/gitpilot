from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

GITHUB_API_BASE = "https://api.github.com"

# Context variable to store the GitHub token for the current request/execution scope.
# This allows deep functions (like tools used by agents) to access the token 
# without passing it explicitly through every function call.
_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_token", default=None)


@contextmanager
def execution_context(token: Optional[str]):
    """
    Context manager to set the GitHub token for the current execution scope.
    Usage:
        with execution_context(token):
            # Code here (and functions it calls) can access the token via _github_token()
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
    # 1. Prefer explicitly provided token
    if provided_token:
        return provided_token

    # 2. Check Request Context (injected by API middleware/endpoint)
    ctx_token = _request_token.get()
    if ctx_token:
        return ctx_token

    # 3. Fallback to environment variables (for CLI/Server mode)
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
        
        # Handle 401 specifically to provide a better error for tools
        if resp.status_code == 401:
             msg = "GitHub Token Expired or Invalid. Please refresh your login."
             
        raise HTTPException(status_code=resp.status_code, detail=msg)

    if resp.status_code == 204:
        return None
    return resp.json()


async def list_user_repos(query: str | None = None, token: Optional[str] = None) -> List[Dict[str, Any]]:
    params = {
        "per_page": 100,
        "affiliation": "owner,collaborator,organization_member",
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


async def get_repo_tree(owner: str, repo: str, token: Optional[str] = None) -> list[dict[str, str]]:
    data = await github_request(
        f"/repos/{owner}/{repo}/git/trees/HEAD",
        params={"recursive": 1},
        token=token,
    )
    return [
        {"path": item["path"], "type": item["type"]}
        for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]


async def get_file(owner: str, repo: str, path: str, token: Optional[str] = None) -> str:
    from base64 import b64decode

    data = await github_request(f"/repos/{owner}/{repo}/contents/{path}", token=token)
    content_b64 = data.get("content") or ""
    return b64decode(content_b64.encode("utf-8")).decode("utf-8", errors="replace")


async def put_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    token: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create or update a file in the repository.
    
    Uses the user's OAuth token. If the user doesn't have write access,
    they need to install the GitPilot GitHub App on the repository.
    """
    from base64 import b64encode

    sha: str | None = None
    try:
        existing = await github_request(f"/repos/{owner}/{repo}/contents/{path}", token=token)
        sha = existing.get("sha")
    except HTTPException:
        sha = None

    body: dict[str, Any] = {
        "message": message,
        "content": b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha

    # Use user token - it has write access if app is installed
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
) -> dict[str, Any]:
    """
    Delete a file from the repository.
    
    Uses the user's OAuth token. If the user doesn't have write access,
    they need to install the GitPilot GitHub App on the repository.
    """
    # Get current file SHA (required for deletion)
    existing = await github_request(f"/repos/{owner}/{repo}/contents/{path}", token=token)
    sha = existing.get("sha")

    if not sha:
        raise HTTPException(
            status_code=404,
            detail=f"File {path} not found or has no SHA"
        )

    # Delete the file
    body = {
        "message": message,
        "sha": sha,
    }
    
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