from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from .auth import get_auth_manager
from .settings import get_settings, GitHubAuthMode

GITHUB_API_BASE = "https://api.github.com"


async def _github_token() -> str:
    """
    Get GitHub token based on configured authentication mode.

    Priority:
    1. GitHub App installation token (recommended)
    2. Personal Access Token (PAT) from settings or environment

    Returns:
        GitHub API token string

    Raises:
        HTTPException: If no valid authentication is configured
    """
    settings = get_settings()
    auth_manager = get_auth_manager()

    # Try GitHub App token (primary method)
    if settings.github.auth_mode == GitHubAuthMode.app:
        app_config = settings.github.app
        if app_config.app_id and app_config.installation_id:
            # Try to get private key from keyring first, then from settings
            private_key = auth_manager.get_app_private_key() or app_config.private_key_base64
            if private_key:
                try:
                    token = await auth_manager.get_installation_token(
                        app_config.app_id,
                        app_config.installation_id,
                        private_key,
                    )
                    return token
                except Exception:
                    pass  # Fall through to PAT

    # Fall back to Personal Access Token
    if settings.github.personal_token:
        return settings.github.personal_token

    # Last resort: environment variables
    token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        return token

    # No authentication configured
    raise HTTPException(
        status_code=401,
        detail=(
            "GitHub authentication not configured. "
            "Please run 'gitpilot login' to configure your GitHub App credentials, "
            "or set GITPILOT_GITHUB_TOKEN in your environment."
        ),
    )


def _github_token_sync() -> str:
    """Synchronous version for CLI usage."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_github_token())


async def github_request(
    path: str,
    *,
    method: str = "GET",
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    token = await _github_token()

    headers = {
        "Authorization": f"Bearer {token}",
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
        raise HTTPException(status_code=resp.status_code, detail=msg)

    if resp.status_code == 204:
        return None
    return resp.json()


async def list_user_repos(query: str | None = None) -> List[Dict[str, Any]]:
    """
    List repositories accessible to the authenticated user.

    For PAT: Lists user's repositories
    For GitHub App: Lists repositories where app is installed
    """
    settings = get_settings()
    auth_manager = get_auth_manager()

    # If using GitHub App, list installation repositories
    if settings.github.auth_mode == GitHubAuthMode.app:
        app_config = settings.github.app
        if app_config.app_id and app_config.installation_id:
            private_key = auth_manager.get_app_private_key() or app_config.private_key_base64
            if private_key:
                try:
                    repos_data = await auth_manager.list_installation_repos(
                        app_config.app_id,
                        app_config.installation_id,
                        private_key,
                    )
                    repos = [
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "full_name": r["full_name"],
                            "private": r["private"],
                            "owner": r["owner"]["login"],
                        }
                        for r in repos_data
                    ]
                    if query:
                        q = query.lower()
                        repos = [r for r in repos if q in r["full_name"].lower()]
                    return repos
                except Exception:
                    pass  # Fall through to user repos

    # Default: List user repos (PAT mode)
    params = {
        "per_page": 100,
        "affiliation": "owner,collaborator,organization_member",
    }
    data = await github_request("/user/repos", params=params)
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


async def get_repo_tree(owner: str, repo: str) -> list[dict[str, str]]:
    data = await github_request(
        f"/repos/{owner}/{repo}/git/trees/HEAD",
        params={"recursive": 1},
    )
    return [
        {"path": item["path"], "type": item["type"]}
        for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]


async def get_file(owner: str, repo: str, path: str) -> str:
    from base64 import b64decode

    data = await github_request(f"/repos/{owner}/{repo}/contents/{path}")
    content_b64 = data.get("content") or ""
    return b64decode(content_b64.encode("utf-8")).decode("utf-8", errors="replace")


async def put_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
) -> dict[str, Any]:
    from base64 import b64encode

    sha: str | None = None
    try:
        existing = await github_request(f"/repos/{owner}/{repo}/contents/{path}")
        sha = existing.get("sha")
    except HTTPException:
        sha = None

    body: dict[str, Any] = {
        "message": message,
        "content": b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha

    result = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        method="PUT",
        json=body,
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
) -> dict[str, Any]:
    """Delete a file from the repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: Path to the file to delete
        message: Commit message for the deletion

    Returns:
        Dictionary with deletion details including commit info
    """
    # Get current file SHA (required for deletion)
    existing = await github_request(f"/repos/{owner}/{repo}/contents/{path}")
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
    )

    commit = result.get("commit", {})
    return {
        "path": path,
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url"),
    }