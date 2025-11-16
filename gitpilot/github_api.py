from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

GITHUB_API_BASE = "https://api.github.com"

# GitHub App configuration
GH_APP_ID = os.getenv("GITPILOT_GH_APP_ID")
GH_APP_PRIVATE_KEY_B64 = os.getenv("GITPILOT_GH_APP_PRIVATE_KEY_BASE64")
GH_APP_INSTALLATION_ID = os.getenv("GITPILOT_GH_APP_INSTALLATION_ID")
GH_APP_CLIENT_ID = os.getenv("GITPILOT_GH_APP_CLIENT_ID")
GH_APP_CLIENT_SECRET = os.getenv("GITPILOT_GH_APP_CLIENT_SECRET")
GH_APP_SLUG = os.getenv("GITPILOT_GH_APP_SLUG")


def _pat_token() -> str | None:
    """Get personal access token from environment if available."""
    return os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")


def _github_jwt() -> str:
    """Generate a JWT for GitHub App authentication."""
    if not GH_APP_ID or not GH_APP_PRIVATE_KEY_B64:
        raise HTTPException(500, "GitHub App not configured")

    try:
        import jwt
    except ImportError:
        raise HTTPException(
            500,
            "PyJWT not installed. Run: pip install pyjwt[crypto]"
        )

    private_key = base64.b64decode(GH_APP_PRIVATE_KEY_B64).decode("utf-8")

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": GH_APP_ID,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


async def _installation_access_token() -> str:
    """Get an installation access token for the GitHub App."""
    if not GH_APP_INSTALLATION_ID:
        raise HTTPException(400, "GitHub App installation not set up")

    jwt_token = _github_jwt()
    url = f"{GITHUB_API_BASE}/app/installations/{GH_APP_INSTALLATION_ID}/access_tokens"
    async with httpx.AsyncClient() as client:
        res = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if res.status_code >= 400:
            raise HTTPException(res.status_code, f"GitHub app token error: {res.text}")
        data = res.json()
        return data["token"]


async def _github_token() -> str:
    """
    Get GitHub token - tries PAT first, then GitHub App installation token.
    """
    # 1) If PAT is set, use old behavior
    pat = _pat_token()
    if pat:
        return pat

    # 2) Otherwise use GitHub App installation token
    return await _installation_access_token()


async def github_request(
    path: str,
    *,
    method: str = "GET",
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    token = _github_token()

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


async def list_installation_repos(query: str | None = None) -> List[Dict[str, Any]]:
    """List repositories accessible via GitHub App installation."""
    params = {"per_page": 100}
    data = await github_request("/installation/repositories", params=params)
    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "owner": r["owner"]["login"],
        }
        for r in data.get("repositories", [])
    ]
    if query:
        q = query.lower()
        repos = [r for r in repos if q in r["full_name"].lower()]
    return repos


async def list_user_repos(query: str | None = None) -> List[Dict[str, Any]]:
    """List user repositories - uses GitHub App if configured, otherwise PAT."""
    # If using GitHub App, use installation repositories endpoint
    if GH_APP_INSTALLATION_ID and not _pat_token():
        return await list_installation_repos(query=query)

    # Otherwise use personal access token mode
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