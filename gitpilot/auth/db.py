"""Postgres for GitPilot accounts — connect + auto‑create the table.

GitPilot keeps its **own** account table (``gitpilot_accounts``), separate from
Matrix Builder's. When ``DATABASE_URL`` is set we use Postgres and ensure the
table exists on first use (idempotent ``CREATE TABLE IF NOT EXISTS``); otherwise
accounts fall back to the JSON store. Uses psycopg (v3) directly — install with
``pip install 'psycopg[binary]'``.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("gitpilot.auth.db")

TABLE = "gitpilot_accounts"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id                text PRIMARY KEY,
  email             text UNIQUE NOT NULL,
  password_hash     text,
  name              text,
  provider          text NOT NULL DEFAULT 'password',
  email_verified_at double precision,
  created_at        double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS {TABLE}_email_idx ON {TABLE} (email);
"""


def database_url() -> str | None:
    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def is_db_configured() -> bool:
    return database_url() is not None


@contextmanager
def connect():  # type: ignore[no-untyped-def]
    """A psycopg connection (autocommit). Raises if psycopg/DATABASE_URL missing."""
    import psycopg  # noqa: PLC0415 — optional dependency, imported lazily

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    conn = psycopg.connect(url, autocommit=True, connect_timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema() -> None:
    """Create the gitpilot_accounts table + index if they don't exist."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)


def verify_database() -> dict[str, Any]:
    """Connect, ensure the schema, and report status — the 'is the DB ready?' check.

    Returns ``{ok, backend, table, accounts}``. Never raises: on failure returns
    ``{ok: False, error: ...}`` so callers can degrade gracefully.
    """
    if not is_db_configured():
        return {"ok": False, "backend": "json", "error": "DATABASE_URL not set"}
    try:
        ensure_schema()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {TABLE}")  # noqa: S608 — TABLE is a constant
            count = cur.fetchone()[0]
        return {"ok": True, "backend": "postgres", "table": TABLE, "accounts": int(count)}
    except Exception as exc:  # noqa: BLE001 — report, don't crash
        logger.warning("gitpilot_accounts DB not ready: %s", exc)
        return {"ok": False, "backend": "json", "error": str(exc)}


def db_usable() -> bool:
    return verify_database().get("ok", False)


__all__ = [
    "TABLE",
    "database_url",
    "is_db_configured",
    "connect",
    "ensure_schema",
    "verify_database",
    "db_usable",
]
