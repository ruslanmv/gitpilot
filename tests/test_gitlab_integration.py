"""Unit tests for the additive GitLab OAuth and repository adapter."""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

from gitpilot import gitlab


def run(coro):
    """Execute an async endpoint."""
    return asyncio.run(coro)


def test_oauth_needs_credentials(monkeypatch):
    """Unconfigured GitLab OAuth must be unavailable."""
    monkeypatch.delenv("GITLAB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITLAB_CLIENT_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        run(gitlab.gitlab_auth_url())
    assert exc.value.status_code == 503


def test_oauth_url_has_state_and_pkce(monkeypatch):
    """The OAuth redirect contains secure CSRF state and S256 PKCE."""
    monkeypatch.setenv("GITLAB_CLIENT_ID", "app-id")
    monkeypatch.setenv("GITLAB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITLAB_REDIRECT_URI", "https://example.org/auth")
    response = run(gitlab.gitlab_auth_url())
    params = parse_qs(urlparse(response["authorization_url"]).query)
    assert params["state"] == [response["state"]]
    assert params["client_id"] == ["app-id"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["https://example.org/auth"]
    gitlab._pending.pop(response["state"], None)


def test_nested_group_project_mapping(monkeypatch):
    """Nested groups use numeric project IDs rather than ambiguous paths."""
    async def fake_request(method, path, token, **kwargs):
        assert path == "/projects"
        assert token == "gitlab-token"
        assert kwargs["params"]["search"] == "pilot"
        return httpx.Response(
            200,
            json=[{"id": 113, "name": "Repo",
                   "path_with_namespace": "enterprise/team/repo",
                   "default_branch": "develop", "visibility": "private"}],
            headers={"x-total": "1"},
        )
    monkeypatch.setattr(gitlab, "_request", fake_request)
    response = run(gitlab.gitlab_projects(query="pilot", page=1, per_page=50,
                                          authorization="Bearer gitlab-token"))
    item = response["repositories"][0]
    assert item["full_name"] == "enterprise/team/repo"
    assert item["owner"] == "enterprise/team"
    assert item["id"] == 113
    assert item["provider"] == "gitlab"


def test_missing_token_returns_link_state():
    """An email-only user sees a connect prompt instead of an error."""
    response = run(gitlab.gitlab_projects(query=None, page=1, per_page=50, authorization=None))
    assert response["repositories"] == []
    assert response["gitlab_connected"] is False


def test_tree_uses_existing_ui_response_shape(monkeypatch):
    """Tree entries map directories and files correctly."""
    async def fake_request(method, path, token, **kwargs):
        assert path == "/projects/113/repository/tree"
        return httpx.Response(200, json=[
            {"path": "src", "type": "tree"},
            {"path": "src/app.py", "type": "blob"},
        ])
    monkeypatch.setattr(gitlab, "_request", fake_request)
    response = run(gitlab.gitlab_tree(113, ref="develop", authorization="Bearer token"))
    assert response["files"][1] == {"path": "src/app.py", "type": "blob"}


def test_file_write_rejects_traversal():
    """An absolute or traversal path is rejected before an API call."""
    payload = gitlab.FileUpdate(path="../secret", content="x", message="test", branch="main")
    with pytest.raises(HTTPException) as exc:
        run(gitlab.gitlab_write_file(10, payload, authorization="Bearer token"))
    assert exc.value.status_code == 400
