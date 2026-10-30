"""The Postgres backend of the web-session store.

Production keeps accounts in Postgres (``DATABASE_URL``) and runs on an
ephemeral container, so sessions must live there too. Without a database in
CI, the Postgres code path runs against a psycopg-style adapter over SQLite that
accepts only ``%s`` placeholders and real transactions; the statements are the
SQLite/Postgres common subset (``ON CONFLICT ... DO UPDATE``, ``RETURNING``).
Set ``GITPILOT_TEST_DATABASE_URL`` to also run against a real Postgres.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import time
from contextlib import contextmanager

import pytest

from gitpilot.auth import crypto
from gitpilot.auth import web_session as ws


class FakePsycopg:
    """Just enough of a psycopg 3 connection, backed by SQLite."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.closed = False
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        assert "?" not in sql, f"qmark placeholder sent to Postgres: {sql}"
        self.statements.append(" ".join(sql.split()))
        return self.db.execute(sql.replace("%s", "?"), params)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def close(self):
        self.closed = True
        self.db.close()


@pytest.fixture
def vault(monkeypatch, tmp_path):
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITPILOT_TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"p" * 32).decode())
    crypto.reset_cipher()
    yield tmp_path
    crypto.reset_cipher()
    ws.reset_store(None)


@pytest.fixture
def pg_store(vault, monkeypatch):
    fake = FakePsycopg(vault / "pg.db")
    monkeypatch.setattr(ws.WebSessionStore, "_pg_connection", lambda self: fake)
    store = ws.WebSessionStore(database_url="postgresql://gitpilot@db.example/gitpilot")
    store.fake = fake  # type: ignore[attr-defined]
    return store


def _exercise(store: ws.WebSessionStore) -> None:
    raw, sess = store.create()
    store.save_credential(sess, ws.ProviderCredential(provider="gitlab", access_token="gl-1",
                                                      refresh_token="r-1", expires_at=time.time() + 60))
    store.save_credential(sess, ws.ProviderCredential(provider="gitlab", access_token="gl-2"))  # upsert
    store.save_credential(sess, ws.ProviderCredential(provider="github", access_token="gho-1"))
    got = store.resolve(raw)
    assert got is not None and got.credentials["gitlab"].access_token == "gl-2"
    assert set(got.credentials) == {"github", "gitlab"}

    new_raw = store.rotate(got, account_id="acct_1")
    assert store.resolve(raw) is None
    rotated = store.resolve(new_raw)
    assert rotated.account_id == "acct_1" and set(rotated.credentials) == {"github", "gitlab"}

    store.put_oauth_state(provider="gitlab", state="s" * 43, verifier="v", redirect_uri="https://x/auth",
                          session=rotated)
    assert store.take_oauth_state("gitlab", "s" * 43)["redirect_uri"] == "https://x/auth"
    assert store.take_oauth_state("gitlab", "s" * 43) is None  # single use

    ticket = store.issue_ws_ticket(rotated)
    assert store.redeem_ws_ticket(ticket).id_hash == rotated.id_hash
    assert store.redeem_ws_ticket(ticket) is None

    assert store.delete_credential(rotated, "github").access_token == "gho-1"
    assert store.revoke_account_sessions("acct_1") == 1
    assert store.resolve(new_raw) is None


def test_postgres_path_end_to_end(pg_store):
    _exercise(pg_store)
    joined = "\n".join(pg_store.fake.statements)
    assert "gitpilot_web_sessions" in joined and "BYTEA" in joined
    assert "ON CONFLICT (session_hash, provider) DO UPDATE" in joined
    assert "DELETE FROM gitpilot_oauth_states WHERE state_hash=%s RETURNING" in joined


def test_sqlite_path_runs_the_same_operations(vault):
    _exercise(ws.WebSessionStore(vault / "local.db"))


def test_rotation_is_atomic(pg_store, monkeypatch):
    raw, sess = pg_store.create()
    pg_store.save_credential(sess, ws.ProviderCredential(provider="github", access_token="t"))
    real = pg_store.fake.execute

    def failing(sql, params=()):
        if sql.lstrip().startswith("DELETE FROM gitpilot_ws_tickets"):
            raise RuntimeError("connection lost")
        return real(sql, params)

    monkeypatch.setattr(pg_store.fake, "execute", failing)
    with pytest.raises(RuntimeError):
        pg_store.rotate(sess)
    monkeypatch.setattr(pg_store.fake, "execute", real)
    survivor = pg_store.resolve(raw)  # the old id still works: nothing half-applied
    assert survivor is not None and survivor.credentials["github"].access_token == "t"


def test_database_url_selects_postgres_and_falls_back_safely(vault, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://gitpilot@db.example/gitpilot")
    monkeypatch.delenv("GITPILOT_AUTH_DB", raising=False)
    fake = FakePsycopg(vault / "pg2.db")
    monkeypatch.setattr(ws.WebSessionStore, "_pg_connection", lambda self: fake)
    ws.reset_store(None)
    assert ws.get_store().dialect == "postgres"

    def down(self):
        raise OSError("database unreachable")

    monkeypatch.setattr(ws.WebSessionStore, "_pg_connection", down)
    ws.reset_store(None)
    assert ws.get_store().dialect == "sqlite"  # sign-in keeps working

    monkeypatch.setenv("GITPILOT_SESSION_STORE", "sqlite")
    monkeypatch.setattr(ws.WebSessionStore, "_pg_connection", lambda self: fake)
    ws.reset_store(None)
    assert ws.get_store().dialect == "sqlite"


@pytest.mark.skipif(not os.getenv("GITPILOT_TEST_DATABASE_URL"), reason="no test Postgres configured")
def test_real_postgres(vault):  # pragma: no cover - needs a database
    _exercise(ws.WebSessionStore(database_url=os.environ["GITPILOT_TEST_DATABASE_URL"]))
