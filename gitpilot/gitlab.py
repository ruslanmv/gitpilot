"""GitLab.com OAuth and repository API integration for GitPilot.

GitLab credentials are provided per request, as with the existing GitHub API.
No access token is written to a server-side file or application log.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

GITLAB_URL = "https://gitlab.com"
API_URL = f"{GITLAB_URL}/api/v4"
_router = APIRouter(tags=["gitlab"])
_pending: dict[str, tuple[float, str]] = {}


class OAuthCallback(BaseModel):
    """GitLab authorization callback values."""

    code: str
    state: str


class TokenPayload(BaseModel):
    """GitLab token to validate."""

    access_token: str


class FileUpdate(BaseModel):
    """A repository file change on a selected branch."""

    path: str = Field(min_length=1)
    content: str
    message: str = Field(min_length=1)
    branch: str = Field(min_length=1)


def _redirect_uri() -> str:
    """Return the configured public frontend OAuth redirect URI."""
    explicit = os.getenv("GITLAB_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = os.getenv("GITPILOT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="GitLab OAuth needs GITLAB_REDIRECT_URI or GITPILOT_PUBLIC_BASE_URL.",
        )
    return f"{base}/auth"


def _oauth_config() -> tuple[str, str]:
    client_id = os.getenv("GITLAB_CLIENT_ID", "").strip()
    client_secret = os.getenv("GITLAB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="GitLab OAuth is not configured.")
    return client_id, client_secret


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Connect your GitLab account first.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Connect your GitLab account first.")
    return token


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
) -> httpx.Response:
    """Send a token-authenticated request to the fixed GitLab.com API host."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method, f"{API_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params, json=json,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="GitLab is currently unreachable.") from exc
    if response.status_code >= 400:
        code = response.status_code
        if code in (401, 403):
            message = "GitLab authorization failed or the project is not accessible."
        elif code == 404:
            message = "GitLab project, ref, or file not found."
        elif code == 429:
            message = "GitLab rate limit reached. Try again later."
        else:
            message = "GitLab could not complete the request."
        raise HTTPException(status_code=code if 400 <= code < 500 else 502, detail=message)
    return response


def _profile(raw: dict) -> dict:
    """Normalize GitLab identity to the existing GitPilot user shape."""
    return {
        "id": raw["id"],
        "login": raw["username"],
        "name": raw.get("name"),
        "email": raw.get("email"),
        "avatar_url": raw.get("avatar_url", ""),
        "html_url": raw.get("web_url", ""),
        "provider": "gitlab",
    }


@_router.get("/api/gitlab/auth/url")
async def gitlab_auth_url():
    """Start GitLab OAuth with CSRF state and S256 PKCE."""
    client_id, _ = _oauth_config()
    redirect = _redirect_uri()
    now = time.monotonic()
    for key, (created, _) in list(_pending.items()):
        if now - created > 600:
            _pending.pop(key, None)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _pending[state] = (now, verifier)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "read_user api",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorization_url": f"{GITLAB_URL}/oauth/authorize?{urlencode(params)}", "state": state}


@_router.post("/api/gitlab/auth/callback")
async def gitlab_auth_callback(payload: OAuthCallback):
    """Exchange a one-time OAuth authorization code and fetch the GitLab profile."""
    client_id, client_secret = _oauth_config()
    entry = _pending.pop(payload.state, None)
    if not entry or time.monotonic() - entry[0] > 600:
        raise HTTPException(status_code=400, detail="GitLab authorization expired. Please try again.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{GITLAB_URL}/oauth/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": payload.code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _redirect_uri(),
                    "code_verifier": entry[1],
                },
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="GitLab authorization was not accepted.")
            access_token = resp.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=400, detail="GitLab did not return an access token.")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="GitLab authorization is temporarily unavailable.") from exc
    user_response = await _request("GET", "/user", access_token)
    return {"access_token": access_token, "token_type": "bearer", "user": _profile(user_response.json()), "provider": "gitlab"}


@_router.post("/api/gitlab/auth/validate")
async def gitlab_validate_token(payload: TokenPayload):
    """Validate a stored GitLab access token on session restoration."""
    try:
        response = await _request("GET", "/user", payload.access_token)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            return {"authenticated": False}
        raise
    return {"authenticated": True, "user": _profile(response.json())}


@_router.get("/api/gitlab/projects")
async def gitlab_projects(
    query: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    authorization: str | None = Header(None),
):
    """List accessible GitLab projects with stable numeric IDs for nested groups."""
    if not authorization:
        return {"repositories": [], "page": page, "per_page": per_page, "total_count": 0,
                "has_more": False, "gitlab_connected": False}
    token = _token(authorization)
    params: dict = {"membership": "true", "simple": "true", "page": page, "per_page": per_page}
    if query:
        params["search"] = query
    response = await _request("GET", "/projects", token, params=params)
    repos = []
    for item in response.json():
        full_name = item["path_with_namespace"]
        repos.append({
            "id": item["id"],
            "name": item["name"],
            "full_name": full_name,
            "owner": full_name.rsplit("/", 1)[0],
            "default_branch": item.get("default_branch") or "main",
            "private": item.get("visibility") != "public",
            "provider": "gitlab",
        })
    total = response.headers.get("x-total")
    return {
        "repositories": repos, "page": page, "per_page": per_page,
        "total_count": int(total) if total and total.isdigit() else None,
        "has_more": bool(response.headers.get("x-next-page")) or (len(repos) == per_page and not total),
        "query": query, "gitlab_connected": True,
    }


@_router.get("/api/gitlab/projects/{project_id}/tree")
async def gitlab_tree(
    project_id: int,
    ref: str | None = None,
    authorization: str | None = Header(None),
):
    """Read the recursively paginated repository tree from a GitLab project."""
    token = _token(authorization)
    files: list[dict] = []
    for page in range(1, 51):
        params: dict = {"recursive": "true", "per_page": 100, "page": page}
        if ref and ref != "HEAD":
            params["ref"] = ref
        response = await _request("GET", f"/projects/{project_id}/repository/tree", token, params=params)
        items = response.json()
        files.extend({"path": item["path"], "type": "tree" if item["type"] == "tree" else "blob"} for item in items)
        if not response.headers.get("x-next-page"):
            break
    return {"files": files}


@_router.get("/api/gitlab/projects/{project_id}/file")
async def gitlab_file(
    project_id: int,
    path: str,
    ref: str = "HEAD",
    authorization: str | None = Header(None),
):
    """Read decoded GitLab file content."""
    token = _token(authorization)
    encoded = quote(path, safe="")
    response = await _request(
        "GET", f"/projects/{project_id}/repository/files/{encoded}", token, params={"ref": ref}
    )
    raw = response.json()
    return {"path": path, "content": base64.b64decode(raw["content"]).decode("utf-8", errors="replace")}


@_router.get("/api/gitlab/projects/{project_id}/branches")
async def gitlab_branches(
    project_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
    query: str | None = None,
    authorization: str | None = Header(None),
):
    """List branches in the shared branch-picker response shape."""
    token = _token(authorization)
    info = (await _request("GET", f"/projects/{project_id}", token)).json()
    params: dict = {"page": page, "per_page": per_page}
    if query:
        params["search"] = query
    response = await _request("GET", f"/projects/{project_id}/repository/branches", token, params=params)
    branches = [
        {"name": item["name"], "is_default": item.get("default", False),
         "protected": item.get("protected", False), "commit_sha": item.get("commit", {}).get("id")}
        for item in response.json()
    ]
    return {
        "repository": info["path_with_namespace"],
        "default_branch": info.get("default_branch") or "main",
        "page": page, "per_page": per_page, "branches": branches,
        "has_more": bool(response.headers.get("x-next-page")),
    }


@_router.post("/api/gitlab/projects/{project_id}/file")
async def gitlab_write_file(
    project_id: int,
    payload: FileUpdate,
    authorization: str | None = Header(None),
):
    """Create or update a file on a GitLab branch using the GitLab Files API."""
    token = _token(authorization)
    if payload.path.startswith("/") or ".." in payload.path.split("/"):
        raise HTTPException(status_code=400, detail="Invalid repository file path.")
    encoded = quote(payload.path, safe="")
    url = f"/projects/{project_id}/repository/files/{encoded}"
    params = {"ref": payload.branch}
    try:
        existing = await _request("GET", url, token, params=params)
        _ = existing
        method = "PUT"
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        method = "POST"
    result = await _request(
        method, url, token,
        json={
            "branch": payload.branch, "content": payload.content,
            "commit_message": payload.message,
        },
    )
    return {"path": payload.path, "branch": payload.branch, "result": result.json(), "provider": "gitlab"}


def build_gitlab_router() -> APIRouter:
    """Return the GitLab OAuth and repository router."""
    return _router
