"""GitLab REST client: project discovery, nested groups, files, branches, merge
requests, issues and error handling — against an in-process fake GitLab."""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from fastapi import HTTPException

from gitpilot import gitlab_api as gl
from gitpilot import scm
from tests._forge_fakes import GITLAB, FakeForges, gitlab_project

ENC = "enterprise%2Fteam%2Frepo"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def forge(monkeypatch):
    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    monkeypatch.delenv("GITPILOT_GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    return FakeForges().install(monkeypatch)


# --- host configuration ------------------------------------------------------

def test_default_host_is_gitlab_com(monkeypatch):
    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)
    assert gl.gitlab_api_url() == "https://gitlab.com/api/v4"


@pytest.mark.parametrize("bad", [
    "http://gitlab.example.com",          # plain http to a remote host
    "https://user:pw@gitlab.example.com",  # credentials in the URL
    "ftp://gitlab.example.com",
    "https://gitlab.example.com/?x=1",
])
def test_misconfigured_host_is_refused(monkeypatch, bad):
    monkeypatch.setenv("GITLAB_BASE_URL", bad)
    with pytest.raises(HTTPException) as exc:
        gl.gitlab_base_url()
    assert exc.value.status_code == 503


def test_self_managed_host_is_used(forge):
    forge.on("GET", GITLAB, "/api/v4/user", {"id": 1, "username": "dev"})
    assert run(gl.get_current_user(token="glpat-x"))["username"] == "dev"
    assert forge.calls[0].host == GITLAB and forge.calls[0].bearer == "glpat-x"


# --- projects and nested groups ----------------------------------------------

def test_projects_map_nested_groups_private_and_permissions(forge):
    forge.on("GET", GITLAB, "/api/v4/projects", httpx.Response(
        200, json=[gitlab_project(113, "enterprise/team/repo", level=40),
                   gitlab_project(7, "solo/tool", level=20, visibility="public")],
        headers={"x-total": "2"},
    ))
    out = run(gl.list_projects(query="re", token="t"))
    first, second = out["repositories"]
    assert first["full_name"] == "enterprise/team/repo"
    assert first["owner"] == "enterprise/team" and first["name"] == "repo"
    assert first["provider"] == "gitlab" and first["private"] is True
    assert first["permissions"]["push"] and first["permissions"]["admin"]
    assert second["private"] is False and not second["permissions"]["push"]
    params = forge.calls[0].params
    assert params["membership"] == "true" and params["search"] == "re"
    assert out["total_count"] == 2 and out["has_more"] is False


def test_group_projects_include_subgroups(forge):
    forge.on("GET", GITLAB, "/api/v4/groups/42/projects", [gitlab_project()])
    out = run(gl.list_group_projects("42", token="t"))
    assert forge.calls[0].params["include_subgroups"] == "true"
    assert out["repositories"][0]["full_name"] == "enterprise/team/repo"


def test_path_and_bound_numeric_id_addressing(forge):
    forge.on("GET", GITLAB, f"/api/v4/projects/{ENC}", gitlab_project())
    forge.on("GET", GITLAB, "/api/v4/projects/113", gitlab_project())
    run(gl.get_repo("enterprise/team", "repo", token="t"))
    with scm.execution_context("gitlab", "t", repo_full_name="enterprise/team/repo", repo_id=113):
        run(gl.get_repo("enterprise/team", "repo"))
        # Another repository never borrows the bound id.
        assert gl.project_ref("other", "proj") == "other%2Fproj"
    assert [c.path for c in forge.calls] == ["/api/v4/projects/enterprise/team/repo", "/api/v4/projects/113"]


@pytest.mark.parametrize("owner,repo", [("a/../b", "c"), ("", ""), ("a//b", "c")])
def test_invalid_project_paths_are_rejected(owner, repo):
    with pytest.raises(HTTPException) as exc:
        gl.project_ref(owner, repo)
    assert exc.value.status_code == 400


# --- tree, files, branches, commits ---------------------------------------------

def test_tree_is_paginated_and_files_only_by_default(forge):
    def tree(req, _m):
        page = req.url.params.get("page")
        if page == "1":
            return httpx.Response(200, json=[{"path": "src", "type": "tree"}, {"path": "src/a.py", "type": "blob"}],
                                  headers={"x-next-page": "2"})
        return httpx.Response(200, json=[{"path": "README.md", "type": "blob"}])
    forge.on("GET", GITLAB, "/api/v4/projects/113/repository/tree", tree)
    assert run(gl.get_repo_tree("", "113", token="t", ref="dev")) == [
        {"path": "src/a.py", "type": "blob"}, {"path": "README.md", "type": "blob"}]
    assert forge.calls[0].params["ref"] == "dev"
    with_dirs = run(gl.get_repo_tree("", "113", token="t", include_dirs=True))
    assert {"path": "src", "type": "tree"} in with_dirs


def test_read_file_resolves_head_to_default_branch(forge):
    forge.on("GET", GITLAB, "/api/v4/projects/113", gitlab_project(default_branch="develop"))
    forge.on("GET", GITLAB, "/api/v4/projects/113/repository/files/src%2Fa.py",
             {"content": base64.b64encode(b"print(1)\n").decode()})
    assert run(gl.get_file("", "113", "src/a.py", token="t")) == "print(1)\n"
    assert forge.calls[-1].params["ref"] == "develop"


def test_put_file_creates_then_updates_with_one_commit_each(forge):
    existing = {"src/a.py"}

    def head(req, m):
        path = m.group(1).replace("%2F", "/")
        return httpx.Response(200 if path in existing else 404)

    forge.on("HEAD", GITLAB, "/api/v4/projects/113/repository/files/(.+)", head)
    forge.on("POST", GITLAB, "/api/v4/projects/113/repository/commits",
             {"id": "abc123", "web_url": f"https://{GITLAB}/c/abc123"})
    created = run(gl.put_file("", "113", "src/new.py", "x = 1\n", "add", token="t", branch="feat"))
    updated = run(gl.put_file("", "113", "src/a.py", "y = 2\n", "edit", token="t", branch="feat"))
    commits = [c.body for c in forge.calls if c.method == "POST"]
    assert commits[0]["actions"][0]["action"] == "create"
    assert commits[1]["actions"][0]["action"] == "update"
    assert base64.b64decode(commits[1]["actions"][0]["content"]) == b"y = 2\n"
    assert commits[0]["branch"] == "feat" and commits[0]["commit_message"] == "add"
    assert created["commit_sha"] == "abc123" and updated["path"] == "src/a.py"


def test_delete_missing_file_is_404_and_traversal_rejected(forge):
    forge.on("HEAD", GITLAB, "/api/v4/projects/113/repository/files/.+", httpx.Response(404))
    with pytest.raises(HTTPException) as missing:
        run(gl.delete_file("", "113", "gone.py", "rm", token="t", branch="main"))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as bad:
        run(gl.put_file("", "113", "../secret", "x", "m", token="t", branch="main"))
    assert bad.value.status_code == 400


def test_create_branch_from_head_uses_default_branch(forge):
    forge.on("GET", GITLAB, "/api/v4/projects/113", gitlab_project(default_branch="trunk"))
    forge.on("POST", GITLAB, "/api/v4/projects/113/repository/branches", {"name": "feat"})
    assert run(gl.create_branch("", "113", "feat", token="t")) == "refs/heads/feat"
    assert forge.calls[-1].params == {"branch": "feat", "ref": "trunk"}


def test_compare_and_commits_normalised(forge):
    forge.on("GET", GITLAB, "/api/v4/projects/113/repository/compare", {
        "commits": [{"id": "a" * 40, "short_id": "aaaaaaaa", "title": "t", "message": "m",
                     "author_name": "Dev", "authored_date": "2026-01-01"}],
        "diffs": [{"old_path": "a", "new_path": "b", "renamed_file": True, "diff": "@@\n-x\n+y\n+z\n"}],
    })
    out = run(gl.compare_refs("", "113", "main", "feat", token="t"))
    assert out["files"][0] == {"filename": "b", "status": "renamed", "additions": 2, "deletions": 1,
                               "changes": 3, "patch": "@@\n-x\n+y\n+z\n", "previous_filename": "a"}
    assert out["commits"][0]["sha"] == "a" * 40


# --- merge requests and issues ----------------------------------------------------

MR = {"id": 9, "iid": 3, "title": "Feat", "description": "d", "state": "opened",
      "source_branch": "feat", "target_branch": "main", "sha": "s", "web_url": "u",
      "author": {"username": "dev"}, "detailed_merge_status": "mergeable"}


def test_merge_request_lifecycle_in_pull_request_shape(forge):
    forge.on("POST", GITLAB, "/api/v4/projects/113/merge_requests", MR)
    forge.on("GET", GITLAB, "/api/v4/projects/113/merge_requests", [MR, {**MR, "iid": 4, "state": "merged"}])
    forge.on("PUT", GITLAB, "/api/v4/projects/113/merge_requests/3/merge",
             {"state": "merged", "merge_commit_sha": "m1"})
    forge.on("POST", GITLAB, "/api/v4/projects/113/merge_requests/3/approve", {})
    forge.on("POST", GITLAB, "/api/v4/projects/113/merge_requests/3/notes", {"id": 5, "body": "lgtm"})

    pr = run(gl.create_pull_request("", "113", title="Feat", head="me:feat", base="main", draft=True, token="t"))
    sent = forge.calls[0].body
    assert sent["source_branch"] == "feat" and sent["title"] == "Draft: Feat"
    assert pr["number"] == 3 and pr["head"]["ref"] == "feat" and pr["state"] == "open" and pr["mergeable"]

    listed = run(gl.list_pull_requests("", "113", state="all", token="t"))
    assert listed[1]["state"] == "closed" and listed[1]["merged"] is True
    assert forge.calls[1].params["state"] == "all"

    assert run(gl.merge_pull_request("", "113", 3, merge_method="squash", token="t"))["merged"] is True
    assert forge.calls[2].body["squash"] is True
    with pytest.raises(HTTPException):
        run(gl.merge_pull_request("", "113", 3, merge_method="rebase", token="t"))

    review = run(gl.create_pr_review("", "113", 3, body="lgtm", event="APPROVE", token="t"))
    assert review["state"] == "APPROVED"
    assert [c.path.rsplit("/", 1)[-1] for c in forge.calls[-2:]] == ["notes", "approve"]


def test_issues_with_assignees_labels_and_comments(forge):
    issue = {"id": 1, "iid": 12, "title": "Bug", "description": "b", "state": "opened",
             "labels": ["bug"], "assignees": [{"username": "ann"}], "author": {"username": "dev"},
             "web_url": "u", "user_notes_count": 1}
    forge.on("GET", GITLAB, "/api/v4/users", [{"id": 77, "username": "ann"}])
    forge.on("POST", GITLAB, "/api/v4/projects/113/issues", issue)
    forge.on("PUT", GITLAB, "/api/v4/projects/113/issues/12", {**issue, "state": "closed"})
    forge.on("GET", GITLAB, "/api/v4/projects/113/issues/12/notes",
             [{"id": 1, "body": "changed label", "system": True}, {"id": 2, "body": "hi", "author": {"username": "x"}}])

    created = run(gl.create_issue("", "113", "Bug", labels=["bug", "p1"], assignees=["ann"], token="t"))
    post = [c for c in forge.calls if c.method == "POST"][0]
    assert post.body["labels"] == "bug,p1" and post.body["assignee_ids"] == [77]
    assert created["number"] == 12 and created["labels"] == [{"name": "bug"}]
    closed = run(gl.update_issue("", "113", 12, state="closed", token="t"))
    assert forge.calls[-1].body == {"state_event": "close"} and closed["state"] == "closed"
    comments = run(gl.list_issue_comments("", "113", 12, token="t"))
    assert [c["body"] for c in comments] == ["hi"]


# --- errors -----------------------------------------------------------------------

def test_rate_limit_and_auth_errors_are_mapped(forge):
    forge.on("GET", GITLAB, "/api/v4/projects/1", httpx.Response(429, headers={"retry-after": "30"}))
    forge.on("GET", GITLAB, "/api/v4/projects/2", httpx.Response(401, json={"message": "401 Unauthorized"}))
    forge.on("POST", GITLAB, "/api/v4/projects/3/repository/branches",
             httpx.Response(400, json={"message": "Branch already exists"}))
    with pytest.raises(HTTPException) as limited:
        run(gl.get_repo("", "1", token="t"))
    assert limited.value.status_code == 429 and limited.value.headers["Retry-After"] == "30"
    with pytest.raises(HTTPException) as expired:
        run(gl.get_repo("", "2", token="t"))
    assert expired.value.status_code == 401 and "reconnect" in expired.value.detail.lower()
    with pytest.raises(HTTPException) as conflict:
        run(gl.create_branch("", "3", "x", "main", token="t"))
    assert conflict.value.detail == "Branch already exists"


def test_no_credential_is_401(forge):
    with pytest.raises(HTTPException) as exc:
        run(gl.get_repo("", "1"))
    assert exc.value.status_code == 401
    assert forge.calls == []
