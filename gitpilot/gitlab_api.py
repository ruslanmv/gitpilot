# gitpilot/gitlab_api.py
"""GitLab REST v4 client with the same function shapes as the GitHub helpers.

Every public coroutine here mirrors one in :mod:`gitpilot.github_api`,
:mod:`gitpilot.github_pulls`, :mod:`gitpilot.github_issues` or
:mod:`gitpilot.github_search`, takes the same arguments and returns the same
payload shape (merge requests are returned as GitHub-shaped pull requests:
``number`` is the MR ``iid``, ``head.ref`` the source branch). That is what lets
:mod:`gitpilot.scm` route the existing agents and tools to GitLab without a
second copy of them.

Repositories are addressed by their full path (``group/subgroup/project``),
URL-encoded as GitLab expects, or by the numeric project id bound to the
request, which survives renames and transfers.

The API host is fixed by the operator (``GITLAB_BASE_URL``, default
https://gitlab.com). It is never taken from a request, so a client cannot point
the server's GitLab credential at another host.
"""
from __future__ import annotations

import base64
import contextvars
import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException

from .scm_context import (
    _request_provider,
    _request_repo,
    current_repo_id,
    token_belongs_to_other_provider,
)

logger = logging.getLogger(__name__)

DEFAULT_GITLAB_URL = "https://gitlab.com"

_request_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "gitlab_request_token", default=None
)
_request_ref: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "gitlab_request_ref", default=None
)

# GitLab access levels (https://docs.gitlab.com/ee/api/members.html#roles).
ACCESS_GUEST = 10
ACCESS_REPORTER = 20
ACCESS_DEVELOPER = 30
ACCESS_MAINTAINER = 40

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


# ---------------------------------------------------------------------------
# Host and credentials
# ---------------------------------------------------------------------------

def gitlab_base_url() -> str:
    """Return the operator-configured GitLab instance URL.

    Only an absolute https URL without credentials, query or fragment is
    accepted. Plain http is allowed only for a loopback host, for local tests of
    a self-managed instance.
    """
    raw = (os.getenv("GITLAB_BASE_URL") or DEFAULT_GITLAB_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    loopback = host in ("localhost", "127.0.0.1", "::1")
    if (
        parsed.scheme not in ("https", "http")
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and not loopback)
    ):
        logger.error("GITLAB_BASE_URL is not a valid https URL; GitLab is disabled")
        raise HTTPException(status_code=503, detail="The GitLab server URL is misconfigured.")
    return raw


def gitlab_api_url() -> str:
    """REST v4 base of the configured GitLab instance."""
    return f"{gitlab_base_url()}/api/v4"


def gitlab_host() -> str:
    """Hostname of the configured GitLab instance (for display)."""
    return urlparse(gitlab_base_url()).hostname or "gitlab.com"


@contextmanager
def execution_context(token: Optional[str], ref: Optional[str] = None,
                      repo: Optional[tuple[str, str]] = None):
    """Bind a GitLab credential, ref and repository for one request or run."""
    provider_var = _request_provider.set("gitlab")
    token_var = _request_token.set(token)
    ref_var = _request_ref.set(ref)
    repo_var = _request_repo.set(repo)
    try:
        yield
    finally:
        _request_provider.reset(provider_var)
        _request_token.reset(token_var)
        _request_ref.reset(ref_var)
        _request_repo.reset(repo_var)


def _gitlab_token(provided: Optional[str] = None) -> str:
    """Resolve the GitLab token: explicit, then request context, then environment.

    Inside a GitHub scope nothing is sent to GitLab: the bound credential is
    GitHub's.
    """
    if _request_provider.get() == "github":
        raise HTTPException(
            status_code=400,
            detail="This operation targets GitLab, but the selected repository is on GitHub.",
        )
    token = provided or _request_token.get()
    if not token:
        token = os.getenv("GITPILOT_GITLAB_TOKEN") or os.getenv("GITLAB_TOKEN")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="GitLab authentication required. Connect your GitLab account first.",
        )
    if token_belongs_to_other_provider(token, "gitlab"):
        raise HTTPException(status_code=400, detail="A GitHub credential cannot be used for GitLab.")
    return token


def _gitlab_ref(provided: Optional[str] = None) -> Optional[str]:
    if provided and provided != "HEAD":
        return provided
    ctx = _request_ref.get()
    return ctx if ctx and ctx != "HEAD" else None


def _clean_segment_path(value: str) -> str:
    parts = [p for p in str(value or "").strip("/").split("/")]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="Invalid GitLab repository path.")
    return "/".join(parts)


def project_ref(owner: str, repo: str) -> str:
    """URL path component for a project: its bound numeric id, else its encoded path.

    ``owner`` may contain nested groups (``group/subgroup``). A bare numeric
    ``repo`` with an empty owner is treated as a project id.
    """
    if not owner and str(repo).isdigit():
        return str(repo)
    full = _clean_segment_path(f"{owner}/{repo}")
    bound = current_repo_id(full)
    if bound and str(bound).isdigit():
        return str(bound)
    return quote(full, safe="")


def _file_path(path: str) -> str:
    clean = str(path or "").strip()
    if not clean or clean.startswith("/") or any(p in ("", "..") for p in clean.split("/")):
        raise HTTPException(status_code=400, detail="Invalid repository file path.")
    return clean


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - GitLab HTML error pages
        return ""
    msg: Any = data.get("message") or data.get("error_description") or data.get("error") if isinstance(data, dict) else ""
    if isinstance(msg, dict):
        msg = "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in msg.items())
    elif isinstance(msg, list):
        msg = "; ".join(map(str, msg))
    return str(msg or "")[:300]


async def gitlab_response(
    path: str,
    *,
    method: str = "GET",
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> httpx.Response:
    """Send one authenticated request to the configured GitLab API.

    Raises :class:`HTTPException` with a safe message on failure. The token is
    never included in an error message or log line.
    """
    gitlab_token = _gitlab_token(token)
    timeout = httpx.Timeout(connect=15.0, read=45.0, write=30.0, pool=15.0)
    try:
        async with httpx.AsyncClient(
            base_url=gitlab_api_url(),
            headers={"Authorization": f"Bearer {gitlab_token}", "User-Agent": "gitpilot"},
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            resp = await client.request(method, path, json=json, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="GitLab is currently unreachable.") from exc

    if resp.status_code < 400:
        return resp
    code = resp.status_code
    detail = _error_message(resp)
    if code == 401:
        message = "GitLab token expired or invalid. Please reconnect GitLab."
    elif code == 403:
        message = "GitLab denied access to this project or action" + (f": {detail}" if detail else ".")
    elif code == 404:
        message = "GitLab project, ref, or file not found" + (f": {detail}" if detail else ".")
    elif code == 429:
        message = "GitLab rate limit reached. Try again later."
    elif code < 500:
        message = detail or "GitLab could not complete the request."
    else:
        message = "GitLab could not complete the request."
    headers = {}
    if code == 429 and resp.headers.get("retry-after"):
        headers["Retry-After"] = resp.headers["retry-after"]
    raise HTTPException(status_code=code if code < 500 else 502, detail=message, headers=headers or None)


async def gitlab_request(path: str, **kwargs: Any) -> Any:
    """Like :func:`gitlab_response` but returns the decoded JSON body."""
    resp = await gitlab_response(path, **kwargs)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def _paginate(path: str, params: Optional[Dict[str, Any]] = None, *,
                    token: Optional[str] = None, max_pages: int = 50) -> List[Any]:
    out: List[Any] = []
    query = dict(params or {})
    query.setdefault("per_page", 100)
    page = 1
    while page and page <= max_pages:
        query["page"] = page
        resp = await gitlab_response(path, params=query, token=token)
        out.extend(resp.json() or [])
        nxt = resp.headers.get("x-next-page")
        page = int(nxt) if nxt and nxt.isdigit() else 0
    return out


# ---------------------------------------------------------------------------
# Normalisation to the GitHub-shaped payloads the rest of GitPilot reads
# ---------------------------------------------------------------------------

def _user(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    return {
        "login": raw.get("username") or raw.get("name") or "unknown",
        "id": raw.get("id"),
        "avatar_url": raw.get("avatar_url") or "",
        "html_url": raw.get("web_url") or "",
    }


def access_level(project: Dict[str, Any]) -> int:
    """Effective access level of the current user on a GitLab project payload."""
    perms = project.get("permissions") or {}
    levels = [
        (perms.get("project_access") or {}).get("access_level") or 0,
        (perms.get("group_access") or {}).get("access_level") or 0,
    ]
    return max(levels)


def normalize_project(item: Dict[str, Any]) -> Dict[str, Any]:
    """GitLab project -> GitPilot repository shape (GitHub-compatible keys)."""
    full_name = item.get("path_with_namespace") or ""
    owner, _, name = full_name.rpartition("/")
    level = access_level(item)
    has_perms = bool(item.get("permissions"))
    return {
        "id": item.get("id"),
        "name": item.get("path") or name or item.get("name"),
        "display_name": item.get("name"),
        "full_name": full_name,
        "owner": owner or (item.get("namespace") or {}).get("full_path", ""),
        "namespace": (item.get("namespace") or {}).get("full_path", owner),
        "default_branch": item.get("default_branch") or "main",
        "private": item.get("visibility") != "public",
        "visibility": item.get("visibility"),
        "html_url": item.get("web_url") or "",
        "description": item.get("description") or "",
        "archived": bool(item.get("archived")),
        "provider": "gitlab",
        "permissions": {
            "pull": (level >= ACCESS_GUEST) if has_perms else True,
            "push": level >= ACCESS_DEVELOPER,
            "admin": level >= ACCESS_MAINTAINER,
            "access_level": level,
        } if has_perms else None,
    }


def _state_out(state: Optional[str]) -> str:
    return "open" if state in ("opened", "locked", "reopened") else "closed"


def _state_in(state: Optional[str]) -> Optional[str]:
    return {"open": "opened", "closed": "closed", "all": "all", "merged": "merged"}.get(state or "open", "opened")


def normalize_issue(item: Dict[str, Any]) -> Dict[str, Any]:
    """GitLab issue -> GitHub issue shape."""
    milestone = item.get("milestone")
    return {
        "id": item.get("id"),
        "number": item.get("iid"),
        "title": item.get("title"),
        "body": item.get("description") or "",
        "state": _state_out(item.get("state")),
        "html_url": item.get("web_url"),
        "user": _user(item.get("author")),
        "labels": [{"name": name} for name in item.get("labels") or []],
        "assignees": [_user(a) for a in item.get("assignees") or []],
        "comments": item.get("user_notes_count", 0),
        "milestone": {"title": milestone.get("title"), "number": milestone.get("iid")} if milestone else None,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "closed_at": item.get("closed_at"),
        "provider": "gitlab",
    }


def normalize_merge_request(item: Dict[str, Any]) -> Dict[str, Any]:
    """GitLab merge request -> GitHub pull request shape."""
    merged = item.get("state") == "merged"
    status = item.get("detailed_merge_status") or item.get("merge_status")
    return {
        "id": item.get("id"),
        "number": item.get("iid"),
        "title": item.get("title"),
        "body": item.get("description") or "",
        "state": "closed" if merged else _state_out(item.get("state")),
        "merged": merged,
        "merged_at": item.get("merged_at"),
        "draft": bool(item.get("draft") or item.get("work_in_progress")),
        "html_url": item.get("web_url"),
        "user": _user(item.get("author")),
        "head": {"ref": item.get("source_branch"), "sha": item.get("sha")},
        "base": {"ref": item.get("target_branch")},
        "mergeable": status in ("can_be_merged", "mergeable") if status else None,
        "merge_commit_sha": item.get("merge_commit_sha") or item.get("squash_commit_sha"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "kind": "merge_request",
        "provider": "gitlab",
    }


def _count_diff(diff: str) -> tuple[int, int]:
    adds = dels = 0
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return adds, dels


def normalize_diff(item: Dict[str, Any]) -> Dict[str, Any]:
    """GitLab diff entry -> GitHub PR/compare file shape."""
    if item.get("new_file"):
        status = "added"
    elif item.get("deleted_file"):
        status = "removed"
    elif item.get("renamed_file"):
        status = "renamed"
    else:
        status = "modified"
    adds, dels = _count_diff(item.get("diff") or "")
    out = {
        "filename": item.get("new_path") or item.get("old_path"),
        "status": status,
        "additions": adds,
        "deletions": dels,
        "changes": adds + dels,
        "patch": item.get("diff") or "",
    }
    if status == "renamed":
        out["previous_filename"] = item.get("old_path")
    return out


def normalize_commit(item: Dict[str, Any]) -> Dict[str, Any]:
    """GitLab commit -> GitHub-like commit summary."""
    return {
        "sha": item.get("id"),
        "short_sha": item.get("short_id") or (item.get("id") or "")[:8],
        "message": item.get("message") or item.get("title") or "",
        "author": {
            "name": item.get("author_name"),
            "email": item.get("author_email"),
            "date": item.get("authored_date") or item.get("created_at"),
        },
        "html_url": item.get("web_url"),
    }


def _note(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "body": item.get("body"),
        "user": _user(item.get("author")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Identity and projects
# ---------------------------------------------------------------------------

async def get_current_user(token: Optional[str] = None) -> Dict[str, Any]:
    """The authenticated GitLab user."""
    return await gitlab_request("/user", token=token)


async def list_projects(
    *,
    query: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    min_access_level: Optional[int] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Projects the user is a member of (directly or through any nested group)."""
    params: Dict[str, Any] = {
        "membership": "true",
        "archived": "false",
        "order_by": "last_activity_at",
        "sort": "desc",
        "page": page,
        "per_page": min(max(per_page, 1), 100),
    }
    if query:
        params["search"] = query
        params["search_namespaces"] = "true"
    if min_access_level:
        params["min_access_level"] = min_access_level
    resp = await gitlab_response("/projects", params=params, token=token)
    repos = [normalize_project(item) for item in resp.json() or []]
    total = resp.headers.get("x-total")
    nxt = resp.headers.get("x-next-page")
    return {
        "repositories": repos,
        "page": page,
        "per_page": params["per_page"],
        "total_count": int(total) if total and total.isdigit() else None,
        # GitLab omits x-total above 10k rows; then a full page means "maybe more".
        "has_more": bool(nxt) or (not total and len(repos) == params["per_page"]),
        "query": query,
    }


async def list_groups(*, query: Optional[str] = None, page: int = 1, per_page: int = 50,
                      token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Groups and subgroups visible to the user, with their full nested path."""
    params: Dict[str, Any] = {"page": page, "per_page": min(per_page, 100), "all_available": "false"}
    if query:
        params["search"] = query
    data = await gitlab_request("/groups", params=params, token=token) or []
    return [
        {"id": g.get("id"), "name": g.get("name"), "full_path": g.get("full_path"),
         "parent_id": g.get("parent_id"), "web_url": g.get("web_url"), "provider": "gitlab"}
        for g in data
    ]


async def list_group_projects(group: str, *, query: Optional[str] = None, page: int = 1,
                              per_page: int = 50, token: Optional[str] = None) -> Dict[str, Any]:
    """Projects of a group, including all of its subgroups."""
    gid = group if str(group).isdigit() else quote(_clean_segment_path(group), safe="")
    params: Dict[str, Any] = {"include_subgroups": "true", "archived": "false", "page": page,
                              "per_page": min(per_page, 100), "order_by": "last_activity_at"}
    if query:
        params["search"] = query
    resp = await gitlab_response(f"/groups/{gid}/projects", params=params, token=token)
    repos = [normalize_project(item) for item in resp.json() or []]
    return {"repositories": repos, "page": page, "per_page": params["per_page"],
            "has_more": bool(resp.headers.get("x-next-page"))}


async def get_repo(owner: str, repo: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Repository metadata, including ``default_branch`` and ``permissions``."""
    data = await gitlab_request(f"/projects/{project_ref(owner, repo)}", token=token)
    return normalize_project(data or {})


async def _default_branch(owner: str, repo: str, token: Optional[str]) -> str:
    return (await get_repo(owner, repo, token=token)).get("default_branch") or "main"


async def _resolve_ref(owner: str, repo: str, ref: Optional[str], token: Optional[str]) -> str:
    resolved = _gitlab_ref(ref)
    return resolved or await _default_branch(owner, repo, token)


# ---------------------------------------------------------------------------
# Branches, tree, files, commits
# ---------------------------------------------------------------------------

async def list_branches(owner: str, repo: str, *, query: Optional[str] = None, page: int = 1,
                        per_page: int = 100, token: Optional[str] = None) -> Dict[str, Any]:
    """Branches in the shared branch-picker shape."""
    params: Dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    if query:
        params["search"] = query
    pid = project_ref(owner, repo)
    resp = await gitlab_response(f"/projects/{pid}/repository/branches", params=params, token=token)
    branches = [
        {"name": item["name"], "is_default": bool(item.get("default")),
         "protected": bool(item.get("protected")),
         "commit_sha": (item.get("commit") or {}).get("id")}
        for item in resp.json() or []
    ]
    return {"branches": branches, "page": page, "per_page": params["per_page"],
            "has_more": bool(resp.headers.get("x-next-page"))}


async def create_branch(owner: str, repo: str, new_branch: str, from_ref: str = "HEAD",
                        token: Optional[str] = None) -> str:
    """Create ``new_branch`` from ``from_ref`` (``HEAD`` = default branch)."""
    base = await _resolve_ref(owner, repo, from_ref, token)
    await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/repository/branches",
        method="POST", params={"branch": new_branch, "ref": base}, token=token,
    )
    return f"refs/heads/{new_branch}"


async def delete_branch(owner: str, repo: str, branch: str, token: Optional[str] = None) -> None:
    """Delete a (non-protected) branch."""
    await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/repository/branches/{quote(branch, safe='')}",
        method="DELETE", token=token,
    )


async def get_repo_tree(owner: str, repo: str, token: Optional[str] = None, ref: str = "HEAD",
                        *, include_dirs: bool = False) -> List[Dict[str, str]]:
    """Recursive tree. Files only by default, like :func:`github_api.get_repo_tree`."""
    params: Dict[str, Any] = {"recursive": "true"}
    resolved = _gitlab_ref(ref)
    if resolved:
        params["ref"] = resolved
    items = await _paginate(f"/projects/{project_ref(owner, repo)}/repository/tree", params, token=token)
    out = []
    for item in items:
        kind = "tree" if item.get("type") == "tree" else "blob"
        if kind == "blob" or include_dirs:
            out.append({"path": item["path"], "type": kind})
    return out


async def get_file(owner: str, repo: str, path: str, token: Optional[str] = None,
                   ref: Optional[str] = None) -> str:
    """Decoded text content of ``path`` at ``ref``."""
    resolved = await _resolve_ref(owner, repo, ref, token)
    data = await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/repository/files/{quote(_file_path(path), safe='')}",
        params={"ref": resolved}, token=token,
    )
    raw = (data or {}).get("content") or ""
    return base64.b64decode(raw.encode("utf-8")).decode("utf-8", errors="replace")


async def file_exists(owner: str, repo: str, path: str, ref: str,
                      token: Optional[str] = None) -> bool:
    """Whether ``path`` exists on ``ref``."""
    try:
        await gitlab_response(
            f"/projects/{project_ref(owner, repo)}/repository/files/{quote(_file_path(path), safe='')}",
            method="HEAD", params={"ref": ref}, token=token,
        )
        return True
    except HTTPException as exc:
        if exc.status_code == 404:
            return False
        raise


async def commit_actions(owner: str, repo: str, *, branch: str, message: str,
                         actions: List[Dict[str, Any]], start_branch: Optional[str] = None,
                         token: Optional[str] = None) -> Dict[str, Any]:
    """Create one commit with several file actions (create/update/delete/move)."""
    body: Dict[str, Any] = {"branch": branch, "commit_message": message, "actions": actions}
    if start_branch:
        body["start_branch"] = start_branch
    data = await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/repository/commits",
        method="POST", json=body, token=token,
    )
    return data or {}


async def put_file(owner: str, repo: str, path: str, content: str, message: str,
                   token: Optional[str] = None, branch: Optional[str] = None) -> Dict[str, Any]:
    """Create or update a file on ``branch`` as one commit."""
    clean = _file_path(path)
    target = branch or await _resolve_ref(owner, repo, None, token)
    exists = await file_exists(owner, repo, clean, target, token=token)
    commit = await commit_actions(
        owner, repo, branch=target, message=message, token=token,
        actions=[{
            "action": "update" if exists else "create",
            "file_path": clean,
            "encoding": "base64",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }],
    )
    return {"path": clean, "commit_sha": commit.get("id", ""), "commit_url": commit.get("web_url")}


async def delete_file(owner: str, repo: str, path: str, message: str,
                      token: Optional[str] = None, branch: Optional[str] = None) -> Dict[str, Any]:
    """Delete a file on ``branch`` as one commit."""
    clean = _file_path(path)
    target = branch or await _resolve_ref(owner, repo, None, token)
    if not await file_exists(owner, repo, clean, target, token=token):
        raise HTTPException(status_code=404, detail=f"File {clean} not found")
    commit = await commit_actions(
        owner, repo, branch=target, message=message, token=token,
        actions=[{"action": "delete", "file_path": clean}],
    )
    return {"path": clean, "commit_sha": commit.get("id", ""), "commit_url": commit.get("web_url")}


async def list_commits(owner: str, repo: str, *, ref: Optional[str] = None, path: Optional[str] = None,
                       page: int = 1, per_page: int = 30, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Commit history of ``ref`` (optionally limited to ``path``)."""
    params: Dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
    resolved = _gitlab_ref(ref)
    if resolved:
        params["ref_name"] = resolved
    if path:
        params["path"] = _file_path(path)
    data = await gitlab_request(f"/projects/{project_ref(owner, repo)}/repository/commits",
                                params=params, token=token) or []
    return [normalize_commit(c) for c in data]


async def compare_refs(owner: str, repo: str, base: str, head: str,
                       token: Optional[str] = None) -> Dict[str, Any]:
    """Diff of ``head`` against ``base`` in the GitHub compare shape."""
    data = await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/repository/compare",
        params={"from": base, "to": head, "straight": "false"}, token=token,
    ) or {}
    commits = [normalize_commit(c) for c in data.get("commits") or []]
    return {
        "base": base, "head": head,
        "ahead_by": len(commits),
        "commits": commits,
        "files": [normalize_diff(d) for d in data.get("diffs") or []],
        "provider": "gitlab",
    }


# ---------------------------------------------------------------------------
# Merge requests (GitHub pull-request shapes)
# ---------------------------------------------------------------------------

def _mr_path(owner: str, repo: str, number: Optional[int] = None) -> str:
    base = f"/projects/{project_ref(owner, repo)}/merge_requests"
    return f"{base}/{int(number)}" if number is not None else base


def _branch_only(ref: Optional[str]) -> Optional[str]:
    # GitHub "owner:branch" head filters -> plain branch.
    return ref.split(":", 1)[1] if ref and ":" in ref else ref


async def list_pull_requests(owner: str, repo: str, *, state: str = "open", sort: str = "created",
                             direction: str = "desc", head: Optional[str] = None,
                             base: Optional[str] = None, per_page: int = 30, page: int = 1,
                             token: Optional[str] = None) -> List[Dict[str, Any]]:
    """List merge requests as pull requests."""
    params: Dict[str, Any] = {
        "state": _state_in(state),
        "order_by": "updated_at" if sort == "updated" else "created_at",
        "sort": "asc" if direction == "asc" else "desc",
        "per_page": min(per_page, 100), "page": page,
    }
    if head:
        params["source_branch"] = _branch_only(head)
    if base:
        params["target_branch"] = base
    data = await gitlab_request(_mr_path(owner, repo), params=params, token=token) or []
    return [normalize_merge_request(mr) for mr in data]


async def get_pull_request(owner: str, repo: str, pull_number: int,
                           token: Optional[str] = None) -> Dict[str, Any]:
    """One merge request as a pull request."""
    return normalize_merge_request(await gitlab_request(_mr_path(owner, repo, pull_number), token=token) or {})


async def create_pull_request(owner: str, repo: str, *, title: str, head: str, base: str,
                              body: Optional[str] = None, draft: bool = False,
                              token: Optional[str] = None) -> Dict[str, Any]:
    """Open a merge request from ``head`` into ``base``."""
    payload: Dict[str, Any] = {
        "source_branch": _branch_only(head),
        "target_branch": base,
        "title": f"Draft: {title}" if draft and not title.lower().startswith("draft:") else title,
        "remove_source_branch": False,
    }
    if body is not None:
        payload["description"] = body
    data = await gitlab_request(_mr_path(owner, repo), method="POST", json=payload, token=token)
    return normalize_merge_request(data or {})


async def update_pull_request(owner: str, repo: str, pull_number: int, *, title: Optional[str] = None,
                              body: Optional[str] = None, state: Optional[str] = None,
                              base: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """Edit a merge request; ``state`` open/closed maps to reopen/close."""
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["description"] = body
    if state == "closed":
        payload["state_event"] = "close"
    elif state == "open":
        payload["state_event"] = "reopen"
    if base is not None:
        payload["target_branch"] = base
    if not payload:
        return await get_pull_request(owner, repo, pull_number, token=token)
    data = await gitlab_request(_mr_path(owner, repo, pull_number), method="PUT", json=payload, token=token)
    return normalize_merge_request(data or {})


async def merge_pull_request(owner: str, repo: str, pull_number: int, *,
                             commit_title: Optional[str] = None, commit_message: Optional[str] = None,
                             merge_method: str = "merge", token: Optional[str] = None) -> Dict[str, Any]:
    """Merge a merge request. ``rebase`` is a GitLab project setting, not a per-merge choice."""
    if merge_method not in ("merge", "squash"):
        raise HTTPException(status_code=422, detail="GitLab supports the 'merge' and 'squash' merge methods here.")
    message = "\n\n".join(p for p in (commit_title, commit_message) if p) or None
    payload: Dict[str, Any] = {"squash": merge_method == "squash"}
    if message:
        payload["squash_commit_message" if merge_method == "squash" else "merge_commit_message"] = message
    data = await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/merge", method="PUT",
                                json=payload, token=token) or {}
    sha = data.get("merge_commit_sha") or data.get("squash_commit_sha") or data.get("sha")
    return {"merged": data.get("state") == "merged", "sha": sha,
            "message": "Merge request merged" if data.get("state") == "merged" else "Merge requested"}


async def list_pr_files(owner: str, repo: str, pull_number: int, *, per_page: int = 100, page: int = 1,
                        token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Files changed by a merge request (with unified-diff patches)."""
    data = await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/diffs",
                                params={"per_page": min(per_page, 100), "page": page}, token=token) or []
    return [normalize_diff(d) for d in data]


async def list_pr_reviews(owner: str, repo: str, pull_number: int,
                          token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Approvals on a merge request as ``APPROVED`` reviews."""
    data = await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/approvals", token=token) or {}
    return [
        {"user": _user(entry.get("user")), "state": "APPROVED", "body": ""}
        for entry in data.get("approved_by") or []
    ]


async def create_pr_review(owner: str, repo: str, pull_number: int, *, body: str, event: str = "COMMENT",
                           token: Optional[str] = None) -> Dict[str, Any]:
    """Approve, request changes on, or comment on a merge request."""
    event = (event or "COMMENT").upper()
    if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise HTTPException(status_code=422, detail=f"Unknown review event: {event}")
    note = None
    text = body
    if event == "REQUEST_CHANGES":
        text = f"**Changes requested**\n\n{body}" if body else "**Changes requested**"
    if text:
        note = await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/notes", method="POST",
                                    json={"body": text}, token=token)
    if event == "APPROVE":
        await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/approve", method="POST", token=token)
    state = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}.get(event, "COMMENTED")
    return {"id": (note or {}).get("id"), "state": state, "body": body}


async def add_pr_comment(owner: str, repo: str, pull_number: int, body: str,
                         token: Optional[str] = None) -> Dict[str, Any]:
    """Comment on a merge request."""
    return _note(await gitlab_request(f"{_mr_path(owner, repo, pull_number)}/notes", method="POST",
                                      json={"body": body}, token=token) or {})


# ---------------------------------------------------------------------------
# Issues (GitHub issue shapes)
# ---------------------------------------------------------------------------

def _issue_path(owner: str, repo: str, number: Optional[int] = None) -> str:
    base = f"/projects/{project_ref(owner, repo)}/issues"
    return f"{base}/{int(number)}" if number is not None else base


async def _user_ids(usernames: List[str], token: Optional[str]) -> List[int]:
    ids: List[int] = []
    for name in usernames:
        found = await gitlab_request("/users", params={"username": name}, token=token) or []
        if not found:
            raise HTTPException(status_code=422, detail=f"GitLab user not found: {name}")
        ids.append(found[0]["id"])
    return ids


async def list_issues(owner: str, repo: str, *, state: str = "open", labels: Optional[str] = None,
                      assignee: Optional[str] = None, milestone: Optional[str] = None,
                      sort: str = "created", direction: str = "desc", per_page: int = 30, page: int = 1,
                      token: Optional[str] = None) -> List[Dict[str, Any]]:
    """List project issues."""
    params: Dict[str, Any] = {
        "state": _state_in(state),
        "order_by": "updated_at" if sort == "updated" else "created_at",
        "sort": "asc" if direction == "asc" else "desc",
        "per_page": min(per_page, 100), "page": page,
    }
    if params["state"] == "merged":
        params["state"] = "closed"
    if labels:
        params["labels"] = labels
    if assignee:
        params["assignee_username"] = assignee
    if milestone:
        params["milestone"] = milestone
    data = await gitlab_request(_issue_path(owner, repo), params=params, token=token) or []
    return [normalize_issue(i) for i in data]


async def get_issue(owner: str, repo: str, issue_number: int, token: Optional[str] = None) -> Dict[str, Any]:
    """One issue by its project-scoped number (iid)."""
    return normalize_issue(await gitlab_request(_issue_path(owner, repo, issue_number), token=token) or {})


async def create_issue(owner: str, repo: str, title: str, *, body: Optional[str] = None,
                       labels: Optional[List[str]] = None, assignees: Optional[List[str]] = None,
                       milestone: Optional[int] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """Open an issue."""
    payload: Dict[str, Any] = {"title": title}
    if body is not None:
        payload["description"] = body
    if labels:
        payload["labels"] = ",".join(labels)
    if assignees:
        payload["assignee_ids"] = await _user_ids(assignees, token)
    if milestone is not None:
        payload["milestone_id"] = milestone
    return normalize_issue(await gitlab_request(_issue_path(owner, repo), method="POST",
                                                json=payload, token=token) or {})


async def update_issue(owner: str, repo: str, issue_number: int, *, title: Optional[str] = None,
                       body: Optional[str] = None, state: Optional[str] = None,
                       labels: Optional[List[str]] = None, assignees: Optional[List[str]] = None,
                       milestone: Optional[int] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """Edit an issue; ``state`` open/closed maps to reopen/close."""
    payload: Dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["description"] = body
    if state == "closed":
        payload["state_event"] = "close"
    elif state == "open":
        payload["state_event"] = "reopen"
    if labels is not None:
        payload["labels"] = ",".join(labels)
    if assignees is not None:
        payload["assignee_ids"] = await _user_ids(assignees, token) if assignees else [0]
    if milestone is not None:
        payload["milestone_id"] = milestone
    if not payload:
        return await get_issue(owner, repo, issue_number, token=token)
    return normalize_issue(await gitlab_request(_issue_path(owner, repo, issue_number), method="PUT",
                                                json=payload, token=token) or {})


async def list_issue_comments(owner: str, repo: str, issue_number: int, *, per_page: int = 30,
                              page: int = 1, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """User comments on an issue (system notes are skipped)."""
    data = await gitlab_request(f"{_issue_path(owner, repo, issue_number)}/notes",
                                params={"per_page": min(per_page, 100), "page": page,
                                        "sort": "asc", "order_by": "created_at"},
                                token=token) or []
    return [_note(n) for n in data if not n.get("system")]


async def add_issue_comment(owner: str, repo: str, issue_number: int, body: str,
                            token: Optional[str] = None) -> Dict[str, Any]:
    """Comment on an issue."""
    return _note(await gitlab_request(f"{_issue_path(owner, repo, issue_number)}/notes", method="POST",
                                      json={"body": body}, token=token) or {})


async def add_labels(owner: str, repo: str, issue_number: int, labels: List[str],
                     token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Add labels; returns the issue's full label list."""
    data = await gitlab_request(_issue_path(owner, repo, issue_number), method="PUT",
                                json={"add_labels": ",".join(labels)}, token=token) or {}
    return [{"name": name} for name in data.get("labels") or []]


async def remove_label(owner: str, repo: str, issue_number: int, label: str,
                       token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remove one label; returns the remaining labels."""
    data = await gitlab_request(_issue_path(owner, repo, issue_number), method="PUT",
                                json={"remove_labels": label}, token=token) or {}
    return [{"name": name} for name in data.get("labels") or []]


async def set_assignees(owner: str, repo: str, issue_number: int, assignees: List[str],
                        token: Optional[str] = None) -> Dict[str, Any]:
    """Replace the issue's assignees."""
    return await update_issue(owner, repo, issue_number, assignees=assignees, token=token)


# ---------------------------------------------------------------------------
# Search (project scope)
# ---------------------------------------------------------------------------

async def search_code(query: str, *, owner: Optional[str] = None, repo: Optional[str] = None,
                      language: Optional[str] = None, path: Optional[str] = None,
                      per_page: int = 30, page: int = 1, token: Optional[str] = None) -> Dict[str, Any]:
    """Search file contents of one project (GitLab code search is project-scoped here).

    ``language`` has no GitLab equivalent in basic search and is ignored.
    """
    del language
    if not (owner and repo):
        raise HTTPException(status_code=422, detail="GitLab code search needs a project.")
    data = await gitlab_request(
        f"/projects/{project_ref(owner, repo)}/search",
        params={"scope": "blobs", "search": query, "per_page": min(per_page, 100), "page": page},
        token=token,
    ) or []
    items = []
    for hit in data:
        file_path = hit.get("path") or hit.get("filename") or ""
        if path and not file_path.startswith(path.strip("/")):
            continue
        items.append({
            "name": file_path.rsplit("/", 1)[-1], "path": file_path,
            "repository": {"full_name": f"{owner}/{repo}"},
            "text_matches": [{"fragment": hit.get("data", "")}],
            "ref": hit.get("ref"), "startline": hit.get("startline"),
        })
    return {"total_count": len(items), "incomplete_results": False, "items": items}


async def search_issues(query: str, *, owner: Optional[str] = None, repo: Optional[str] = None,
                        state: Optional[str] = None, label: Optional[str] = None,
                        is_pr: Optional[bool] = None, per_page: int = 30, page: int = 1,
                        token: Optional[str] = None) -> Dict[str, Any]:
    """Search issues (or merge requests when ``is_pr``) of one project."""
    if not (owner and repo):
        raise HTTPException(status_code=422, detail="GitLab issue search needs a project.")
    params: Dict[str, Any] = {"search": query, "per_page": min(per_page, 100), "page": page}
    if state:
        params["state"] = _state_in(state)
    if label:
        params["labels"] = label
    if is_pr:
        data = await gitlab_request(_mr_path(owner, repo), params=params, token=token) or []
        items = [normalize_merge_request(mr) for mr in data]
    else:
        data = await gitlab_request(_issue_path(owner, repo), params=params, token=token) or []
        items = [normalize_issue(i) for i in data]
    return {"total_count": len(items), "incomplete_results": False, "items": items}
