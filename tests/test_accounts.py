"""GitPilot account-first auth: primitives + the full email/password flow.

Covers OWASP-aligned behaviour: strong hashing, signed expiring tokens, generic
(non-enumerating) responses, email verification before use, and HttpOnly
session cookies.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.auth import build_account_router
from gitpilot.auth.accounts import get_account_store, reset_account_store
from gitpilot.auth.security import (
    VERIFY_EMAIL,
    hash_password,
    make_link_token,
    read_link_token,
    sign,
    unsign,
    verify_password,
)


# --- primitives -------------------------------------------------------------


def test_password_hash_roundtrip_and_strength():
    h = hash_password("correct horse battery")
    assert h.startswith("pbkdf2_sha256$600000$")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", None)
    with pytest.raises(ValueError):
        hash_password("short")  # < 8 chars


def test_signed_token_tamper_and_expiry():
    tok = sign({"k": "v"}, ttl_seconds=60)
    assert unsign(tok) == {"k": "v", "exp": pytest.approx(int(time.time()) + 60, abs=2)}
    body, sig = tok.split(".", 1)
    assert unsign(f"{body}.{sig}x") is None  # tampered signature
    assert unsign(sign({"k": "v"}, ttl_seconds=-1)) is None  # expired


def test_link_token_is_purpose_scoped():
    tok = make_link_token("a@b.co", VERIFY_EMAIL)
    assert read_link_token(tok, VERIFY_EMAIL) == "a@b.co"
    assert read_link_token(tok, "reset-password") is None  # wrong purpose rejected


# --- API flow ---------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))  # isolated store
    monkeypatch.setenv("GITPILOT_AUTH_SECRET", "test-secret-xyz")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)  # dev no-op email
    monkeypatch.delenv("DATABASE_URL", raising=False)  # force the JSON store here
    reset_account_store()
    app = FastAPI()
    app.include_router(build_account_router())
    yield TestClient(app)
    reset_account_store()


def _verify_token(email: str) -> str:
    return make_link_token(email, VERIFY_EMAIL)


def test_signup_then_verify_then_login(client):
    # Signup is generic + 202 (does not reveal account state).
    r = client.post("/api/account/signup", json={"email": "Dev@Example.com", "password": "supersecret1"})
    assert r.status_code == 202 and "Check your email" in r.json()["message"]

    # Cannot log in before verifying.
    pre = client.post("/api/account/login", json={"email": "dev@example.com", "password": "supersecret1"})
    assert pre.status_code == 403

    # Verify -> session cookie set.
    v = client.post("/api/account/verify-email", json={"token": _verify_token("dev@example.com")})
    assert v.status_code == 200 and v.json()["email_verified"] is True
    assert "gitpilot_session" in v.cookies or "set-cookie" in {k.lower() for k in v.headers}

    # Login works after verification and sets a session.
    li = client.post("/api/account/login", json={"email": "dev@example.com", "password": "supersecret1"})
    assert li.status_code == 200
    me = client.get("/api/account/me")
    assert me.status_code == 200 and me.json()["email"] == "dev@example.com"


def test_login_is_generic_and_does_not_enumerate(client):
    # Unknown account and wrong password return the identical 401 message.
    a = client.post("/api/account/login", json={"email": "nobody@x.co", "password": "whatever12"})
    client.post("/api/account/signup", json={"email": "real@x.co", "password": "supersecret1"})
    client.post("/api/account/verify-email", json={"token": _verify_token("real@x.co")})
    b = client.post("/api/account/login", json={"email": "real@x.co", "password": "wrongpass12"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"] == "Invalid email or password."


def test_forgot_password_is_always_generic(client):
    # Same response whether or not the account exists (no enumeration).
    miss = client.post("/api/account/password/forgot", json={"email": "ghost@x.co"})
    client.post("/api/account/signup", json={"email": "has@x.co", "password": "supersecret1"})
    hit = client.post("/api/account/password/forgot", json={"email": "has@x.co"})
    assert miss.status_code == hit.status_code == 200
    assert miss.json() == hit.json()


def test_reset_password_then_login(client):
    client.post("/api/account/signup", json={"email": "r@x.co", "password": "oldpassword1"})
    from gitpilot.auth.security import RESET_PASSWORD

    tok = make_link_token("r@x.co", RESET_PASSWORD)
    rr = client.post("/api/account/password/reset", json={"token": tok, "password": "newpassword1"})
    assert rr.status_code == 200 and rr.json()["email_verified"] is True  # reset proves ownership
    ok = client.post("/api/account/login", json={"email": "r@x.co", "password": "newpassword1"})
    assert ok.status_code == 200


def test_me_requires_session(client):
    assert client.get("/api/account/me").status_code == 401


def test_passwords_are_hashed_at_rest(client, tmp_path):
    client.post("/api/account/signup", json={"email": "h@x.co", "password": "supersecret1"})
    acc = get_account_store().get_by_email("h@x.co")
    assert acc is not None
    assert acc.password_hash and "supersecret1" not in acc.password_hash
    assert acc.password_hash.startswith("pbkdf2_sha256$")
