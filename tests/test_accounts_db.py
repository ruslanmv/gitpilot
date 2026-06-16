"""End-to-end GitPilot account test against a real Postgres (e.g. Neon).

Runs only when DATABASE_URL points at a reachable Postgres (and psycopg is
installed); otherwise it skips, so CI without a DB still passes. Set:

    DATABASE_URL=postgresql://USER:PW@HOST/db?sslmode=require pytest tests/test_accounts_db.py

It verifies the DB is reachable, auto-creates the gitpilot_accounts table, and
runs signup -> verify -> login -> persisted+hashed -> cleanup.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot.auth import build_account_router
from gitpilot.auth.accounts import get_account_store, reset_account_store
from gitpilot.auth.db import verify_database
from gitpilot.auth.security import VERIFY_EMAIL, make_link_token

_status = verify_database()
pytestmark = pytest.mark.skipif(
    not _status.get("ok"),
    reason=f"no reachable Postgres (DATABASE_URL): {_status.get('error', 'unset')}",
)


def test_db_is_ready_and_table_created():
    s = verify_database()
    assert s["ok"] and s["backend"] == "postgres" and s["table"] == "gitpilot_accounts"


def test_account_end_to_end_on_postgres():
    reset_account_store()
    store = get_account_store()
    assert type(store).__name__ == "PgAccountStore"

    email = f"e2e-{uuid.uuid4().hex[:10]}@gitpilot.test"
    app = FastAPI()
    app.include_router(build_account_router())
    c = TestClient(app)
    try:
        assert c.post("/api/account/signup", json={"email": email, "password": "supersecret1"}).status_code == 202
        v = c.post("/api/account/verify-email", json={"token": make_link_token(email, VERIFY_EMAIL)})
        assert v.status_code == 200 and v.json()["email_verified"] is True
        assert c.post("/api/account/login", json={"email": email, "password": "supersecret1"}).status_code == 200

        acc = store.get_by_email(email)
        assert acc is not None and acc.is_verified
        assert acc.password_hash and "supersecret1" not in acc.password_hash
        assert acc.password_hash.startswith("pbkdf2_sha256$")
    finally:
        store.delete_by_email(email)
        reset_account_store()
