"""Security primitives for GitPilot accounts (no third-party crypto deps).

- Password hashing: PBKDF2-HMAC-SHA256, 600k iterations (OWASP 2024).
- Signed, expiring tokens: HMAC-SHA256 over a base64url JSON payload — used for
  email-verification / password-reset links and for the session cookie.

These are the same patterns Matrix Builder uses; kept stdlib-only so GitPilot
gains accounts without new dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

# --- config -----------------------------------------------------------------

_DEV_SECRET = "gitpilot-dev-only-change-me"  # noqa: S105 — dev fallback signing key, not a password


def auth_secret() -> str:
    """HMAC signing key for tokens/sessions. MUST be set in production."""
    return os.getenv("GITPILOT_AUTH_SECRET", _DEV_SECRET)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- password hashing -------------------------------------------------------

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
MIN_PASSWORD_LEN = 8


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${_b64u(salt)}${_b64u(dk)}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not password or not encoded:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$", 3)
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64u_decode(salt_b64), int(iters)
        )
    except Exception:  # noqa: BLE001 — any malformed hash is a non-match
        return False
    return hmac.compare_digest(dk, _b64u_decode(hash_b64))


# --- signed tokens (links + sessions) --------------------------------------


def sign(payload: dict[str, Any], *, ttl_seconds: int, secret: str | None = None) -> str:
    """Sign a JSON payload with an expiry. Returns ``<body>.<sig>`` (URL-safe)."""
    data = dict(payload)
    data["exp"] = int(time.time()) + int(ttl_seconds)
    body = _b64u(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64u(
        hmac.new(
            (secret or auth_secret()).encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
    )
    return f"{body}.{sig}"


def unsign(token: str, *, secret: str | None = None) -> dict[str, Any] | None:
    """Verify signature + expiry. Returns the payload, or None if invalid/expired."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64u(
            hmac.new(
                (secret or auth_secret()).encode("utf-8"), body.encode("ascii"), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64u_decode(body))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# --- purpose-scoped link tokens (email verification / password reset) ------

VERIFY_EMAIL = "verify-email"
RESET_PASSWORD = "reset-password"  # noqa: S105 — token-purpose label, not a password


def make_link_token(email: str, purpose: str, *, ttl_seconds: int = 900) -> str:
    return sign({"email": email.lower(), "purpose": purpose}, ttl_seconds=ttl_seconds)


def read_link_token(token: str, purpose: str) -> str | None:
    payload = unsign(token)
    if not payload or payload.get("purpose") != purpose:
        return None
    email = payload.get("email")
    return str(email) if email else None


# --- session tokens (HttpOnly cookie value) --------------------------------

SESSION_COOKIE = "gitpilot_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600


def make_session(user_id: str, email: str) -> str:
    return sign({"uid": user_id, "email": email.lower()}, ttl_seconds=SESSION_TTL_SECONDS)


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return unsign(token)


__all__ = [
    "auth_secret",
    "hash_password",
    "verify_password",
    "sign",
    "unsign",
    "VERIFY_EMAIL",
    "RESET_PASSWORD",
    "make_link_token",
    "read_link_token",
    "SESSION_COOKIE",
    "SESSION_TTL_SECONDS",
    "make_session",
    "read_session",
    "MIN_PASSWORD_LEN",
]
