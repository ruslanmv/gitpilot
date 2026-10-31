"""OllaBridge device pairing: where the token goes and who uses it.

OllaBridge Cloud authenticates every ``/v1/*`` call with the device token that
pairing issues (``Authorization: Bearer``). Before this, the web UI put the
token only into the form, so "Load Models" (and every model call) went out
without it and got 401. On a shared deployment it must also never become the
server's token: it belongs to one person's OllaBridge account.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import model_catalog, ollabridge_auth
from gitpilot import settings as settings_mod
from gitpilot.auth import crypto
from gitpilot.auth import web_session as ws
from gitpilot.auth.middleware import WebSessionMiddleware
from tests._forge_fakes import FakeForges

CLOUD = "app.ollabridge.com"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITPILOT_TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"o" * 32).decode())
    for var in ("SPACE_ID", "SPACE_HOST", "GITPILOT_ENABLE_ACCOUNTS", "GITPILOT_MULTI_USER",
                "OLLABRIDGE_API_KEY", "OLLABRIDGE_BASE_URL", "DATABASE_URL", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    crypto.reset_cipher()
    ws.reset_store(tmp_path / "sessions.db")
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_FILE", tmp_path / "settings.json")
    fresh = settings_mod.AppSettings()
    fresh.ollabridge.base_url = "https://app.ollabridge.com"
    fresh.ollabridge.api_key = ""
    monkeypatch.setattr(settings_mod, "_settings", fresh)
    yield tmp_path
    ws.reset_store(None)
    crypto.reset_cipher()


@pytest.fixture
def cloud(env, monkeypatch):
    """A fake OllaBridge Cloud: code FHHF-3765 pairs once, /v1/models needs the token."""
    fake = FakeForges().install(monkeypatch)
    used = set()

    def pair(req, _m):
        body = json.loads(req.content)
        code = body["code"].replace("-", "")
        if code != "FHHF3765" or code in used:
            return httpx.Response(200, json={"status": "error", "error": "Invalid pairing code"})
        used.add(code)
        return httpx.Response(200, json={"status": "ok", "device_token": "devtok-secret", "device_id": "dev_1"})

    fake.on("POST", CLOUD, "/device/pair-simple", pair)

    def models(url, headers=None, timeout=None):
        """``requests.get`` stand-in for model listing."""
        resp = httpx.Response(200 if (headers or {}).get("Authorization") == "Bearer devtok-secret" else 401,
                              json={"data": [{"id": "qwen2.5:1.5b"}, {"id": "llama3:8b"}]},
                              request=httpx.Request("GET", url))
        class R:
            status_code = resp.status_code

            def raise_for_status(self):
                if resp.status_code >= 400:
                    import requests

                    err = requests.HTTPError(f"{resp.status_code} Client Error")
                    err.response = self
                    raise err

            def json(self):
                return resp.json()

        return R()

    monkeypatch.setattr(model_catalog.requests, "get", models)
    return fake


@pytest.fixture
def app_client(cloud):
    from gitpilot._api_app import api_get_settings, api_list_models
    from gitpilot.ollabridge_proxy import router

    app = FastAPI()
    app.include_router(router)
    app.add_api_route("/api/settings", api_get_settings, methods=["GET"])
    app.add_api_route("/api/settings/models", api_list_models, methods=["GET"])
    app.add_middleware(WebSessionMiddleware)
    return TestClient(app)


def _session_header():
    raw, _ = ws.get_store().create()
    return {"X-GitPilot-Session": raw}


# --- multi-user (the Hugging Face Space / production) -----------------------------

@pytest.fixture
def shared(monkeypatch):
    monkeypatch.setenv("GITPILOT_MULTI_USER", "true")


def test_pairing_keeps_the_token_on_the_users_session_only(app_client, cloud, shared):
    alice = _session_header()
    resp = app_client.post("/api/ollabridge/pair", json={"base_url": "", "code": " fhhf-3765 "}, headers=alice)
    data = resp.json()
    assert resp.status_code == 200 and data["success"] and data["stored"] == "session"
    assert data["token"] is None and "devtok-secret" not in resp.text      # never to the browser
    assert data["base_url"] == "https://app.ollabridge.com"                  # the default server
    sent = cloud.to(CLOUD)[0].body
    assert sent["code"] == "FHHF-3765" and sent["client"]["name"] == "GitPilot"
    assert settings_mod.get_settings().ollabridge.api_key == ""              # not the server's token
    assert not (settings_mod.CONFIG_FILE.exists() and "devtok" in settings_mod.CONFIG_FILE.read_text())

    page = app_client.get("/api/settings", headers=alice)
    assert page.json()["ollabridge_pairing"]["scope"] == "session"
    assert "devtok-secret" not in page.text


def test_load_models_uses_the_paired_account(app_client, cloud, shared):
    alice, bob = _session_header(), _session_header()
    app_client.post("/api/ollabridge/pair", json={"code": "FHHF3765"}, headers=alice)

    mine = app_client.get("/api/settings/models?provider=ollabridge", headers=alice).json()
    assert mine["error"] is None and mine["models"] == ["llama3:8b", "qwen2.5:1.5b"]

    theirs = app_client.get("/api/settings/models?provider=ollabridge", headers=bob).json()
    assert theirs["models"] == [] and "Device Pairing" in theirs["error"]   # another user: no token
    assert app_client.get("/api/settings", headers=bob).json()["ollabridge_pairing"] is None


def test_without_a_session_the_code_is_not_spent(app_client, cloud, shared):
    resp = app_client.post("/api/ollabridge/pair", json={"code": "FHHF3765"})
    assert resp.status_code == 401
    assert cloud.to(CLOUD) == []                                             # single-use code kept
    assert app_client.post("/api/ollabridge/pair", json={"code": "FHHF3765"},
                           headers=_session_header()).json()["success"]


@pytest.mark.parametrize("url", ["http://app.ollabridge.com", "https://127.0.0.1:11435",
                                 "https://user:pw@app.ollabridge.com", "file:///etc/passwd"])
def test_a_shared_server_only_pairs_with_public_https_servers(app_client, cloud, shared, url):
    resp = app_client.post("/api/ollabridge/pair", json={"base_url": url, "code": "FHHF3765"},
                           headers=_session_header())
    assert resp.status_code == 400
    assert cloud.to(CLOUD) == []


def test_unpair_forgets_the_token(app_client, cloud, shared):
    alice = _session_header()
    app_client.post("/api/ollabridge/pair", json={"code": "FHHF3765"}, headers=alice)
    assert app_client.delete("/api/ollabridge/pair", headers=alice).json() == {"paired": False, "scope": "session"}
    assert app_client.get("/api/settings", headers=alice).json()["ollabridge_pairing"] is None


def test_model_calls_resolve_to_the_users_token_and_its_server(env, shared):
    raw, sess = ws.get_store().create()
    ws.get_store().save_credential(sess, ws.ProviderCredential(
        provider="ollabridge", access_token="devtok-secret",
        profile={"base_url": "https://app.ollabridge.com"}))
    settings = settings_mod.get_settings()
    settings.ollabridge.base_url = "https://ruslanmv-ollabridge.hf.space"   # server default differs
    token = ws._current.set(ws.get_store().resolve(raw))
    try:
        assert ollabridge_auth.resolve(settings) == ("https://app.ollabridge.com", "devtok-secret")
        from gitpilot.direct_chat import resolve_endpoint

        settings.provider = settings_mod.LLMProvider.ollabridge
        endpoint = resolve_endpoint(settings)                                # v4 loop / direct chat
        assert endpoint.api_key == "devtok-secret"
        assert endpoint.base_url.startswith("https://app.ollabridge.com")
    finally:
        ws._current.reset(token)
    assert ollabridge_auth.resolve(settings) == ("https://ruslanmv-ollabridge.hf.space", "")


# --- single-user install -----------------------------------------------------------

def test_single_user_pairing_configures_the_server(app_client, cloud):
    resp = app_client.post("/api/ollabridge/pair", json={"base_url": "https://app.ollabridge.com/v1/",
                                                         "code": "FHHF3765"})
    data = resp.json()
    assert data["success"] and data["stored"] == "server"
    assert data["token"] == "devtok-secret"                                  # VS Code extension keeps it
    s = settings_mod.get_settings()
    assert s.ollabridge.api_key == "devtok-secret" and s.ollabridge.base_url == "https://app.ollabridge.com"
    models = app_client.get("/api/settings/models?provider=ollabridge").json()
    assert models["models"] == ["llama3:8b", "qwen2.5:1.5b"]
    assert app_client.get("/api/settings").json()["ollabridge_pairing"]["scope"] == "server"


def test_an_invalid_code_reports_the_clouds_message(app_client, cloud):
    data = app_client.post("/api/ollabridge/pair", json={"code": "ZZZZ0000"}).json()
    assert data == {**data, "success": False, "error": "Invalid pairing code"}


def test_a_revoked_pairing_says_so(app_client, cloud, shared):
    alice = _session_header()
    raw = alice["X-GitPilot-Session"]
    sess = ws.get_store().resolve(raw)
    ws.get_store().save_credential(sess, ws.ProviderCredential(
        provider="ollabridge", access_token="revoked-token", profile={"base_url": "https://app.ollabridge.com"}))
    data = app_client.get("/api/settings/models?provider=ollabridge", headers=alice).json()
    assert data["models"] == [] and "rejected the saved token" in data["error"]
