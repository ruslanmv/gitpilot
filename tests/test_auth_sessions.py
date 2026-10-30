"""Server-side sessions, OAuth (state + PKCE) for GitHub and GitLab, CSRF,
token refresh, logout and account sessions — with every provider mocked."""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.auth import crypto
from gitpilot.auth import web_session as ws
from gitpilot.auth.middleware import WebSessionMiddleware
from gitpilot.auth.session_router import build_session_router
from gitpilot.gitlab import build_gitlab_router
from tests._forge_fakes import GITHUB_API, GITHUB_WEB, GITLAB, FakeForges, gitlab_project


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITPILOT_TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh-secret")
    monkeypatch.setenv("GITHUB_REDIRECT_URI", "https://app.example/auth")
    monkeypatch.setenv("GITLAB_CLIENT_ID", "gl-client")
    monkeypatch.setenv("GITLAB_CLIENT_SECRET", "gl-secret")
    monkeypatch.setenv("GITLAB_REDIRECT_URI", "https://app.example/auth")
    monkeypatch.setenv("GITLAB_BASE_URL", f"https://{GITLAB}")
    for var in ("GITPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GITPILOT_GITLAB_TOKEN", "GITLAB_TOKEN",
                "CORS_ORIGINS", "GITPILOT_COOKIE_SAMESITE", "GITPILOT_PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    crypto.reset_cipher()
    ws.reset_store(tmp_path / "sessions.db")
    yield tmp_path
    crypto.reset_cipher()
    ws.reset_store(None)


@pytest.fixture
def forges(env, monkeypatch):
    fake = FakeForges().install(monkeypatch)
    fake.on("POST", GITHUB_WEB, "/login/oauth/access_token", lambda req, m: httpx.Response(
        200, json={"access_token": "gho_user_token", "scope": "repo", "token_type": "bearer"}))
    fake.on("GET", GITHUB_API, "/user", {"id": 1, "login": "octo", "avatar_url": "a", "html_url": "h"})
    fake.on("POST", GITLAB, "/oauth/token", lambda req, m: httpx.Response(200, json={
        "access_token": "gl-access-1", "refresh_token": "gl-refresh-1", "expires_in": 7200, "scope": "api"}))
    fake.on("GET", GITLAB, "/api/v4/user", {"id": 9, "username": "glu", "name": "GL User", "web_url": "w"})
    fake.on("POST", GITLAB, "/oauth/revoke", {})
    fake.on("DELETE", GITHUB_API, "/applications/gh-client/token", httpx.Response(204))
    return fake


@pytest.fixture
def client(forges):
    app = FastAPI()
    app.include_router(build_session_router())
    app.include_router(build_gitlab_router())
    app.add_middleware(WebSessionMiddleware)
    return TestClient(app)


def _connect(client, provider="github"):
    start = client.get(f"/api/auth/oauth/{provider}/start").json()
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    resp = client.post(f"/api/auth/oauth/{provider}/callback", json={"code": "the-code", "state": state},
                       headers=_csrf(client))
    assert resp.status_code == 200, resp.text
    return resp


def _csrf(client):
    info = client.get("/api/auth/session").json()
    return {"X-GitPilot-CSRF": info["csrf_token"]} if info.get("csrf_token") else {}


# --- OAuth start -----------------------------------------------------------------

@pytest.mark.parametrize("provider,host", [("github", "github.com"), ("gitlab", GITLAB)])
def test_authorize_url_has_state_and_s256_pkce(client, provider, host):
    data = client.get(f"/api/auth/oauth/{provider}/start").json()
    url = urlparse(data["authorization_url"])
    q = parse_qs(url.query)
    assert url.hostname == host
    assert q["state"] == [data["state"]] and len(data["state"]) >= 40
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) >= 43
    assert q["redirect_uri"] == ["https://app.example/auth"]


def test_unconfigured_provider_is_503(client, monkeypatch):
    monkeypatch.delenv("GITLAB_CLIENT_SECRET")
    assert client.get("/api/auth/oauth/gitlab/start").status_code == 503
    assert client.get("/api/auth/providers").json()["gitlab"]["web"] is False


# --- OAuth callback --------------------------------------------------------------

@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_callback_keeps_tokens_server_side(client, forges, provider, env):
    resp = _connect(client, provider)
    body = resp.json()
    assert "access_token" not in body and "refresh_token" not in body
    assert body["providers"][provider]["connected"] is True
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "gitpilot_session=gps_" in cookie

    token_call = [c for c in forges.calls if c.path.endswith(("/oauth/token", "/access_token"))][0]
    verifier = parse_qs(token_call.body if isinstance(token_call.body, str) else "").get("code_verifier", [""])[0]
    assert verifier, "PKCE verifier must be sent to the token endpoint"

    raw_db = (env / "sessions.db").read_bytes()
    for secret in (b"gho_user_token", b"gl-access-1", b"gl-refresh-1"):
        assert secret not in raw_db  # encrypted at rest


def test_pkce_verifier_matches_challenge(client, forges):
    start = client.get("/api/auth/oauth/gitlab/start").json()
    q = parse_qs(urlparse(start["authorization_url"]).query)
    client.post("/api/auth/oauth/gitlab/callback", json={"code": "c", "state": start["state"]})
    form = parse_qs([c for c in forges.calls if c.path == "/oauth/token"][0].body)
    digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=")
    assert digest.decode() == q["code_challenge"][0]


def test_state_is_single_use_and_provider_bound(client):
    start = client.get("/api/auth/oauth/github/start").json()
    assert client.post("/api/auth/oauth/gitlab/callback",
                       json={"code": "c", "state": start["state"]}).status_code == 400
    # The wrong-provider attempt consumed it as well: a state is never reusable.
    assert client.post("/api/auth/oauth/github/callback",
                       json={"code": "c", "state": start["state"]}).status_code == 400


def test_unknown_and_expired_state_rejected(client, monkeypatch):
    assert client.post("/api/auth/oauth/github/callback",
                       json={"code": "c", "state": "x" * 43}).status_code == 400
    start = client.get("/api/auth/oauth/github/start").json()
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + ws.OAUTH_STATE_TTL + 5)
    resp = client.post("/api/auth/oauth/github/callback", json={"code": "c", "state": start["state"]})
    assert resp.status_code == 400 and "expired" in resp.json()["detail"]


def test_state_started_in_another_session_is_refused(client, forges):
    _connect(client, "github")
    start = client.get("/api/auth/oauth/gitlab/start").json()  # bound to this session
    other = TestClient(client.app)
    resp = other.post("/api/auth/oauth/gitlab/callback", json={"code": "c", "state": start["state"]})
    assert resp.status_code == 400 and "another browser session" in resp.json()["detail"]


def test_provider_error_is_a_clean_400(client, forges):
    forges.routes.insert(0, ("POST", GITHUB_WEB, __import__("re").compile("^/login/oauth/access_token$"),
                             lambda r, m: httpx.Response(200, json={"error": "bad_verification_code"})))
    start = client.get("/api/auth/oauth/github/start").json()
    resp = client.post("/api/auth/oauth/github/callback", json={"code": "c", "state": start["state"]})
    assert resp.status_code == 400


# --- using the session ------------------------------------------------------------

def test_gitlab_routes_use_the_session_credential(client, forges):
    _connect(client, "gitlab")
    forges.on("GET", GITLAB, "/api/v4/projects", [gitlab_project()])
    data = client.get("/api/gitlab/projects").json()
    assert data["gitlab_connected"] is True and data["repositories"][0]["id"] == 113
    assert forges.to(GITLAB)[-1].bearer == "gl-access-1"


def test_both_providers_can_be_connected_at_once(client):
    _connect(client, "github")
    info = _connect(client, "gitlab").json()
    assert set(info["providers"]) == {"github", "gitlab"}


def test_session_rotates_on_each_connection(client):
    first = _connect(client, "github").json()["session_token"]
    second = _connect(client, "gitlab").json()["session_token"]
    assert first != second
    assert ws.get_store().resolve(first) is None and ws.get_store().resolve(second) is not None


def test_header_transport_works_without_cookies_and_without_csrf(client):
    token = _connect(client, "github").json()["session_token"]
    bare = TestClient(client.app)  # no cookie jar contents
    info = bare.get("/api/auth/session", headers={"X-GitPilot-Session": token}).json()
    assert info["providers"]["github"]["user"]["login"] == "octo"
    resp = bare.post("/api/auth/providers/github/disconnect", headers={"X-GitPilot-Session": token})
    assert resp.status_code == 200


def test_csrf_required_for_cookie_authenticated_writes(client):
    _connect(client, "github")
    assert client.post("/api/auth/providers/github/disconnect").status_code == 403
    assert client.post("/api/auth/providers/github/disconnect",
                       headers={"X-GitPilot-CSRF": "wrong"}).status_code == 403
    resp = client.post("/api/auth/providers/github/disconnect", headers=_csrf(client))
    assert resp.status_code == 200 and resp.json()["providers"] == {} and resp.json()["revoked"] is True


def test_logout_revokes_server_side(client, forges):
    token = _connect(client, "gitlab").json()["session_token"]
    assert client.post("/api/auth/logout", headers=_csrf(client)).status_code == 200
    assert ws.get_store().resolve(token) is None
    replay = TestClient(client.app).get("/api/auth/session", headers={"X-GitPilot-Session": token}).json()
    assert replay["authenticated"] is False
    assert any(c.path == "/oauth/revoke" for c in forges.calls)


def test_expiring_gitlab_token_is_refreshed(client, forges):
    _connect(client, "gitlab")
    store = ws.get_store()
    sess = store.resolve(client.cookies.get("gitpilot_session"))
    cred = sess.credentials["gitlab"]
    cred.expires_at = time.time() - 1
    store.save_credential(sess, cred)
    forges.routes.insert(0, ("POST", GITLAB, __import__("re").compile("^/oauth/token$"), lambda r, m: httpx.Response(
        200, json={"access_token": "gl-access-2", "refresh_token": "gl-refresh-2", "expires_in": 7200})))
    forges.on("GET", GITLAB, "/api/v4/projects", [])
    client.get("/api/gitlab/projects")
    refresh_form = parse_qs([c for c in forges.calls if c.path == "/oauth/token"][-1].body)
    assert refresh_form["grant_type"] == ["refresh_token"] and refresh_form["refresh_token"] == ["gl-refresh-1"]
    assert forges.to(GITLAB)[-1].bearer == "gl-access-2"


def test_unrefreshable_expired_token_is_dropped(client, forges):
    _connect(client, "gitlab")
    store = ws.get_store()
    sess = store.resolve(client.cookies.get("gitpilot_session"))
    cred = sess.credentials["gitlab"]
    cred.expires_at = time.time() - 1
    store.save_credential(sess, cred)
    forges.routes.insert(0, ("POST", GITLAB, __import__("re").compile("^/oauth/token$"),
                             lambda r, m: httpx.Response(400, json={"error": "invalid_grant"})))
    info = client.get("/api/auth/session").json()
    assert "gitlab" not in info["providers"]


def test_legacy_browser_token_import(client, forges):
    resp = client.post("/api/auth/session/import", json={"provider": "github", "access_token": "gho_legacy_token"})
    assert resp.status_code == 200 and "access_token" not in resp.json()
    assert resp.json()["providers"]["github"]["user"]["login"] == "octo"
    forges.routes.insert(0, ("GET", GITHUB_API, __import__("re").compile("^/user$"),
                             lambda r, m: httpx.Response(401, json={"message": "Bad credentials"})))
    bad = TestClient(client.app).post("/api/auth/session/import",
                                      json={"provider": "github", "access_token": "gho_revoked_x"})
    assert bad.status_code == 401


def test_ws_ticket_is_single_use(client):
    _connect(client, "github")
    ticket = client.post("/api/auth/ws-ticket", headers=_csrf(client)).json()["ticket"]
    store = ws.get_store()
    assert store.redeem_ws_ticket(ticket) is not None
    assert store.redeem_ws_ticket(ticket) is None


def test_samesite_none_needs_an_origin_allowlist(monkeypatch):
    monkeypatch.setenv("GITPILOT_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    assert ws.cookie_samesite() == "none"
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert ws.cookie_samesite() == "lax"


def test_rotated_encryption_key_still_decrypts(env, monkeypatch):
    old = base64.urlsafe_b64encode(b"k" * 32).decode()
    sealed = crypto.seal("secret")
    monkeypatch.setenv("GITPILOT_TOKEN_ENCRYPTION_KEY",
                       base64.urlsafe_b64encode(b"n" * 32).decode() + "," + old)
    crypto.reset_cipher()
    assert crypto.unseal(sealed) == "secret"


def test_database_never_stores_raw_session_ids(client, env):
    token = _connect(client, "github").json()["session_token"]
    con = sqlite3.connect(env / "sessions.db")
    ids = [r[0] for r in con.execute("SELECT id_hash FROM gitpilot_web_sessions")]
    con.close()
    assert token not in ids and hashlib.sha256(token.encode()).hexdigest() in ids


# --- accounts on server-side sessions ----------------------------------------------

@pytest.fixture
def accounts(env, monkeypatch):
    from gitpilot.auth import build_account_router
    from gitpilot.auth.accounts import get_account_store, reset_account_store
    from gitpilot.auth.security import hash_password

    monkeypatch.setenv("GITPILOT_ENABLE_ACCOUNTS", "true")
    monkeypatch.setenv("GITPILOT_AUTH_SECRET", "test-secret-value")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_account_store()
    store = get_account_store()
    store.create("ada@example.com", password_hash=hash_password("correct horse"), name="Ada", verified=True)
    app = FastAPI()
    app.include_router(build_account_router())
    app.include_router(build_session_router())
    app.add_middleware(WebSessionMiddleware)
    return TestClient(app)


def test_account_login_is_a_revocable_server_session(accounts):
    login = accounts.post("/api/account/login", json={"email": "ada@example.com", "password": "correct horse"})
    assert login.status_code == 200
    token = login.json()["session_token"]
    assert token.startswith("gps_")
    assert accounts.get("/api/account/me").json()["email"] == "ada@example.com"
    info = accounts.get("/api/auth/session").json()
    assert info["account"]["email"] == "ada@example.com" and info["authenticated"]
    assert accounts.post("/api/account/logout", headers=_csrf(accounts)).status_code == 200
    replay = TestClient(accounts.app).get("/api/account/me", headers={"X-GitPilot-Session": token})
    assert replay.status_code == 401


def test_password_reset_link_works_once(accounts):
    from gitpilot.auth.accounts import get_account_store
    from gitpilot.auth.security import RESET_PASSWORD, make_link_token, password_fingerprint

    acc = get_account_store().get_by_email("ada@example.com")
    link = make_link_token("ada@example.com", RESET_PASSWORD, fingerprint=password_fingerprint(acc.password_hash))
    first = accounts.post("/api/account/password/reset", json={"token": link, "password": "new password 1"})
    assert first.status_code == 200
    again = accounts.post("/api/account/password/reset", json={"token": link, "password": "new password 2"})
    assert again.status_code == 401


def test_reset_signs_out_every_other_session(accounts):
    from gitpilot.auth.accounts import get_account_store
    from gitpilot.auth.security import RESET_PASSWORD, make_link_token, password_fingerprint

    other = TestClient(accounts.app)
    old = other.post("/api/account/login", json={"email": "ada@example.com", "password": "correct horse"})
    old_token = old.json()["session_token"]
    acc = get_account_store().get_by_email("ada@example.com")
    link = make_link_token("ada@example.com", RESET_PASSWORD, fingerprint=password_fingerprint(acc.password_hash))
    TestClient(accounts.app).post("/api/account/password/reset", json={"token": link, "password": "brand new pw"})
    assert ws.get_store().resolve(old_token) is None


def test_legacy_signed_account_session_is_migrated(accounts):
    from gitpilot.auth.accounts import get_account_store
    from gitpilot.auth.security import make_session

    acc = get_account_store().get_by_email("ada@example.com")
    legacy = make_session(acc.id, acc.email)
    fresh = TestClient(accounts.app)
    info = fresh.get("/api/auth/session", headers={"X-GitPilot-Session": legacy}).json()
    assert info["authenticated"] and info["session_token"].startswith("gps_")


def test_cross_site_websocket_cannot_use_the_session_cookie(monkeypatch):
    from gitpilot.auth.middleware import _origin_allowed

    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    monkeypatch.delenv("CORS_ORIGIN_REGEX", raising=False)
    assert _origin_allowed({"origin": "https://app.example", "host": "api.example"})
    assert _origin_allowed({"origin": "http://api.example", "host": "api.example"})  # same origin
    assert _origin_allowed({"host": "api.example"})  # native client, no Origin
    assert not _origin_allowed({"origin": "https://evil.example", "host": "api.example"})
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://gitpilot-[a-z0-9-]+\.vercel\.app")
    assert _origin_allowed({"origin": "https://gitpilot-pr-1.vercel.app", "host": "api.example"})
    assert not _origin_allowed({"origin": "https://gitpilot-pr-1.vercel.app.evil.example", "host": "api.example"})


def test_cookie_writes_from_a_trusted_origin_need_no_token(client, monkeypatch):
    """Existing same-origin UI calls keep working; cross-site forgeries do not."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    _connect(client, "github")
    assert client.post("/api/auth/ws-ticket", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/auth/ws-ticket", headers={"Origin": "https://app.example"}).status_code == 200
    assert client.post("/api/auth/ws-ticket",
                       headers={"Referer": "https://app.example/workspace"}).status_code == 200
    assert client.post("/api/auth/ws-ticket", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/ws-ticket", headers={"Origin": "null"}).status_code == 403
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert client.post("/api/auth/ws-ticket", headers={"Origin": "https://evil.example"}).status_code == 403


# --- deployments: Hugging Face Space + production frontend ------------------------

def test_space_host_supplies_public_url_redirect_and_secure_cookies(env, monkeypatch):
    from gitpilot.auth import deployment, oauth

    monkeypatch.delenv("GITHUB_REDIRECT_URI")
    monkeypatch.delenv("GITLAB_REDIRECT_URI")
    monkeypatch.setenv("SPACE_ID", "ruslanmv/gitpilot")
    monkeypatch.setenv("SPACE_HOST", "ruslanmv-gitpilot.hf.space")
    assert deployment.public_base_url() == "https://ruslanmv-gitpilot.hf.space"
    assert oauth.provider_config("gitlab").redirect_uri == "https://ruslanmv-gitpilot.hf.space/auth"
    assert ws.cookie_secure("http")  # TLS terminates at the Spaces proxy
    monkeypatch.setenv("GITPILOT_PUBLIC_BASE_URL", "https://gitpilot.ruslanmv.com")
    assert oauth.provider_config("github").redirect_uri == "https://gitpilot.ruslanmv.com/auth"
    assert deployment.trusted_public_origins() == ["https://gitpilot.ruslanmv.com",
                                                   "https://ruslanmv-gitpilot.hf.space"]


@pytest.mark.parametrize("origin,expected", [
    ("https://gitpilot.ruslanmv.com", "https://gitpilot.ruslanmv.com/auth"),     # production (Vercel)
    ("https://ruslanmv-gitpilot.hf.space", "https://ruslanmv-gitpilot.hf.space/auth"),  # Space UI
    ("https://evil.example", "https://gitpilot.ruslanmv.com/auth"),             # not allowed: default
])
def test_oauth_returns_to_the_frontend_that_started_it(client, forges, monkeypatch, origin, expected):
    monkeypatch.delenv("GITHUB_REDIRECT_URI")
    monkeypatch.setenv("GITPILOT_PUBLIC_BASE_URL", "https://gitpilot.ruslanmv.com")
    monkeypatch.setenv("SPACE_HOST", "ruslanmv-gitpilot.hf.space")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    start = client.get("/api/auth/oauth/github/start", params={"origin": origin}).json()
    q = parse_qs(urlparse(start["authorization_url"]).query)
    assert q["redirect_uri"] == [expected]
    client.post("/api/auth/oauth/github/callback", json={"code": "c", "state": start["state"]})
    exchange = parse_qs([c for c in forges.calls if c.path == "/login/oauth/access_token"][-1].body)
    assert exchange["redirect_uri"] == [expected]  # the same URI as the authorization request


def test_same_origin_frontend_is_always_a_valid_redirect(client, monkeypatch):
    monkeypatch.delenv("GITHUB_REDIRECT_URI")
    monkeypatch.setenv("GITPILOT_PUBLIC_BASE_URL", "https://gitpilot.ruslanmv.com")
    start = client.get("/api/auth/oauth/github/start", params={"origin": "http://testserver"}).json()
    assert parse_qs(urlparse(start["authorization_url"]).query)["redirect_uri"] == ["http://testserver/auth"]


def test_extra_frontends_by_allowlist_or_pattern(monkeypatch):
    from gitpilot.auth.deployment import allowed_frontend_origin

    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("GITPILOT_OAUTH_REDIRECT_ORIGINS", "https://staging.example.com")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://gitpilot-[a-z0-9-]+\.vercel\.app")
    assert allowed_frontend_origin("https://staging.example.com/") == "https://staging.example.com"
    assert allowed_frontend_origin("https://gitpilot-pr-7.vercel.app") == "https://gitpilot-pr-7.vercel.app"
    assert allowed_frontend_origin("https://user@evil.example") is None
    assert allowed_frontend_origin("javascript:alert(1)") is None
    assert allowed_frontend_origin("*") is None


def test_space_origin_is_trusted_when_the_proxy_rewrites_host(monkeypatch):
    from gitpilot.auth.middleware import _trusted_origin

    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("SPACE_HOST", "ruslanmv-gitpilot.hf.space")
    monkeypatch.setenv("GITPILOT_PUBLIC_BASE_URL", "https://gitpilot.ruslanmv.com")
    proxied = {"host": "10.0.0.5:7860"}  # what the app sees behind the Spaces proxy
    assert _trusted_origin("https://ruslanmv-gitpilot.hf.space", proxied)
    assert _trusted_origin("https://gitpilot.ruslanmv.com", proxied)
    assert not _trusted_origin("https://evil.example", proxied)


def test_auth_dir_prefers_explicit_then_persistent_storage(env, monkeypatch, tmp_path):
    from gitpilot.auth import crypto, deployment

    monkeypatch.setenv("GITPILOT_AUTH_DIR", str(tmp_path / "persist"))
    assert crypto.auth_dir() == tmp_path / "persist"
    monkeypatch.delenv("GITPILOT_AUTH_DIR")
    assert deployment.persistent_auth_dir() is None  # not on a Space
