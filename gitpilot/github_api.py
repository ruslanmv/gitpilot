from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

GITHUB_API_BASE = "https://api.github.com"

_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_token", default=None)


@contextmanager
def execution_context(token: Optional[str]):
    token_var = _request_token.set(token)
    try:
        yield
    finally:
        _request_token.reset(token_var)


def _github_token(provided_token: Optional[str] = None) -> str:
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
    from base64 import b64encode
    # Import inside function to avoid circular top-level imports
    from .github_app import is_github_app_configured, get_installation_token_for_repo

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

    async def _do_put(use_token: Optional[str]) -> Any:
        return await github_request(
            f"/repos/{owner}/{repo}/contents/{path}",
            method="PUT",
            json=body,
            token=use_token,
        )

    try:
        result = await _do_put(token)
    except HTTPException as e:
        # Fallback to GitHub App Token if we get a 403 permissions error
        msg = str(e.detail)
        if (
            e.status_code == 403
            and "Resource not accessible by integration" in msg
            and is_github_app_configured()
        ):
            print(f"[GitPilot] 403 on put_file. Falling back to GitHub App token for {owner}/{repo}")
            app_token = await get_installation_token_for_repo(owner, repo)
            result = await _do_put(app_token)
        else:
            raise

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
    """Delete a file from the repository."""
    from .github_app import is_github_app_configured, get_installation_token_for_repo
    
    async def _get_existing(use_token: Optional[str]) -> Any:
        return await github_request(
            f"/repos/{owner}/{repo}/contents/{path}",
            token=use_token,
        )

    try:
        existing = await _get_existing(token)
    except HTTPException as e:
        # Fallback check for existence
        msg = str(e.detail)
        if (
            e.status_code == 403
            and "Resource not accessible by integration" in msg
            and is_github_app_configured()
        ):
            app_token = await get_installation_token_for_repo(owner, repo)
            existing = await _get_existing(app_token)
        else:
            raise

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

    async def _do_delete(use_token: Optional[str]) -> Any:
        return await github_request(
            f"/repos/{owner}/{repo}/contents/{path}",
            method="DELETE",
            json=body,
            token=use_token,
        )

    try:
        result = await _do_delete(token)
    except HTTPException as e:
        # Fallback delete
        msg = str(e.detail)
        if (
            e.status_code == 403
            and "Resource not accessible by integration" in msg
            and is_github_app_configured()
        ):
            print(f"[GitPilot] 403 on delete_file. Falling back to GitHub App token for {owner}/{repo}")
            app_token = await get_installation_token_for_repo(owner, repo)
            result = await _do_delete(app_token)
        else:
            raise

    commit = result.get("commit", {})
    return {
        "path": path,
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url"),
    }