"""GitLab OAuth and repository routes (``/api/gitlab/...``).

Projects are addressed by their stable numeric id, which works for any depth of
nested groups and survives renames and transfers. Every route resolves the
caller's *GitLab* credential only — the server-side session vault, or an
explicit ``Authorization`` header from an API client — and sends it only to the
configured GitLab instance (:func:`gitpilot.gitlab_api.gitlab_base_url`).

The REST client lives in :mod:`gitpilot.gitlab_api`; agents reach GitLab through
:mod:`gitpilot.scm` with the same code paths they use for GitHub.
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import gitlab_api as gl
from .auth.session_router import OAuthCallbackBody, complete_oauth
from .auth.web_session import current_session, resolve_provider_token

_router = APIRouter(tags=["gitlab"])

# Back-compat for imports of the first release's module internals.
GITLAB_URL = gl.DEFAULT_GITLAB_URL
OAuthCallback = OAuthCallbackBody


class TokenPayload(BaseModel):
    """GitLab token to validate (API clients that keep their own token)."""

    access_token: str


class FileUpdate(BaseModel):
    """A repository file change on a selected branch."""

    path: str = Field(min_length=1)
    content: str
    message: str = Field(min_length=1)
    branch: str = Field(min_length=1)


class BranchCreate(BaseModel):
    """A new branch and the ref it starts from."""

    name: str = Field(min_length=1, max_length=255)
    ref: str = Field(default="HEAD", min_length=1, max_length=255)


class MergeRequestCreate(BaseModel):
    """A merge request from ``source_branch`` into ``target_branch``."""

    title: str = Field(min_length=1, max_length=255)
    source_branch: str = Field(min_length=1)
    target_branch: Optional[str] = None
    description: Optional[str] = None
    draft: bool = False


class MergeRequestUpdate(BaseModel):
    """Editable merge-request fields; ``state`` is ``open`` or ``closed``."""

    title: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = Field(default=None, pattern="^(open|closed)$")
    target_branch: Optional[str] = None


class MergeBody(BaseModel):
    """How to merge: ``merge`` or ``squash``."""

    merge_method: str = Field(default="merge", pattern="^(merge|squash)$")
    commit_message: Optional[str] = None


class ReviewBody(BaseModel):
    """Approve, request changes or comment."""

    event: str = Field(default="COMMENT", pattern="^(APPROVE|REQUEST_CHANGES|COMMENT)$")
    body: str = ""


class NoteBody(BaseModel):
    """A comment."""

    body: str = Field(min_length=1, max_length=100_000)


class IssueCreate(BaseModel):
    """A new issue."""

    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None


class IssueUpdate(BaseModel):
    """Editable issue fields; ``state`` is ``open`` or ``closed``."""

    title: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = Field(default=None, pattern="^(open|closed)$")
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None


def _token(authorization: Optional[str]) -> Optional[str]:
    """The caller's GitLab credential (``None`` falls back to the server token, if any)."""
    return resolve_provider_token("gitlab", authorization)


def _require(authorization: Optional[str]) -> Optional[str]:
    token = _token(authorization)
    if token is None and not _server_token():
        raise HTTPException(status_code=401, detail="Connect your GitLab account first.")
    return token


def _server_token() -> bool:
    return bool(os.getenv("GITPILOT_GITLAB_TOKEN") or os.getenv("GITLAB_TOKEN"))


def _pid(project_id: int) -> tuple[str, str]:
    # (owner, repo) form understood by gitlab_api: empty owner + numeric id.
    return "", str(int(project_id))


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

@_router.get("/api/gitlab/auth/url")
async def gitlab_auth_url(return_to: Optional[str] = Query(None, max_length=300)):
    """Start GitLab OAuth with server-side state and S256 PKCE."""
    from .auth import oauth

    return oauth.start("gitlab", current_session(), return_to=return_to)


@_router.post("/api/gitlab/auth/callback")
async def gitlab_auth_callback(payload: OAuthCallbackBody, request: Request, response: Response):
    """Exchange the one-time code server-side and connect GitLab to the session.

    The access and refresh tokens are stored encrypted on the server; the
    response describes the session and the GitLab identity only.
    """
    return await complete_oauth("gitlab", payload.code, payload.state, request, response)


@_router.post("/api/gitlab/auth/validate")
async def gitlab_validate_token(payload: TokenPayload):
    """Validate a GitLab token held by an API client."""
    from .auth import oauth

    try:
        profile = await oauth.fetch_profile("gitlab", payload.access_token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return {"authenticated": False}
        raise
    return {"authenticated": True, "user": profile}


# ---------------------------------------------------------------------------
# Projects and groups
# ---------------------------------------------------------------------------

@_router.get("/api/gitlab/projects")
async def gitlab_projects(
    query: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    min_access_level: Optional[int] = Query(None, ge=10, le=50),
    authorization: Optional[str] = Header(None),
):
    """Projects the user can access, including private ones and nested-group projects."""
    token = _token(authorization)
    if token is None and not _server_token():
        return {"repositories": [], "page": page, "per_page": per_page, "total_count": 0,
                "has_more": False, "gitlab_connected": False}
    result = await gl.list_projects(query=query, page=page, per_page=per_page,
                                    min_access_level=min_access_level, token=token)
    result["gitlab_connected"] = True
    result["host"] = gl.gitlab_host()
    return result


@_router.get("/api/gitlab/groups")
async def gitlab_groups(
    query: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Groups and subgroups the user belongs to (``full_path`` shows the nesting)."""
    token = _require(authorization)
    return {"groups": await gl.list_groups(query=query, page=page, per_page=per_page, token=token)}


@_router.get("/api/gitlab/groups/{group_id}/projects")
async def gitlab_group_projects(
    group_id: int,
    query: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Projects of a group and all its subgroups."""
    token = _require(authorization)
    return await gl.list_group_projects(str(group_id), query=query, page=page, per_page=per_page, token=token)


@_router.get("/api/gitlab/projects/{project_id}")
async def gitlab_project(project_id: int, authorization: Optional[str] = Header(None)):
    """Project metadata with the user's effective permissions (pull / push / admin)."""
    token = _require(authorization)
    return await gl.get_repo(*_pid(project_id), token=token)


# ---------------------------------------------------------------------------
# Tree, files, branches, history
# ---------------------------------------------------------------------------

@_router.get("/api/gitlab/projects/{project_id}/tree")
async def gitlab_tree(
    project_id: int,
    ref: Optional[str] = None,
    include_dirs: bool = True,
    authorization: Optional[str] = Header(None),
):
    """Recursive repository tree (directories included, for the file browser)."""
    token = _require(authorization)
    files = await gl.get_repo_tree(*_pid(project_id), token=token, ref=ref or "HEAD", include_dirs=include_dirs)
    return {"files": files}


@_router.get("/api/gitlab/projects/{project_id}/file")
async def gitlab_file(
    project_id: int,
    path: str = Query(..., min_length=1),
    ref: str = "HEAD",
    authorization: Optional[str] = Header(None),
):
    """Decoded file content."""
    token = _require(authorization)
    return {"path": path, "content": await gl.get_file(*_pid(project_id), path, token=token, ref=ref)}


@_router.post("/api/gitlab/projects/{project_id}/file")
async def gitlab_write_file(
    project_id: int,
    payload: FileUpdate,
    authorization: Optional[str] = Header(None),
):
    """Create or update a file on a branch as one commit."""
    token = _require(authorization)
    result = await gl.put_file(*_pid(project_id), payload.path, payload.content, payload.message,
                               token=token, branch=payload.branch)
    return {**result, "branch": payload.branch, "provider": "gitlab"}


@_router.delete("/api/gitlab/projects/{project_id}/file")
async def gitlab_delete_file(
    project_id: int,
    path: str = Query(..., min_length=1),
    branch: str = Query(..., min_length=1),
    message: str = Query(..., min_length=1, max_length=2000),
    authorization: Optional[str] = Header(None),
):
    """Delete a file on a branch as one commit."""
    token = _require(authorization)
    result = await gl.delete_file(*_pid(project_id), path, message, token=token, branch=branch)
    return {**result, "branch": branch, "provider": "gitlab"}


@_router.get("/api/gitlab/projects/{project_id}/branches")
async def gitlab_branches(
    project_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
    query: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Branches in the shared branch-picker response shape."""
    token = _require(authorization)
    info = await gl.get_repo(*_pid(project_id), token=token)
    listing = await gl.list_branches(*_pid(project_id), query=query, page=page, per_page=per_page, token=token)
    branches = sorted(listing["branches"], key=lambda b: (0 if b["is_default"] else 1, b["name"].lower()))
    return {
        "repository": info["full_name"], "default_branch": info["default_branch"],
        "page": page, "per_page": per_page, "branches": branches, "has_more": listing["has_more"],
    }


@_router.post("/api/gitlab/projects/{project_id}/branches", status_code=201)
async def gitlab_create_branch(
    project_id: int, payload: BranchCreate, authorization: Optional[str] = Header(None),
):
    """Create a branch from ``ref`` (``HEAD`` = default branch)."""
    token = _require(authorization)
    ref = await gl.create_branch(*_pid(project_id), payload.name, payload.ref, token=token)
    return {"ref": ref, "name": payload.name, "provider": "gitlab"}


@_router.get("/api/gitlab/projects/{project_id}/commits")
async def gitlab_commits(
    project_id: int,
    ref: Optional[str] = None,
    path: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Commit history of a ref (optionally of one path)."""
    token = _require(authorization)
    return {"commits": await gl.list_commits(*_pid(project_id), ref=ref, path=path, page=page,
                                             per_page=per_page, token=token)}


@_router.get("/api/gitlab/projects/{project_id}/compare")
async def gitlab_compare(
    project_id: int,
    base: str = Query(..., min_length=1),
    head: str = Query(..., min_length=1),
    authorization: Optional[str] = Header(None),
):
    """Diff between two refs (files with unified-diff patches, and commits)."""
    token = _require(authorization)
    return await gl.compare_refs(*_pid(project_id), base, head, token=token)


# ---------------------------------------------------------------------------
# Merge requests
# ---------------------------------------------------------------------------

@_router.get("/api/gitlab/projects/{project_id}/merge_requests")
async def gitlab_list_mrs(
    project_id: int,
    state: str = Query("open", pattern="^(open|closed|merged|all)$"),
    source_branch: Optional[str] = None,
    target_branch: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Merge requests (pull-request shape: ``number`` is the MR iid)."""
    token = _require(authorization)
    items = await gl.list_pull_requests(*_pid(project_id), state=state, head=source_branch,
                                        base=target_branch, page=page, per_page=per_page, token=token)
    return {"merge_requests": items}


@_router.post("/api/gitlab/projects/{project_id}/merge_requests", status_code=201)
async def gitlab_create_mr(
    project_id: int, payload: MergeRequestCreate, authorization: Optional[str] = Header(None),
):
    """Open a merge request (target defaults to the project's default branch)."""
    token = _require(authorization)
    target = payload.target_branch or (await gl.get_repo(*_pid(project_id), token=token))["default_branch"]
    if target == payload.source_branch:
        raise HTTPException(status_code=422, detail="Source and target branch are the same.")
    return await gl.create_pull_request(*_pid(project_id), title=payload.title, head=payload.source_branch,
                                        base=target, body=payload.description, draft=payload.draft, token=token)


@_router.get("/api/gitlab/projects/{project_id}/merge_requests/{iid}")
async def gitlab_get_mr(project_id: int, iid: int, authorization: Optional[str] = Header(None)):
    """One merge request."""
    token = _require(authorization)
    return await gl.get_pull_request(*_pid(project_id), iid, token=token)


@_router.put("/api/gitlab/projects/{project_id}/merge_requests/{iid}")
async def gitlab_update_mr(
    project_id: int, iid: int, payload: MergeRequestUpdate, authorization: Optional[str] = Header(None),
):
    """Edit, close or reopen a merge request."""
    token = _require(authorization)
    return await gl.update_pull_request(*_pid(project_id), iid, title=payload.title, body=payload.description,
                                        state=payload.state, base=payload.target_branch, token=token)


@_router.put("/api/gitlab/projects/{project_id}/merge_requests/{iid}/merge")
async def gitlab_merge_mr(
    project_id: int, iid: int, payload: MergeBody, authorization: Optional[str] = Header(None),
):
    """Merge (or squash-merge) a merge request."""
    token = _require(authorization)
    return await gl.merge_pull_request(*_pid(project_id), iid, merge_method=payload.merge_method,
                                       commit_message=payload.commit_message, token=token)


@_router.get("/api/gitlab/projects/{project_id}/merge_requests/{iid}/changes")
async def gitlab_mr_changes(project_id: int, iid: int, authorization: Optional[str] = Header(None)):
    """Files changed by a merge request, with patches."""
    token = _require(authorization)
    return {"files": await gl.list_pr_files(*_pid(project_id), iid, token=token)}


@_router.post("/api/gitlab/projects/{project_id}/merge_requests/{iid}/reviews")
async def gitlab_review_mr(
    project_id: int, iid: int, payload: ReviewBody, authorization: Optional[str] = Header(None),
):
    """Approve, request changes on, or comment on a merge request."""
    token = _require(authorization)
    return await gl.create_pr_review(*_pid(project_id), iid, body=payload.body, event=payload.event, token=token)


@_router.post("/api/gitlab/projects/{project_id}/merge_requests/{iid}/notes", status_code=201)
async def gitlab_mr_note(
    project_id: int, iid: int, payload: NoteBody, authorization: Optional[str] = Header(None),
):
    """Comment on a merge request."""
    token = _require(authorization)
    return await gl.add_pr_comment(*_pid(project_id), iid, payload.body, token=token)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

@_router.get("/api/gitlab/projects/{project_id}/issues")
async def gitlab_list_issues(
    project_id: int,
    state: str = Query("open", pattern="^(open|closed|all)$"),
    labels: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    """Project issues (GitHub issue shape: ``number`` is the issue iid)."""
    token = _require(authorization)
    return {"issues": await gl.list_issues(*_pid(project_id), state=state, labels=labels, page=page,
                                           per_page=per_page, token=token)}


@_router.post("/api/gitlab/projects/{project_id}/issues", status_code=201)
async def gitlab_create_issue(
    project_id: int, payload: IssueCreate, authorization: Optional[str] = Header(None),
):
    """Open an issue."""
    token = _require(authorization)
    return await gl.create_issue(*_pid(project_id), title=payload.title, body=payload.description,
                                 labels=payload.labels, assignees=payload.assignees, token=token)


@_router.get("/api/gitlab/projects/{project_id}/issues/{iid}")
async def gitlab_get_issue(project_id: int, iid: int, authorization: Optional[str] = Header(None)):
    """One issue."""
    token = _require(authorization)
    return await gl.get_issue(*_pid(project_id), iid, token=token)


@_router.patch("/api/gitlab/projects/{project_id}/issues/{iid}")
async def gitlab_update_issue(
    project_id: int, iid: int, payload: IssueUpdate, authorization: Optional[str] = Header(None),
):
    """Edit, close or reopen an issue."""
    token = _require(authorization)
    return await gl.update_issue(*_pid(project_id), iid, title=payload.title, body=payload.description,
                                 state=payload.state, labels=payload.labels, assignees=payload.assignees,
                                 token=token)


@_router.get("/api/gitlab/projects/{project_id}/issues/{iid}/notes")
async def gitlab_issue_notes(
    project_id: int, iid: int, page: int = Query(1, ge=1), authorization: Optional[str] = Header(None),
):
    """Comments on an issue."""
    token = _require(authorization)
    return {"comments": await gl.list_issue_comments(*_pid(project_id), iid, page=page, token=token)}


@_router.post("/api/gitlab/projects/{project_id}/issues/{iid}/notes", status_code=201)
async def gitlab_issue_note(
    project_id: int, iid: int, payload: NoteBody, authorization: Optional[str] = Header(None),
):
    """Comment on an issue."""
    token = _require(authorization)
    return await gl.add_issue_comment(*_pid(project_id), iid, payload.body, token=token)


def build_gitlab_router() -> APIRouter:
    """Return the GitLab OAuth and repository router."""
    return _router


__all__: List[str] = ["build_gitlab_router"]
