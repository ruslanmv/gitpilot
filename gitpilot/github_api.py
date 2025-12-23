# gitpilot/github_api.py
from __future__ import annotations

import contextvars
import os
import re
from base64 import b64decode, b64encode
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

GITHUB_API_BASE = "https://api.github.com"

# Context variable to store the GitHub token for the current request/execution scope
_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_token", default=None
)

# Git SHA (40-hex) validator
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# add near _request_token
_request_ref: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_ref", default=None
)


@contextmanager
def execution_context(token: Optional[str], ref: Optional[str] = None):
    token_var = _request_token.set(token)
    ref_var = _request_ref.set(ref)
    try:
        yield
    finally:
        _request_token.reset(token_var)
        _request_ref.reset(ref_var)


def _github_ref(provided_ref: Optional[str] = None) -> Optional[str]:
    if provided_ref:
        return provided_ref
    return _request_ref.get()


def _github_token(provided_token: Optional[str] = None) -> str:
    """
    Get GitHub token from:
    1. Explicit argument
    2. Request Context (set via execution_context)
    3. Environment variables (Fallback)
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
    """
    Core GitHub request helper.
    Raises HTTPException with GitHub's error message on failures.
    """
    github_token = _github_token(token)

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitpilot",
    }

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

    async with httpx.AsyncClient(
        base_url=GITHUB_API_BASE, headers=headers, timeout=timeout
    ) as client:
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

    # Some GitHub endpoints return 200 with empty body
    if not resp.content:
        return None

    return resp.json()


# -----------------------------------------------------------------------------
# Repos listing (legacy + pagination/search)
# -----------------------------------------------------------------------------

async def list_user_repos(
    query: str | None = None, token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Legacy function - fetches first 100 repos.
    (Retro-compatible with older GitPilot versions.)
    """
    params = {
        "per_page": 100,
        "affiliation": "owner,collaborator,organization_member",
        "sort": "updated",
        "direction": "desc",
    }
    data = await github_request("/user/repos", params=params, token=token)

    # FIXED: Added default_branch mapping
    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "owner": r["owner"]["login"],
            "default_branch": r.get("default_branch", "main"),  # Critical Fix
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
    token: Optional[str] = None,
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
    per_page = min(per_page, 100)
    params = {
        "page": page,
        "per_page": per_page,
        "affiliation": "owner,collaborator,organization_member",
        "sort": "updated",
        "direction": "desc",
    }

    github_token = _github_token(token)
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitpilot",
    }

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

    async with httpx.AsyncClient(
        base_url=GITHUB_API_BASE, headers=headers, timeout=timeout
    ) as client:
        resp = await client.get("/user/repos", params=params)

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()

    # FIXED: Added default_branch mapping
    repos = [
        {
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": r["private"],
            "owner": r["owner"]["login"],
            "default_branch": r.get("default_branch", "main"),  # Critical Fix
        }
        for r in data
    ]

    link_header = resp.headers.get("Link", "") or ""
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
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search across ALL user repositories, then return paginated results.

    Returns:
      {
        "repositories": [...],
        "page": int,
        "per_page": int,
        "total_count": int,
        "has_more": bool,
      }
    """
    all_repos: List[Dict[str, Any]] = []
    fetch_page = 1
    max_pages = 15  # safety (1500 repos)

    while fetch_page <= max_pages:
        result = await list_user_repos_paginated(
            page=fetch_page,
            per_page=100,
            token=token,
        )
        all_repos.extend(result["repositories"])
        if not result["has_more"]:
            break
        fetch_page += 1

    q = query.lower()
    filtered = [
        r
        for r in all_repos
        if q in r["name"].lower() or q in r["full_name"].lower()
    ]

    total_count = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = filtered[start:end]

    return {
        "repositories": paginated,
        "page": page,
        "per_page": per_page,
        "total_count": total_count,
        "has_more": end < total_count,
    }


# -----------------------------------------------------------------------------
# Repo + Ref resolution helpers (fixes "No commit found for SHA: main")
# -----------------------------------------------------------------------------

async def get_repo(owner: str, repo: str, token: Optional[str] = None) -> Dict[str, Any]:
    """
    Get repository information including default_branch.
    """
    return await github_request(f"/repos/{owner}/{repo}", token=token)


async def _resolve_head_ref(owner: str, repo: str, token: Optional[str]) -> str:
    repo_data = await get_repo(owner, repo, token=token)
    return repo_data.get("default_branch", "main")


async def _resolve_ref_to_commit_sha(
    owner: str,
    repo: str,
    ref: Optional[str],
    token: Optional[str],
) -> str:
    """
    Resolve a ref (branch/tag/commit SHA/"HEAD"/None) to a commit SHA.
    """
    if not ref or ref == "HEAD":
        ref = await _resolve_head_ref(owner, repo, token)

    if _SHA_RE.match(ref):
        return ref.lower()

    # Branch ref
    try:
        data = await github_request(
            f"/repos/{owner}/{repo}/git/ref/heads/{ref}",
            token=token,
        )
        return data["object"]["sha"]
    except HTTPException:
        pass

    # Tag ref (lightweight or annotated)
    try:
        data = await github_request(
            f"/repos/{owner}/{repo}/git/ref/tags/{ref}",
            token=token,
        )
        obj = data.get("object") or {}
        sha = obj.get("sha")
        obj_type = obj.get("type")

        if not sha:
            raise HTTPException(status_code=404, detail=f"Tag ref '{ref}' not found.")

        # Annotated tag -> dereference to commit SHA
        if obj_type == "tag":
            tag_obj = await github_request(
                f"/repos/{owner}/{repo}/git/tags/{sha}",
                token=token,
            )
            target = tag_obj.get("object") or {}
            target_sha = target.get("sha")
            if not target_sha:
                raise HTTPException(
                    status_code=404, detail=f"Annotated tag '{ref}' has no target sha."
                )
            return target_sha

        # Lightweight tag points directly to commit SHA
        return sha
    except HTTPException:
        pass

    # Fallback: commits endpoint resolves branch/tag names to a commit
    try:
        commit = await github_request(
            f"/repos/{owner}/{repo}/commits/{ref}",
            token=token,
        )
        sha = commit.get("sha")
        if not sha:
            raise HTTPException(status_code=404, detail=f"Ref not found: {ref}")
        return sha
    except HTTPException as e:
        raise HTTPException(status_code=404, detail=f"Ref not found: {ref}") from e


async def _commit_sha_to_tree_sha(
    owner: str,
    repo: str,
    commit_sha: str,
    token: Optional[str],
) -> str:
    """
    Convert commit SHA -> tree SHA using /git/commits/{sha}.
    """
    commit = await github_request(
        f"/repos/{owner}/{repo}/git/commits/{commit_sha}",
        token=token,
    )
    tree = commit.get("tree") or {}
    tree_sha = tree.get("sha")
    if not tree_sha:
        raise HTTPException(status_code=500, detail="Failed to resolve tree SHA from commit.")
    return tree_sha


# -----------------------------------------------------------------------------
# Branch creation
# -----------------------------------------------------------------------------

async def create_branch(
    owner: str,
    repo: str,
    new_branch: str,
    from_ref: str = "HEAD",
    token: Optional[str] = None,
) -> str:
    """
    Create a new branch from a ref (default: HEAD = default branch).
    """
    base_commit_sha = await _resolve_ref_to_commit_sha(owner, repo, from_ref, token)

    body = {"ref": f"refs/heads/{new_branch}", "sha": base_commit_sha}
    new_ref = await github_request(
        f"/repos/{owner}/{repo}/git/refs",
        method="POST",
        json=body,
        token=token,
    )
    return new_ref["ref"]


# -----------------------------------------------------------------------------
# Tree + File APIs (branch-aware)
# -----------------------------------------------------------------------------

async def get_repo_tree(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    ref: str = "HEAD",
):
    # ✅ FIX: Only use context ref if caller did NOT provide a specific ref
    # i.e. only when ref is missing/empty or explicitly "HEAD"
    ctx_ref = _github_ref(None)
    if (not ref or ref == "HEAD") and ctx_ref:
        ref = ctx_ref

    commit_sha = await _resolve_ref_to_commit_sha(owner, repo, ref, token)
    tree_sha = await _commit_sha_to_tree_sha(owner, repo, commit_sha, token)

    tree_data = await github_request(
        f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
        params={"recursive": 1},
        token=token,
    )

    return [
        {"path": item["path"], "type": item["type"]}
        for item in tree_data.get("tree", [])
        if item.get("type") == "blob"
    ]


async def get_file(
    owner: str,
    repo: str,
    path: str,
    token: Optional[str] = None,
    ref: Optional[str] = None,
) -> str:
    # ✅ FIX: Only use context ref if ref is missing or "HEAD"
    ctx_ref = _github_ref(None)
    if (not ref or ref == "HEAD") and ctx_ref:
        ref = ctx_ref

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
) -> Dict[str, Any]:
    """
    Create or update a file in the repository on a specific branch.
    (Retro-compatible signature with older GitPilot versions.)
    """
    sha: Optional[str] = None
    try:
        params = {"ref": branch} if branch else None
        existing = await github_request(
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
            token=token,
        )
        sha = existing.get("sha")
    except HTTPException:
        sha = None

    body: Dict[str, Any] = {
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
    commit = (result or {}).get("commit", {}) if isinstance(result, dict) else {}
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
) -> Dict[str, Any]:
    """
    Delete a file from the repository on a specific branch.
    (Retro-compatible signature with older GitPilot versions.)
    """
    params = {"ref": branch} if branch else None
    existing = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        params=params,
        token=token,
    )
    sha = existing.get("sha")
    if not sha:
        raise HTTPException(status_code=404, detail=f"File {path} not found or has no SHA")

    body: Dict[str, Any] = {"message": message, "sha": sha}
    if branch:
        body["branch"] = branch

    result = await github_request(
        f"/repos/{owner}/{repo}/contents/{path}",
        method="DELETE",
        json=body,
        token=token,
    )
    commit = (result or {}).get("commit", {}) if isinstance(result, dict) else {}
    return {
        "path": path,
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url"),
    }
