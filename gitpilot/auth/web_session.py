"""Server-side web sessions and the provider-credential vault.

A browser signs in once and holds only an opaque session id, in an HttpOnly
cookie (``gitpilot_session``). Everything else stays on the server:

* the GitHub and GitLab OAuth access/refresh tokens, encrypted with
  :mod:`gitpilot.auth.crypto`, one row per (session, provider);
* the CSRF token the SPA echoes in ``X-GitPilot-CSRF`` for cookie-authenticated
  writes;
* the GitPilot account the session belongs to, if any;
* pending OAuth ``state`` values with their PKCE verifiers, so any worker can
  complete a flow another worker started;
* single-use WebSocket tickets.

The session id is never stored: rows are keyed by its SHA-256, so a copy of the
database does not yield usable sessions. Sessions expire after
``GITPILOT_SESSION_TTL_HOURS`` (default 7 days) of inactivity and are revoked
server-side on logout.

Deployments that serve the SPA and the API from different sites cannot rely on
cookies (third-party cookie blocking); there the SPA sends the same session id
in the ``X-GitPilot-Session`` header instead. Provider tokens stay server-side
either way.

Storage is Postgres when ``DATABASE_URL`` is set (the database the account
store already uses; sessions then survive restarts and redeploys and are shared
by every replica), else SQLite (``<auth dir>/web_sessions.db``, WAL mode),
shared by every worker on one host. ``GITPILOT_AUTH_DB`` (a SQLite path) or
``GITPILOT_SESSION_STORE=sqlite`` forces SQLite.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .crypto import auth_dir, seal, unseal

logger = logging.getLogger("gitpilot.auth.web_session")

SESSION_COOKIE = "gitpilot_session"
SESSION_HEADER = "x-gitpilot-session"
CSRF_HEADER = "x-gitpilot-csrf"
PROVIDER_HEADER = "x-gitpilot-git-provider"
SESSION_PREFIX = "gps_"
OAUTH_STATE_TTL = 600
WS_TICKET_TTL = 60
#: Refresh an access token this long before it expires.
REFRESH_MARGIN = 120


def session_ttl_seconds() -> int:
    """Idle lifetime of a web session."""
    try:
        hours = float(os.getenv("GITPILOT_SESSION_TTL_HOURS", "168"))
    except ValueError:
        hours = 168.0
    return int(max(hours, 0.1) * 3600)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class ProviderCredential:
    """A provider credential as held in memory for one request."""

    provider: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    scope: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)

    def expiring(self, margin: float = REFRESH_MARGIN) -> bool:
        return self.expires_at is not None and time.time() >= float(self.expires_at) - margin

    def public(self) -> Dict[str, Any]:
        """What the browser may see: identity and expiry, never the tokens."""
        return {
            "connected": True,
            "user": self.profile,
            "scope": self.scope,
            "expires_at": self.expires_at,
            "refreshable": bool(self.refresh_token),
        }


@dataclass
class WebSession:
    """A resolved session for the current request."""

    id_hash: str
    csrf_token: str
    account_id: Optional[str]
    created_at: float
    expires_at: float
    #: "cookie" or "header" — CSRF checks apply to cookie-borne sessions only.
    transport: str = "cookie"
    credentials: Dict[str, ProviderCredential] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-request context
# ---------------------------------------------------------------------------

_current: contextvars.ContextVar[Optional[WebSession]] = contextvars.ContextVar("web_session", default=None)
_declared_provider: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "declared_git_provider", default=None
)


def current_session() -> Optional[WebSession]:
    """The web session resolved for the current request, if any."""
    return _current.get()


def declared_provider() -> Optional[str]:
    """The ``X-GitPilot-Git-Provider`` the client declared for this request."""
    return _declared_provider.get()


@contextmanager
def bind(session: Optional[WebSession], declared: Optional[str] = None) -> Iterator[None]:
    """Bind a session (and declared provider) for the duration of a request."""
    t1 = _current.set(session)
    t2 = _declared_provider.set(declared)
    try:
        yield
    finally:
        _current.reset(t1)
        _declared_provider.reset(t2)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

#: Tables share the ``gitpilot_`` prefix of ``gitpilot_accounts``: in production
#: they live in the same Postgres database as other applications' tables.
T_SESSIONS = "gitpilot_web_sessions"
T_CREDENTIALS = "gitpilot_web_credentials"
T_OAUTH = "gitpilot_oauth_states"
T_TICKETS = "gitpilot_ws_tickets"


def _schema(dialect: str) -> list[str]:
    """DDL for one backend. Everything else is SQL both SQLite and Postgres run."""
    blob = "BYTEA" if dialect == "postgres" else "BLOB"
    return [
        f"""CREATE TABLE IF NOT EXISTS {T_SESSIONS} (
            id_hash     TEXT PRIMARY KEY,
            csrf        TEXT NOT NULL,
            account_id  TEXT,
            created_at  DOUBLE PRECISION NOT NULL,
            last_seen   DOUBLE PRECISION NOT NULL,
            expires_at  DOUBLE PRECISION NOT NULL
        )""",
        f"CREATE INDEX IF NOT EXISTS {T_SESSIONS}_account ON {T_SESSIONS} (account_id)",
        f"CREATE INDEX IF NOT EXISTS {T_SESSIONS}_expires ON {T_SESSIONS} (expires_at)",
        f"""CREATE TABLE IF NOT EXISTS {T_CREDENTIALS} (
            session_hash TEXT NOT NULL,
            provider     TEXT NOT NULL,
            access_enc   {blob} NOT NULL,
            refresh_enc  {blob},
            expires_at   DOUBLE PRECISION,
            scope        TEXT,
            profile      TEXT,
            updated_at   DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (session_hash, provider)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {T_OAUTH} (
            state_hash   TEXT PRIMARY KEY,
            provider     TEXT NOT NULL,
            verifier_enc {blob} NOT NULL,
            redirect_uri TEXT,
            session_hash TEXT,
            return_to    TEXT,
            created_at   DOUBLE PRECISION NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS {T_TICKETS} (
            ticket_hash  TEXT PRIMARY KEY,
            session_hash TEXT NOT NULL,
            created_at   DOUBLE PRECISION NOT NULL
        )""",
    ]


class WebSessionStore:
    """Sessions, encrypted credentials, OAuth states and WebSocket tickets.

    Two backends run the same SQL: SQLite (default; ``GITPILOT_AUTH_DB`` or
    ``<auth dir>/web_sessions.db``) and Postgres (``database_url`` — chosen
    automatically when ``DATABASE_URL`` is set, as for accounts). Postgres keeps
    sessions across restarts and redeploys of an ephemeral container (e.g. a
    Hugging Face Space) and is shared by every replica.
    """

    def __init__(self, path: Optional[Path] = None, *, database_url: Optional[str] = None):
        self._lock = threading.RLock()
        self.database_url = database_url
        self.dialect = "postgres" if database_url else "sqlite"
        self.path: Optional[Path] = None
        if self.dialect == "sqlite":
            self.path = Path(path or os.getenv("GITPILOT_AUTH_DB") or (auth_dir() / "web_sessions.db"))
        self._pg: Any = None
        self._init()

    # -- connections ----------------------------------------------------------

    def _pg_connection(self) -> Any:
        import psycopg  # optional dependency, as for the Postgres account store

        if self._pg is None or getattr(self._pg, "closed", False):
            self._pg = psycopg.connect(self.database_url, autocommit=True, connect_timeout=10)
        return self._pg

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        if self.dialect == "postgres":
            try:
                yield self._pg_connection()
            except Exception:
                # Drop a broken connection; the next call reconnects.
                try:
                    if self._pg is not None:
                        self._pg.close()
                finally:
                    self._pg = None
                raise
            return
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            yield conn
        finally:
            conn.close()

    def _x(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Execute with the backend's placeholder style (statements are written with ``?``)."""
        if self.dialect == "postgres":
            sql = sql.replace("?", "%s")
        return conn.execute(sql, params)

    @contextmanager
    def _atomic(self, conn: Any) -> Iterator[None]:
        if self.dialect == "postgres":
            with conn.transaction():
                yield
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _init(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as conn:
            if self.dialect == "sqlite":
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:  # pragma: no cover - network filesystems
                    pass
            for statement in _schema(self.dialect):
                conn.execute(statement)
        if self.path is not None:
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # pragma: no cover - Windows
                pass

    # -- sessions ---------------------------------------------------------

    def create(self, account_id: Optional[str] = None) -> tuple[str, WebSession]:
        """Create a session; returns the raw id (for the cookie) and the session."""
        raw = SESSION_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        sess = WebSession(
            id_hash=_hash(raw), csrf_token=secrets.token_urlsafe(32), account_id=account_id,
            created_at=now, expires_at=now + session_ttl_seconds(),
        )
        with self._lock, self._conn() as conn:
            self._x(conn,
                    f"INSERT INTO {T_SESSIONS}(id_hash, csrf, account_id, created_at, last_seen, expires_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (sess.id_hash, sess.csrf_token, account_id, now, now, sess.expires_at))
        return raw, sess

    def resolve(self, raw: Optional[str], *, transport: str = "cookie",
                touch: bool = True) -> Optional[WebSession]:
        """Look up a live session by its raw id (and slide its expiry)."""
        if not raw or not raw.startswith(SESSION_PREFIX) or len(raw) > 200:
            return None
        id_hash = _hash(raw)
        now = time.time()
        with self._lock, self._conn() as conn:
            row = self._x(conn,
                          f"SELECT csrf, account_id, created_at, last_seen, expires_at FROM {T_SESSIONS}"
                          " WHERE id_hash=?", (id_hash,)).fetchone()
            if not row:
                return None
            csrf, account_id, created_at, last_seen, expires_at = row
            if expires_at <= now:
                self._delete(conn, id_hash)
                return None
            if touch and now - last_seen > 60:
                expires_at = now + session_ttl_seconds()
                self._x(conn, f"UPDATE {T_SESSIONS} SET last_seen=?, expires_at=? WHERE id_hash=?",
                        (now, expires_at, id_hash))
            creds = self._load_credentials(conn, id_hash)
        return WebSession(id_hash=id_hash, csrf_token=csrf, account_id=account_id,
                          created_at=created_at, expires_at=expires_at, transport=transport,
                          credentials=creds)

    def rotate(self, session: WebSession, *, account_id: Optional[str] = None,
               keep_account: bool = True) -> str:
        """Give a session a new id and CSRF token, keeping its credentials.

        Called on every sign-in or provider connection, so a session id planted
        before authentication (session fixation) never becomes an authenticated
        one. Returns the new raw id.
        """
        raw = SESSION_PREFIX + secrets.token_urlsafe(32)
        new_hash, new_csrf, now = _hash(raw), secrets.token_urlsafe(32), time.time()
        account = (account_id or session.account_id) if keep_account else account_id
        with self._lock, self._conn() as conn, self._atomic(conn):
            updated = self._x(
                conn,
                f"UPDATE {T_SESSIONS} SET id_hash=?, csrf=?, account_id=?, last_seen=?, expires_at=?"
                " WHERE id_hash=?",
                (new_hash, new_csrf, account, now, now + session_ttl_seconds(), session.id_hash),
            ).rowcount
            if not updated:  # revoked meanwhile: start a new, empty session
                self._x(conn,
                        f"INSERT INTO {T_SESSIONS}(id_hash, csrf, account_id, created_at, last_seen,"
                        " expires_at) VALUES (?,?,?,?,?,?)",
                        (new_hash, new_csrf, account, now, now, now + session_ttl_seconds()))
                session.credentials.clear()
            self._x(conn, f"UPDATE {T_CREDENTIALS} SET session_hash=? WHERE session_hash=?",
                    (new_hash, session.id_hash))
            self._x(conn, f"DELETE FROM {T_TICKETS} WHERE session_hash=?", (session.id_hash,))
        session.id_hash, session.csrf_token, session.account_id = new_hash, new_csrf, account
        session.expires_at = now + session_ttl_seconds()
        return raw

    def attach_account(self, session: WebSession, account_id: Optional[str]) -> None:
        """Bind (or unbind) a GitPilot account to a session."""
        with self._lock, self._conn() as conn:
            self._x(conn, f"UPDATE {T_SESSIONS} SET account_id=? WHERE id_hash=?", (account_id, session.id_hash))
        session.account_id = account_id

    def revoke(self, session: WebSession) -> None:
        """Delete a session and every credential it holds."""
        with self._lock, self._conn() as conn:
            self._delete(conn, session.id_hash)

    def revoke_account_sessions(self, account_id: str, *, keep: Optional[WebSession] = None) -> int:
        """Revoke every session of an account (password change/reset), except ``keep``."""
        with self._lock, self._conn() as conn:
            rows = self._x(conn, f"SELECT id_hash FROM {T_SESSIONS} WHERE account_id=?", (account_id,)).fetchall()
            n = 0
            for (id_hash,) in rows:
                if keep is not None and id_hash == keep.id_hash:
                    continue
                self._delete(conn, id_hash)
                n += 1
        return n

    def _delete(self, conn: Any, id_hash: str) -> None:
        self._x(conn, f"DELETE FROM {T_CREDENTIALS} WHERE session_hash=?", (id_hash,))
        self._x(conn, f"DELETE FROM {T_TICKETS} WHERE session_hash=?", (id_hash,))
        self._x(conn, f"DELETE FROM {T_SESSIONS} WHERE id_hash=?", (id_hash,))

    def purge_expired(self) -> None:
        """Remove expired sessions, OAuth states and tickets."""
        now = time.time()
        with self._lock, self._conn() as conn:
            expired = self._x(conn, f"SELECT id_hash FROM {T_SESSIONS} WHERE expires_at<=?", (now,)).fetchall()
            for (id_hash,) in expired:
                self._delete(conn, id_hash)
            self._x(conn, f"DELETE FROM {T_OAUTH} WHERE created_at<=?", (now - OAUTH_STATE_TTL,))
            self._x(conn, f"DELETE FROM {T_TICKETS} WHERE created_at<=?", (now - WS_TICKET_TTL,))

    # -- credentials --------------------------------------------------------

    def _load_credentials(self, conn: Any, id_hash: str) -> Dict[str, ProviderCredential]:
        out: Dict[str, ProviderCredential] = {}
        rows = self._x(conn,
                       f"SELECT provider, access_enc, refresh_enc, expires_at, scope, profile FROM {T_CREDENTIALS}"
                       " WHERE session_hash=?", (id_hash,)).fetchall()
        for provider, access_enc, refresh_enc, expires_at, scope, profile in rows:
            access = unseal(access_enc)
            if not access:
                continue
            try:
                prof = json.loads(profile) if profile else {}
            except ValueError:
                prof = {}
            out[provider] = ProviderCredential(
                provider=provider, access_token=access, refresh_token=unseal(refresh_enc),
                expires_at=expires_at, scope=scope or "", profile=prof,
            )
        return out

    def save_credential(self, session: WebSession, cred: ProviderCredential) -> None:
        """Store (encrypted) or replace a provider credential on a session."""
        with self._lock, self._conn() as conn:
            self._x(
                conn,
                f"INSERT INTO {T_CREDENTIALS}(session_hash, provider, access_enc, refresh_enc, expires_at,"
                " scope, profile, updated_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT (session_hash, provider) DO UPDATE SET access_enc=excluded.access_enc,"
                " refresh_enc=excluded.refresh_enc, expires_at=excluded.expires_at, scope=excluded.scope,"
                " profile=excluded.profile, updated_at=excluded.updated_at",
                (session.id_hash, cred.provider, seal(cred.access_token), seal(cred.refresh_token),
                 cred.expires_at, cred.scope, json.dumps(cred.profile or {}), time.time()),
            )
        session.credentials[cred.provider] = cred

    def reload_credential(self, session: WebSession, provider: str) -> Optional[ProviderCredential]:
        """Re-read one credential (another worker may have refreshed it)."""
        with self._lock, self._conn() as conn:
            creds = self._load_credentials(conn, session.id_hash)
        cred = creds.get(provider)
        if cred:
            session.credentials[provider] = cred
        else:
            session.credentials.pop(provider, None)
        return cred

    def delete_credential(self, session: WebSession, provider: str) -> Optional[ProviderCredential]:
        """Remove a provider credential from a session; returns what was removed."""
        cred = session.credentials.pop(provider, None)
        with self._lock, self._conn() as conn:
            self._x(conn, f"DELETE FROM {T_CREDENTIALS} WHERE session_hash=? AND provider=?",
                    (session.id_hash, provider))
        return cred

    # -- OAuth state ----------------------------------------------------------

    def put_oauth_state(self, *, provider: str, state: str, verifier: str, redirect_uri: Optional[str],
                        session: Optional[WebSession], return_to: Optional[str] = None) -> None:
        """Remember a pending authorization (state -> PKCE verifier, redirect, session)."""
        self.purge_expired()
        with self._lock, self._conn() as conn:
            self._x(conn,
                    f"INSERT INTO {T_OAUTH}(state_hash, provider, verifier_enc, redirect_uri, session_hash,"
                    " return_to, created_at) VALUES (?,?,?,?,?,?,?)",
                    (_hash(state), provider, seal(verifier), redirect_uri,
                     session.id_hash if session else None, return_to, time.time()))

    def take_oauth_state(self, provider: str, state: str) -> Optional[Dict[str, Any]]:
        """Consume a pending state exactly once. ``None`` if unknown, used, expired or for another provider."""
        if not state or len(state) > 512:
            return None
        with self._lock, self._conn() as conn:
            # One atomic statement: two requests racing with the same state
            # cannot both receive the row.
            row = self._x(conn,
                          f"DELETE FROM {T_OAUTH} WHERE state_hash=? RETURNING provider, verifier_enc,"
                          " redirect_uri, session_hash, return_to, created_at", (_hash(state),)).fetchone()
        if not row:
            return None
        row_provider, verifier_enc, redirect_uri, session_hash, return_to, created_at = row
        if row_provider != provider or time.time() - created_at > OAUTH_STATE_TTL:
            return None
        verifier = unseal(verifier_enc)
        if not verifier:
            return None
        return {"verifier": verifier, "redirect_uri": redirect_uri, "session_hash": session_hash,
                "return_to": return_to}

    # -- WebSocket tickets ------------------------------------------------------

    def issue_ws_ticket(self, session: WebSession) -> str:
        """A single-use, 60-second ticket that authenticates one WebSocket upgrade."""
        ticket = secrets.token_urlsafe(32)
        with self._lock, self._conn() as conn:
            self._x(conn, f"INSERT INTO {T_TICKETS}(ticket_hash, session_hash, created_at) VALUES (?,?,?)",
                    (_hash(ticket), session.id_hash, time.time()))
        return ticket

    def redeem_ws_ticket(self, ticket: Optional[str]) -> Optional[WebSession]:
        """Consume a ticket and return its session, if both are still valid."""
        if not ticket or len(ticket) > 200:
            return None
        with self._lock, self._conn() as conn:
            row = self._x(conn, f"DELETE FROM {T_TICKETS} WHERE ticket_hash=? RETURNING session_hash, created_at",
                          (_hash(ticket),)).fetchone()
            if not row or time.time() - row[1] > WS_TICKET_TTL:
                return None
            srow = self._x(conn,
                           f"SELECT csrf, account_id, created_at, expires_at FROM {T_SESSIONS} WHERE id_hash=?",
                           (row[0],)).fetchone()
            if not srow or srow[3] <= time.time():
                return None
            creds = self._load_credentials(conn, row[0])
        return WebSession(id_hash=row[0], csrf_token=srow[0], account_id=srow[1], created_at=srow[2],
                          expires_at=srow[3], transport="ticket", credentials=creds)


_store: Optional[WebSessionStore] = None
_store_lock = threading.Lock()


def _default_store() -> WebSessionStore:
    """Postgres when ``DATABASE_URL`` is set (unless ``GITPILOT_AUTH_DB`` or
    ``GITPILOT_SESSION_STORE=sqlite`` asks for SQLite), else SQLite."""
    mode = (os.getenv("GITPILOT_SESSION_STORE") or "").strip().lower()
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url and mode != "sqlite" and not os.getenv("GITPILOT_AUTH_DB"):
        try:
            store = WebSessionStore(database_url=url)
            logger.info("Web sessions: Postgres (DATABASE_URL)")
            return store
        except Exception:  # noqa: BLE001 - keep sign-in working on a database outage
            logger.exception(
                "Web sessions could not use DATABASE_URL; falling back to a local SQLite store "
                "(sessions will not survive a restart)."
            )
    return WebSessionStore()


def get_store() -> WebSessionStore:
    """The process-wide session store."""
    global _store
    with _store_lock:
        if _store is None:
            _store = _default_store()
        return _store


def reset_store(path: Optional[Path] = None) -> WebSessionStore:
    """Replace the store (tests)."""
    global _store
    with _store_lock:
        _store = WebSessionStore(path) if path else None
    return get_store()


# ---------------------------------------------------------------------------
# Cookies and CSRF
# ---------------------------------------------------------------------------

def _cors_wildcard() -> bool:
    return any(o.strip() == "*" for o in (os.getenv("CORS_ORIGINS") or "").split(","))


def cookie_samesite() -> str:
    """``lax`` by default; ``none`` only when explicitly configured and CORS is an allowlist.

    ``SameSite=None`` makes the cookie travel on cross-site requests, which is
    only safe when the API's CORS policy names its trusted origins.
    """
    value = (os.getenv("GITPILOT_COOKIE_SAMESITE") or "lax").strip().lower()
    if value not in ("lax", "strict", "none"):
        value = "lax"
    if value == "none" and _cors_wildcard():
        logger.warning("GITPILOT_COOKIE_SAMESITE=none is ignored while CORS_ORIGINS='*'; using lax.")
        value = "lax"
    return value


def cookie_secure(scheme: Optional[str]) -> bool:
    """Secure cookies on https (directly or behind a TLS-terminating proxy)."""
    forced = (os.getenv("GITPILOT_COOKIE_SECURE") or "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    from .deployment import public_base_url

    public = (public_base_url() or "").lower()
    return scheme == "https" or public.startswith("https")


def set_session_cookie(response: Any, raw_session: str, *, scheme: Optional[str]) -> None:
    """Set the HttpOnly session cookie on a Starlette response."""
    samesite = cookie_samesite()
    secure = cookie_secure(scheme) or samesite == "none"
    response.set_cookie(
        SESSION_COOKIE, raw_session, max_age=session_ttl_seconds(), httponly=True,
        secure=secure, samesite=samesite, path="/",
    )


def clear_session_cookie(response: Any, *, scheme: Optional[str]) -> None:
    """Expire the session cookie."""
    samesite = cookie_samesite()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(scheme) or samesite == "none",
                           httponly=True, samesite=samesite)


def csrf_ok(session: WebSession, presented: Optional[str]) -> bool:
    """Constant-time comparison of the presented CSRF token."""
    return bool(presented) and hmac.compare_digest(session.csrf_token, presented or "")


# ---------------------------------------------------------------------------
# Provider token resolution
# ---------------------------------------------------------------------------

def session_provider_token(provider: str) -> Optional[str]:
    """The current session's access token for ``provider``, if connected."""
    sess = current_session()
    if not sess:
        return None
    cred = sess.credentials.get(provider)
    return cred.access_token if cred else None


def resolve_provider_token(provider: str, authorization: Optional[str] = None) -> Optional[str]:
    """The credential a request may use for ``provider``.

    1. An explicit ``Authorization`` header (API clients, the VS Code
       extension), unless the client declared a different provider for the
       request or the token visibly belongs to the other provider.
    2. The provider credential stored server-side for the web session.

    ``None`` lets the provider helper fall back to the server's own token, as
    single-user installs always have.
    """
    from ..scm_context import token_belongs_to_other_provider

    header = _parse_authorization(authorization)
    if header:
        declared = declared_provider()
        if declared and declared != provider:
            header = None
        elif token_belongs_to_other_provider(header, provider):
            logger.warning("Ignored a %s request credential that belongs to another provider.", provider)
            header = None
    return header or session_provider_token(provider)


def _parse_authorization(value: Optional[str]) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    value = str(value).strip()
    for prefix in ("Bearer ", "bearer ", "token ", "Token "):
        if value.startswith(prefix):
            return value[len(prefix):].strip() or None
    return value


__all__ = [
    "SESSION_COOKIE", "SESSION_HEADER", "CSRF_HEADER", "PROVIDER_HEADER",
    "ProviderCredential", "WebSession", "WebSessionStore", "get_store", "reset_store",
    "current_session", "declared_provider", "bind", "set_session_cookie", "clear_session_cookie",
    "csrf_ok", "cookie_samesite", "cookie_secure", "session_provider_token",
    "resolve_provider_token", "session_ttl_seconds",
]
