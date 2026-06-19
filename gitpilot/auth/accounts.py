"""GitPilot account store — the durable user identity (account‑first model).

A GitPilot account is *who the user is*; a connected GitHub identity is *which
repositories they can access*. This store keeps the account record (email,
password hash, verification state). It is JSON‑file backed under
``GITPILOT_CONFIG_DIR`` for a single‑node deployment; the same interface maps to
a real per‑user database for multi‑tenant SaaS (every record is keyed by
``owner_id`` = the account id — never a shared/global record).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import time

from . import db


def _config_dir() -> Path:
    return Path(os.getenv("GITPILOT_CONFIG_DIR", str(Path.home() / ".gitpilot")))


def _accounts_file() -> Path:
    return _config_dir() / "accounts.json"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


@dataclass
class Account:
    id: str
    email: str
    password_hash: str | None = None
    name: str | None = None
    provider: str = "password"  # password | github | google
    email_verified_at: float | None = None
    created_at: float = field(default_factory=time)

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None


class AccountStore:
    """Thread-safe, JSON-persisted account store (one record per email)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_email: dict[str, Account] = {}
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        path = _accounts_file()
        if not path.exists():
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            for row in rows:
                acc = Account(**row)
                self._by_email[_norm(acc.email)] = acc
        except Exception:  # noqa: BLE001 — a corrupt file shouldn't crash startup
            self._by_email = {}

    def _save(self) -> None:
        path = _accounts_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps([asdict(a) for a in self._by_email.values()], indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001, S110 — best-effort persist; in-memory stays correct
            pass

    # -- queries -------------------------------------------------------------

    def get_by_email(self, email: str) -> Account | None:
        with self._lock:
            return self._by_email.get(_norm(email))

    def get_by_id(self, user_id: str) -> Account | None:
        with self._lock:
            return next((a for a in self._by_email.values() if a.id == user_id), None)

    # -- mutations -----------------------------------------------------------

    def create(
        self,
        email: str,
        *,
        password_hash: str | None = None,
        name: str | None = None,
        provider: str = "password",
        verified: bool = False,
    ) -> Account:
        with self._lock:
            key = _norm(email)
            acc = Account(
                id=f"acct_{uuid.uuid4().hex[:20]}",
                email=key,
                password_hash=password_hash,
                name=name,
                provider=provider,
                email_verified_at=time() if verified else None,
            )
            self._by_email[key] = acc
            self._save()
            return acc

    def mark_verified(self, email: str) -> None:
        with self._lock:
            acc = self._by_email.get(_norm(email))
            if acc and acc.email_verified_at is None:
                acc.email_verified_at = time()
                self._save()

    def set_password(self, email: str, password_hash: str) -> None:
        with self._lock:
            acc = self._by_email.get(_norm(email))
            if acc:
                acc.password_hash = password_hash
                # A successful reset proves inbox ownership -> verified.
                acc.email_verified_at = acc.email_verified_at or time()
                self._save()

    def set_name(self, email: str, name: str | None) -> None:
        with self._lock:
            acc = self._by_email.get(_norm(email))
            if acc:
                acc.name = name
                self._save()

    def delete_by_email(self, email: str) -> None:
        with self._lock:
            self._by_email.pop(_norm(email), None)
            self._save()


class PgAccountStore:
    """Postgres-backed account store (GitPilot's own ``gitpilot_accounts`` table).

    Same interface as the JSON store; selected automatically when ``DATABASE_URL``
    is set and the table is reachable. The table is created on first use.
    """

    # Table/column names are module constants (never user input); all user
    # values are bound as %s parameters, so the f-string queries are safe.

    def __init__(self) -> None:
        db.ensure_schema()

    @staticmethod
    def _row(r: tuple) -> Account:  # type: ignore[type-arg]
        return Account(
            id=r[0],
            email=r[1],
            password_hash=r[2],
            name=r[3],
            provider=r[4],
            email_verified_at=r[5],
            created_at=r[6],
        )

    _COLS = "id, email, password_hash, name, provider, email_verified_at, created_at"

    def get_by_email(self, email: str) -> Account | None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._COLS} FROM {db.TABLE} WHERE email = %s", (_norm(email),))  # noqa: S608
            row = cur.fetchone()
        return self._row(row) if row else None

    def get_by_id(self, user_id: str) -> Account | None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._COLS} FROM {db.TABLE} WHERE id = %s", (user_id,))  # noqa: S608
            row = cur.fetchone()
        return self._row(row) if row else None

    def create(
        self,
        email: str,
        *,
        password_hash: str | None = None,
        name: str | None = None,
        provider: str = "password",
        verified: bool = False,
    ) -> Account:
        acc = Account(
            id=f"acct_{uuid.uuid4().hex[:20]}",
            email=_norm(email),
            password_hash=password_hash,
            name=name,
            provider=provider,
            email_verified_at=time() if verified else None,
        )
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {db.TABLE} ({self._COLS}) VALUES (%s,%s,%s,%s,%s,%s,%s) "  # noqa: S608
                "ON CONFLICT (email) DO NOTHING",
                (
                    acc.id,
                    acc.email,
                    acc.password_hash,
                    acc.name,
                    acc.provider,
                    acc.email_verified_at,
                    acc.created_at,
                ),
            )
        return acc

    def mark_verified(self, email: str) -> None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {db.TABLE} SET email_verified_at = %s "  # noqa: S608
                "WHERE email = %s AND email_verified_at IS NULL",
                (time(), _norm(email)),
            )

    def set_password(self, email: str, password_hash: str) -> None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {db.TABLE} SET password_hash = %s, "  # noqa: S608
                "email_verified_at = COALESCE(email_verified_at, %s) WHERE email = %s",
                (password_hash, time(), _norm(email)),
            )

    def set_name(self, email: str, name: str | None) -> None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {db.TABLE} SET name = %s WHERE email = %s",  # noqa: S608
                (name, _norm(email)),
            )

    def delete_by_email(self, email: str) -> None:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {db.TABLE} WHERE email = %s", (_norm(email),))  # noqa: S608


_store: AccountStore | PgAccountStore | None = None
_store_lock = threading.Lock()


def get_account_store() -> AccountStore | PgAccountStore:
    """Postgres store when DATABASE_URL is set and reachable; JSON otherwise."""
    global _store  # noqa: PLW0603 — module-level singleton
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _build_store()
    return _store


def _build_store() -> AccountStore | PgAccountStore:
    if db.is_db_configured():
        try:
            return PgAccountStore()  # creates the table on first use
        except Exception:  # noqa: BLE001 — DB down / driver missing -> JSON fallback
            logging.getLogger("gitpilot.auth").warning(
                "DATABASE_URL set but Postgres unavailable; using JSON account store"
            )
    return AccountStore()


def reset_account_store() -> None:
    """Test helper — forget the cached store (and any loaded file)."""
    global _store  # noqa: PLW0603 — module-level singleton
    with _store_lock:
        _store = None


__all__ = [
    "Account",
    "AccountStore",
    "PgAccountStore",
    "get_account_store",
    "reset_account_store",
]
