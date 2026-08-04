"""Configuring which MCP Context Forge GitPilot talks to.

One authority behind three surfaces (desktop UI, mobile UI, VS Code), so the
rules live here rather than in each front end:

* A saved secret is never handed back — reads report *whether* one exists.
* A blank field means "keep what you have"; clearing is an explicit act, so a
  settings form that renders an empty password box can never erase one.
* The credential is proved against the gateway, not trusted because it is set.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.mcp_admin_api import router
from gitpilot.mcp_gateway_config import (
    GatewayProfile,
    GatewayUpdate,
    apply_update,
    load_profile,
    save_profile,
    probe_gateway,
)


@pytest.fixture()
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp_gateway.json"
    monkeypatch.setenv("GITPILOT_MCP_GATEWAY_FILE", str(path))
    for name in ("GITPILOT_MCP_FORGE_URL", "GITPILOT_MCP_FORGE_TOKEN",
                 "GITPILOT_MCP_FORGE_EMAIL", "GITPILOT_MCP_FORGE_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    return path


@pytest.fixture()
def api(config_file):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Storage ─────────────────────────────────────────────────────────────────

def test_a_saved_profile_round_trips(config_file):
    save_profile(GatewayProfile(url="http://forge.test:4444", email="a@b.c", password="pw"))
    loaded = load_profile()
    assert loaded.resolved_url() == "http://forge.test:4444"
    assert loaded.resolved_password() == "pw"


def test_the_config_file_is_owner_only(config_file):
    """It holds live credentials, so it must not be world-readable."""
    save_profile(GatewayProfile(url="http://forge.test", api_token="tok"))
    assert oct(config_file.stat().st_mode)[-3:] == "600"


def test_a_corrupt_file_falls_back_instead_of_breaking_settings(config_file):
    config_file.write_text("{not json", encoding="utf-8")
    assert load_profile().resolved_url() == "http://localhost:4444"


def test_settings_override_the_environment(config_file, monkeypatch):
    monkeypatch.setenv("GITPILOT_MCP_FORGE_URL", "http://from-env:4444")
    assert load_profile().resolved_url() == "http://from-env:4444"
    assert load_profile().source_of_url() == "environment"

    save_profile(GatewayProfile(url="http://from-settings:4444"))
    assert load_profile().resolved_url() == "http://from-settings:4444"
    assert load_profile().source_of_url() == "settings"


# ── The empty-vs-clear rule ─────────────────────────────────────────────────

def test_a_blank_password_keeps_the_saved_one():
    """A settings form always posts an empty password box. That is not a delete."""
    saved = GatewayProfile(url="http://f", password="original", api_token="tok")
    updated = apply_update(saved, GatewayUpdate(url="http://f2", password="", api_token=""))
    assert updated.password == "original"
    assert updated.api_token == "tok"
    assert updated.url == "http://f2"


def test_clearing_a_credential_is_explicit_and_targeted():
    saved = GatewayProfile(password="original", api_token="tok")

    # Clearing one credential must leave the other alone.
    only_password = apply_update(saved, GatewayUpdate(clear_password=True))
    assert only_password.password == "" and only_password.api_token == "tok"

    only_token = apply_update(saved, GatewayUpdate(clear_api_token=True))
    assert only_token.api_token == "" and only_token.password == "original"

    both = apply_update(saved, GatewayUpdate(clear_password=True, clear_api_token=True))
    assert both.password == "" and both.api_token == ""


def test_a_new_secret_replaces_the_old_one():
    saved = GatewayProfile(password="original")
    assert apply_update(saved, GatewayUpdate(password="fresh")).password == "fresh"


def test_an_unknown_auth_mode_is_refused():
    with pytest.raises(ValueError) as exc:
        apply_update(GatewayProfile(), GatewayUpdate(auth_mode="magic"))
    assert "auto" in str(exc.value)  # the message lists the valid modes


def test_a_trailing_slash_is_normalised():
    assert apply_update(GatewayProfile(), GatewayUpdate(url="http://f:4444/ ")).url == "http://f:4444"


# ── The API never returns a secret ──────────────────────────────────────────

def test_reading_the_config_reports_secrets_as_booleans(api, config_file):
    api.put("/api/mcp/gateway", json={
        "url": "http://forge.test:4444", "email": "admin@example.com",
        "password": "s3cret", "apiToken": "tok-123",
    })
    body = api.get("/api/mcp/gateway").json()

    assert body["url"] == "http://forge.test:4444"
    assert body["hasPassword"] is True and body["hasApiToken"] is True
    # The values themselves must appear nowhere in the response.
    serialized = json.dumps(body)
    assert "s3cret" not in serialized
    assert "tok-123" not in serialized


def test_saving_without_a_password_does_not_erase_it(api):
    api.put("/api/mcp/gateway", json={"url": "http://f:4444", "password": "s3cret"})
    api.put("/api/mcp/gateway", json={"url": "http://f2:4444"})  # form with a blank box
    body = api.get("/api/mcp/gateway").json()
    assert body["url"] == "http://f2:4444"
    assert body["hasPassword"] is True


def test_a_credential_can_be_removed_deliberately(api):
    api.put("/api/mcp/gateway", json={"password": "s3cret", "apiToken": "tok"})
    body = api.put("/api/mcp/gateway", json={"clearPassword": True, "clearApiToken": True}).json()
    assert body["hasPassword"] is False and body["hasApiToken"] is False


def test_an_invalid_auth_mode_is_a_400_not_a_crash(api):
    assert api.put("/api/mcp/gateway", json={"authMode": "magic"}).status_code == 400


# ── Testing a connection reports what the gateway said ──────────────────────

def _gateway(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _run_test(profile, handler, monkeypatch):
    """Probe a scripted gateway. Synchronous so no async plugin is required."""
    transport = _gateway(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return asyncio.run(probe_gateway(profile))


def test_an_open_gateway_needs_no_credential(config_file, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=[{"id": "a"}, {"id": "b"}])

    result = _run_test(GatewayProfile(url="http://f:4444"), handler, monkeypatch)
    assert result.ok and result.reachable
    assert result.auth_required is False
    assert result.auth_mode_used == "none"
    assert result.server_count == 2


def test_an_unreachable_gateway_says_so_rather_than_failing_silently(config_file, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = _run_test(GatewayProfile(url="http://f:4444"), handler, monkeypatch)
    assert not result.ok and not result.reachable
    assert "Could not reach" in result.detail


def test_a_password_is_exchanged_for_a_token_and_verified(config_file, monkeypatch):
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/auth/email/login":
            return httpx.Response(200, json={"access_token": "jwt-1"})
        seen.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") == "Bearer jwt-1":
            return httpx.Response(200, json={"items": [{"id": "a"}]})
        return httpx.Response(401, json={"detail": "Invalid authentication credentials"})

    profile = GatewayProfile(url="http://f:4444", auth_mode="login",
                             email="admin@example.com", password="s3cret")
    result = _run_test(profile, handler, monkeypatch)
    assert result.ok and result.authenticated
    assert result.auth_mode_used == "login"
    assert result.server_count == 1


def test_a_wrong_password_is_reported_with_the_reason(config_file, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/auth/email/login":
            return httpx.Response(401, json={"detail": "Invalid email or password"})
        return httpx.Response(401, json={"detail": "Invalid authentication credentials"})

    profile = GatewayProfile(url="http://f:4444", auth_mode="login",
                             email="admin@example.com", password="wrong")
    result = _run_test(profile, handler, monkeypatch)
    assert not result.ok
    assert "rejected" in result.detail and "password" in result.detail


def test_a_rejected_token_names_the_classic_mistake(config_file, monkeypatch):
    """Handing Forge its own signing secret is the error people actually make."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(401, json={"detail": "Invalid authentication credentials"})

    profile = GatewayProfile(url="http://f:4444", auth_mode="token", api_token="the-signing-secret")
    result = _run_test(profile, handler, monkeypatch)
    assert not result.ok
    assert "signing secret is not a bearer token" in result.detail.lower()


def test_a_protected_gateway_with_nothing_configured_says_what_to_add(config_file, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(401, json={"detail": "Invalid authentication credentials"})

    result = _run_test(GatewayProfile(url="http://f:4444"), handler, monkeypatch)
    assert not result.ok and result.reachable
    assert "API token" in result.detail or "email" in result.detail


def test_the_test_endpoint_accepts_an_unsaved_draft(api, config_file):
    """"Test connection" must work before Save, or nobody will use it."""
    response = api.post("/api/mcp/gateway/test", json={"url": "http://127.0.0.1:1"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["url"] == "http://127.0.0.1:1"
    # Testing a draft must not persist it.
    assert api.get("/api/mcp/gateway").json()["savedUrl"] == ""
