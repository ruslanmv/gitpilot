"""AI execution on GitLab end to end: an approved plan commits to a new branch of
a nested-group project and opens a merge request — with the same executor GitHub
uses, a stubbed LLM, and a fake GitLab. Also checks the HTTP routes bind the
repository's provider and the session's GitLab credential."""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from gitpilot import agentic, scm
from gitpilot.agentic import PlanFile, PlanResult, PlanStep
from tests._forge_fakes import GITHUB_API, GITLAB, FakeForges, gitlab_project

PID = 113
FULL = "acme/platform/app"


def run(coro):
    return asyncio.run(coro)


class _Stub:
    sequential = "sequential"

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def gitlab(monkeypatch, tmp_path):
    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    for var in ("GITPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GITPILOT_GITLAB_TOKEN", "GITLAB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    # No LLM: every generated file body is fixed.
    monkeypatch.setattr(agentic, "_build_llm", lambda: object())
    monkeypatch.setattr(agentic, "_crewai", lambda: {"Agent": _Stub, "Task": _Stub, "Crew": _Stub, "Process": _Stub})

    async def fake_agent_call(ctx, func, *, label="agent"):
        return "generated = True\n"

    monkeypatch.setattr(agentic, "_guarded_agent_call", fake_agent_call)

    fake = FakeForges().install(monkeypatch)
    files = {"src/a.py": b"old = 1\n", "old.py": b"x\n"}
    branches = {"main"}

    def head(req, m):
        return httpx.Response(200 if m.group(1).replace("%2F", "/") in files else 404)

    def read(req, m):
        path = m.group(1).replace("%2F", "/")
        if path not in files:
            return httpx.Response(404, json={"message": "404 File Not Found"})
        return httpx.Response(200, json={"content": base64.b64encode(files[path]).decode()})

    def create_branch(req, m):
        branches.add(req.url.params["branch"])
        return httpx.Response(201, json={"name": req.url.params["branch"]})

    fake.on("GET", GITLAB, f"/api/v4/projects/{PID}", gitlab_project(PID, FULL, default_branch="main"))
    fake.on("POST", GITLAB, f"/api/v4/projects/{PID}/repository/branches", create_branch)
    fake.on("HEAD", GITLAB, f"/api/v4/projects/{PID}/repository/files/(.+)", head)
    fake.on("GET", GITLAB, f"/api/v4/projects/{PID}/repository/files/(.+)", read)
    fake.on("POST", GITLAB, f"/api/v4/projects/{PID}/repository/commits",
            lambda req, m: httpx.Response(201, json={"id": "c0ffee", "web_url": "u"}))
    fake.on("POST", GITLAB, f"/api/v4/projects/{PID}/merge_requests", lambda req, m: httpx.Response(201, json={
        "iid": 5, "title": req.content and "x", "state": "opened", "source_branch": "feat",
        "target_branch": "main", "web_url": f"https://{GITLAB}/{FULL}/-/merge_requests/5"}))
    fake.branches = branches  # type: ignore[attr-defined]
    return fake


PLAN = PlanResult(goal="Add feature", summary="s", steps=[
    PlanStep(step_number=1, title="Change files", description="d", files=[
        PlanFile(path="src/new.py", action="CREATE"),
        PlanFile(path="src/a.py", action="MODIFY"),
        PlanFile(path="old.py", action="DELETE"),
    ]),
])


def test_approved_plan_commits_to_a_new_gitlab_branch(gitlab):
    with scm.execution_context("gitlab", "gl-token", repo_full_name=FULL, repo_id=PID):
        result = run(agentic.execute_plan_lite(PLAN, FULL, token="gl-token", branch_name="feat"))

    assert result["branch"] == "feat"
    assert result["branch_url"] == f"https://{GITLAB}/{FULL}/-/tree/feat"
    assert "feat" in gitlab.branches
    commits = [c.body for c in gitlab.calls if c.path.endswith("/repository/commits")]
    assert [(c["actions"][0]["action"], c["actions"][0]["file_path"], c["branch"]) for c in commits] == [
        ("create", "src/new.py", "feat"), ("update", "src/a.py", "feat"), ("delete", "old.py", "feat")]
    # Every call went to GitLab, by stable project id, with the GitLab token.
    assert gitlab.to(GITHUB_API) == []
    assert {c.bearer for c in gitlab.calls} == {"gl-token"}
    assert all(f"/projects/{PID}" in c.path for c in gitlab.calls)


def test_execution_ends_in_a_merge_request(gitlab):
    with scm.execution_context("gitlab", "gl-token", repo_full_name=FULL, repo_id=PID):
        mr = run(agentic.create_pr_after_execution(FULL, "feat", "Add feature",
                                                   {"steps": [{"summary": "Created src/new.py"}]},
                                                   token="gl-token"))
    assert mr["number"] == 5 and mr["kind"] == "merge_request"
    body = [c.body for c in gitlab.calls if c.path.endswith("/merge_requests")][0]
    assert body["source_branch"] == "feat" and body["target_branch"] == "main"
    assert "GitPilot Auto-MR" in body["description"]


def test_same_executor_still_targets_github_for_github_repos(gitlab, monkeypatch):
    calls = []

    async def fake_put(owner, repo, path, content, message, token=None, branch=None):
        calls.append((owner, repo, path, branch, token))
        return {"path": path, "commit_sha": "s"}

    async def noop(*a, **k):
        return "refs/heads/feat"

    monkeypatch.setattr("gitpilot.github_api.put_file", fake_put)
    monkeypatch.setattr("gitpilot.github_api.create_branch", noop)
    plan = PlanResult(goal="g", summary="s", steps=[
        PlanStep(step_number=1, title="t", description="d", files=[PlanFile(path="a.py", action="CREATE")])])
    with scm.execution_context("github", "gho_x"):
        result = run(agentic.execute_plan_lite(plan, "acme/app", token="gho_x", branch_name="feat"))
    assert calls == [("acme", "app", "a.py", "feat", "gho_x")]
    assert result["branch_url"] == "https://github.com/acme/app/tree/feat"
    assert gitlab.to(GITLAB) == []


# --- HTTP routes bind the provider + session credential ---------------------------

@pytest.fixture
def app_client(gitlab, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from gitpilot import _api_app
    from gitpilot.auth import crypto
    from gitpilot.auth import web_session as ws

    from gitpilot.session import SessionManager

    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITPILOT_TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"z" * 32).decode())
    monkeypatch.setattr(_api_app, "_session_mgr", SessionManager(root=tmp_path / "sessions"))
    crypto.reset_cipher()
    store = ws.reset_store(tmp_path / "s.db")
    raw, sess = store.create()
    store.save_credential(sess, ws.ProviderCredential(provider="gitlab", access_token="gl-session-token",
                                                      profile={"login": "glu"}))
    client = TestClient(_api_app.app)
    client.headers["X-GitPilot-Session"] = raw
    yield client
    crypto.reset_cipher()
    ws.reset_store(None)


def test_execute_route_runs_in_the_gitlab_scope_with_the_session_token(app_client, gitlab, monkeypatch):
    from gitpilot import _api_app

    seen = {}

    async def fake_executor(plan, full_name, token=None, branch_name=None):
        seen.update(provider=scm.current_provider(), full_name=full_name, token=token)
        await scm.put_file(*scm.split_full_name(full_name), "a.py", "x", "m", token=token, branch=branch_name)
        return {"status": "completed", "branch": branch_name, "executionLog": {"steps": []}}

    monkeypatch.setattr(_api_app, "execute_plan", fake_executor)
    monkeypatch.setattr(_api_app, "execute_plan_lite", fake_executor)
    resp = app_client.post("/api/chat/execute", json={
        "repo_owner": "acme/platform", "repo_name": "app", "provider": "gitlab", "repo_id": str(PID),
        "branch_name": "feat", "plan": PLAN.model_dump(),
    })
    assert resp.status_code == 200, resp.text
    assert seen == {"provider": "gitlab", "full_name": FULL, "token": "gl-session-token"}
    commit = [c for c in gitlab.calls if c.path.endswith("/repository/commits")]
    assert commit and commit[0].bearer == "gl-session-token"


def test_github_routes_never_receive_the_gitlab_session_token(app_client, gitlab):
    resp = app_client.get("/api/repos/acme/app/file", params={"path": "README.md"})
    assert resp.status_code in (400, 401)
    assert gitlab.to(GITHUB_API) == []


def test_gitlab_file_crud_routes_use_the_session_token(app_client, gitlab):
    write = app_client.post(f"/api/gitlab/projects/{PID}/file",
                            json={"path": "docs/x.md", "content": "# x", "message": "doc", "branch": "feat"})
    assert write.status_code == 200 and write.json()["commit_sha"] == "c0ffee"
    delete = app_client.delete(f"/api/gitlab/projects/{PID}/file",
                               params={"path": "old.py", "branch": "feat", "message": "rm"})
    assert delete.status_code == 200
    branch = app_client.post(f"/api/gitlab/projects/{PID}/branches", json={"name": "feat-2"})
    assert branch.status_code == 201 and branch.json()["ref"] == "refs/heads/feat-2"
    mr = app_client.post(f"/api/gitlab/projects/{PID}/merge_requests",
                         json={"title": "T", "source_branch": "feat"})
    assert mr.status_code == 201 and mr.json()["number"] == 5
    same = app_client.post(f"/api/gitlab/projects/{PID}/merge_requests",
                           json={"title": "T", "source_branch": "main"})
    assert same.status_code == 422
    assert {c.bearer for c in gitlab.calls} == {"gl-session-token"}


def test_sessions_carry_provider_and_stable_id(app_client):
    created = app_client.post("/api/sessions", json={
        "repo_full_name": FULL, "branch": "main", "provider": "gitlab", "repo_id": PID,
        "repos": [{"full_name": FULL, "branch": "main", "mode": "write", "repo_id": PID}],
        "active_repo": FULL,
    }).json()
    assert created["session"]["provider"] == "gitlab" and created["session"]["repo_id"] == str(PID)
    assert created["session"]["repos"][0]["provider"] == "gitlab"
    listed = app_client.get("/api/sessions", params={"repo": FULL, "provider": "gitlab"}).json()["sessions"]
    assert [s["id"] for s in listed] == [created["session_id"]]
    assert app_client.get("/api/sessions", params={"repo": FULL, "provider": "github"}).json()["sessions"] == []


def test_installation_status_never_returns_the_server_token(app_client, gitlab, monkeypatch):
    monkeypatch.setenv("GITPILOT_GITHUB_TOKEN", "ghp_server_secret")
    gitlab.on("GET", GITHUB_API, "/user", {"login": "srv", "id": 1, "avatar_url": ""})
    body = app_client.get("/api/auth/installation-status").json()
    assert "ghp_server_secret" not in str(body)
