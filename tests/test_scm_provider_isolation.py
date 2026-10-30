"""Provider isolation: one agent implementation, two providers, and a credential
that only ever reaches its own provider."""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from fastapi import HTTPException

from gitpilot import github_api, gitlab_api, scm
from gitpilot.agent.policy import CapabilityMask
from gitpilot.toolkit import forge
from gitpilot.toolkit.builtins import build_default_registry
from tests._forge_fakes import GITHUB_API, GITLAB, FakeForges, gitlab_project


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def forges(monkeypatch):
    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    for var in ("GITPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GITPILOT_GITLAB_TOKEN", "GITLAB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return FakeForges().install(monkeypatch)


def test_github_helpers_refuse_inside_a_gitlab_scope(forges, monkeypatch):
    # Even the server's own GitHub token must not stand in for a GitLab repo.
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_server")
    with scm.execution_context("gitlab", "gitlab-oauth-token"):
        with pytest.raises(HTTPException) as exc:
            run(github_api.get_repo("acme", "app"))
        with pytest.raises(HTTPException):
            run(github_api.github_request("/user", token="gitlab-oauth-token"))
    assert exc.value.status_code == 400
    assert forges.to(GITHUB_API) == []


def test_gitlab_helpers_refuse_inside_a_github_scope(forges):
    with scm.execution_context("github", "gho_user"):
        with pytest.raises(HTTPException):
            run(gitlab_api.get_repo("acme", "app", token="gho_user"))
    assert forges.to(GITLAB) == []


def test_visible_token_prefixes_never_cross_providers(forges):
    with pytest.raises(HTTPException):
        run(github_api.get_repo("acme", "app", token="glpat-abc"))
    with pytest.raises(HTTPException):
        run(gitlab_api.get_repo("acme", "app", token="ghp_abc"))
    assert forges.calls == []


def test_scm_dispatches_by_bound_provider(forges):
    forges.on("GET", GITLAB, "/api/v4/projects/acme%2Fplatform%2Fapp", gitlab_project(path="acme/platform/app"))
    forges.on("GET", GITHUB_API, "/repos/acme/app", {"id": 1, "full_name": "acme/app", "default_branch": "main"})
    with scm.execution_context("gitlab", "gl-token"):
        run(scm.get_repo("acme/platform", "app"))
    with scm.execution_context("github", "gho_x"):
        run(scm.get_repo("acme", "app"))
    assert [(c.host, c.bearer) for c in forges.calls] == [(GITLAB, "gl-token"), (GITHUB_API, "gho_x")]


def test_explicit_provider_argument_wins_without_a_scope(forges):
    forges.on("GET", GITLAB, "/api/v4/projects/9", gitlab_project(9))
    run(scm.get_repo("", "9", token="gl", provider="gitlab"))
    assert forges.calls[0].host == GITLAB


@pytest.mark.parametrize("provider,name,ok", [
    ("github", "acme/app", True),
    ("github", "acme/team/app", False),
    ("gitlab", "acme/team/sub/app", True),
    ("gitlab", "app", False),
    ("gitlab", "acme/../app", False),
])
def test_repository_name_rules(provider, name, ok):
    if ok:
        owner, repo = scm.split_full_name(name, provider)
        assert f"{owner}/{repo}" == name
    else:
        with pytest.raises(ValueError):
            scm.split_full_name(name, provider)


def test_agentic_accepts_nested_gitlab_names_only_for_gitlab():
    from gitpilot.agentic import _split_repo_full_name

    with scm.execution_context("gitlab", "t"):
        assert _split_repo_full_name("acme/platform/app") == ("acme/platform", "app")
    with scm.execution_context("github", "t"), pytest.raises(ValueError):
        _split_repo_full_name("acme/platform/app")


def test_urls_and_workspace_dirs_are_provider_specific(monkeypatch, tmp_path):
    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert scm.branch_url("acme/app", "feat", "github") == "https://github.com/acme/app/tree/feat"
    assert scm.branch_url("a/b/app", "feat", "gitlab") == f"https://{GITLAB}/a/b/app/-/tree/feat"
    assert scm.workspace_dir("acme/app", "github") != scm.workspace_dir("acme/app", "gitlab")
    assert "_gitlab" in scm.workspace_dir("a/b/app", "gitlab").parts


def test_crewai_tools_route_with_their_stored_provider(monkeypatch):
    """The provider is stored with the token, so a tool thread cannot pair them wrongly."""
    from gitpilot import agent_tools

    calls = []

    async def fake_mr(owner, repo, **kw):
        calls.append(("gitlab", owner, repo, kw["token"]))
        return {"number": 1, "html_url": "u"}

    async def fake_pr(owner, repo, **kw):  # pragma: no cover - must not be reached
        calls.append(("github", owner, repo, kw["token"]))
        return {}

    monkeypatch.setattr(gitlab_api, "create_pull_request", fake_mr)
    monkeypatch.setattr("gitpilot.github_pulls.create_pull_request", fake_pr)
    agent_tools.set_repo_context("acme/platform", "app", token="gl-token", branch="feat", provider="gitlab")
    run(agent_tools.bound_scm.create_pull_request("acme/platform", "app", title="t", head="feat",
                                                  base="main", token="gl-token"))
    assert calls == [("gitlab", "acme/platform", "app", "gl-token")]
    assert agent_tools.get_repo_provider() == "gitlab"


def test_forge_registry_offers_only_the_bound_providers_tools():
    gh = build_default_registry(include_forge=True, forge_provider="github")
    gl = build_default_registry(include_forge=True, forge_provider="gitlab")
    gh_ids, gl_ids = set(gh.ids()), set(gl.ids())
    assert "github.pr.create" in gh_ids and not any(i.startswith("gitlab.") for i in gh_ids)
    assert {"gitlab.mr.create", "gitlab.issue.create", "gitlab.mr.list"} <= gl_ids
    assert not any(i.startswith("github.") for i in gl_ids)
    assert forge.gitlab_available()


def test_topology_grants_cover_gitlab_twins():
    mask = CapabilityMask.from_mapping({"github.pr.create": "ask", "github.issue.list": True})
    assert mask.qualifier("gitlab.mr.create").force_ask
    assert mask.allows("gitlab.issue.list")
    assert not mask.allows("gitlab.issue.create")
    explicit = CapabilityMask.from_mapping({"github.pr.create": True, "gitlab.mr.create": False})
    assert explicit.allows("github.pr.create") and not explicit.allows("gitlab.mr.create")


def test_clone_credentials_stay_out_of_git_config():
    from gitpilot.workspace import _auth_env, _remote_url

    url = _remote_url("acme", "app", "github")
    env = _auth_env(url, "gho_secret", "github")
    assert "gho_secret" not in url
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert base64.b64encode(b"x-access-token:gho_secret").decode() in env["GIT_CONFIG_VALUE_0"]


def test_gitlab_clone_url_targets_configured_host(monkeypatch):
    from gitpilot.workspace import _auth_env, _remote_url

    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    url = _remote_url("a/b", "app", "gitlab")
    assert url == f"https://{GITLAB}/a/b/app.git"
    env = _auth_env(url, "gl-token", "gitlab")
    assert env["GIT_CONFIG_KEY_0"] == f"http.https://{GITLAB}/.extraHeader"
    assert base64.b64encode(b"oauth2:gl-token").decode() in env["GIT_CONFIG_VALUE_0"]


def test_github_api_unchanged_outside_any_scope(forges, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_server")
    forges.on("GET", GITHUB_API, "/repos/acme/app", {"id": 1})
    run(github_api.get_repo("acme", "app"))
    assert forges.calls[0].bearer == "ghp_server"


def test_token_header_resolution_respects_declared_provider():
    from gitpilot.auth import web_session as ws

    with ws.bind(None, "gitlab"):
        assert ws.resolve_provider_token("github", "Bearer something") is None
        assert ws.resolve_provider_token("gitlab", "Bearer something") == "something"
    with ws.bind(None, None):
        assert ws.resolve_provider_token("github", "Bearer glpat-x") is None
        assert ws.resolve_provider_token("gitlab", "token ghp_x") is None
        assert ws.resolve_provider_token("github", "ghp_x") == "ghp_x"


def test_httpx_mock_is_the_only_transport(forges):
    """Guard for the suite itself: unmatched requests 404 instead of hitting the network."""
    async def go():
        async with httpx.AsyncClient() as client:
            return await client.get("https://example.invalid/x")
    assert run(go()).status_code == 404
