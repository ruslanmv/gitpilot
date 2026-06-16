"""GitPilot account API — account‑first auth (email/password + verification).

Identity model:
    GitPilot account  = who the user is (this module)
    GitHub connection = which repos they can access (existing /api/auth/* flow)

Security posture (OWASP Authentication Cheat Sheet):
- PBKDF2‑SHA256 600k password hashing; never store plaintext.
- Generic responses on signup / login / forgot‑password (no account enumeration).
- Email verified before the account is usable.
- Session is a signed, HttpOnly cookie (not localStorage); logout clears it.

Mounted only when ``GITPILOT_ENABLE_ACCOUNTS=true`` so it never disturbs the
existing bring‑your‑own‑GitHub‑token flow.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel

from .accounts import Account, get_account_store
from .db import verify_database
from .emailer import (
    email_config,
    public_base_url,
    send_reset_email,
    send_verification_email,
)
from .security import (
    RESET_PASSWORD,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    VERIFY_EMAIL,
    hash_password,
    make_link_token,
    make_session,
    read_link_token,
    read_session,
    verify_password,
)

logger = logging.getLogger("gitpilot.auth.router")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LINK_TTL = 900  # 15 minutes


# --- schemas ----------------------------------------------------------------


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenRequest(BaseModel):
    token: str


class EmailRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


class AccountResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    provider: str = "password"
    email_verified: bool = False


def _public(acc: Account) -> AccountResponse:
    return AccountResponse(
        id=acc.id,
        email=acc.email,
        name=acc.name,
        provider=acc.provider,
        email_verified=acc.is_verified,
    )


def _valid_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address.")
    return e


def _set_session_cookie(response: Response, acc: Account) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session(acc.id, acc.email),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=public_base_url().startswith("https"),
        samesite="lax",
        path="/",
    )


# --- dependency -------------------------------------------------------------


def current_account(gitpilot_session: str | None = Cookie(default=None)) -> Account:
    """The signed‑in account, or 401. Reads the HttpOnly session cookie."""
    claims = read_session(gitpilot_session)
    acc = get_account_store().get_by_id(claims["uid"]) if claims else None
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return acc


def build_account_router() -> APIRouter:  # noqa: PLR0915 — nested route handlers
    router = APIRouter(prefix="/api/account", tags=["account"])
    store = get_account_store()

    @router.get("/status")
    def status_() -> dict[str, bool]:
        return {"accounts_enabled": True}

    @router.get("/health")
    def health() -> dict[str, object]:
        """Is the account DB ready? Creates the table if needed, then reports."""
        return verify_database()

    @router.get("/email-health")
    def email_health() -> dict[str, object]:
        """Redacted email wiring report: provider, sender, base URL, last send.

        No secrets and no full recipient addresses are returned — only the
        information needed to diagnose 'signup returned 202 but no email came'.
        """
        return email_config()

    @router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
    def signup(payload: SignupRequest) -> dict[str, str]:
        email = _valid_email(payload.email)
        try:
            pw = hash_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        # Generic response either way (no enumeration). Only create + email a
        # genuinely new address; an existing one is left untouched.
        if store.get_by_email(email) is None:
            store.create(email, password_hash=pw, name=payload.name)
            sent = send_verification_email(
                email, make_link_token(email, VERIFY_EMAIL, ttl_seconds=_LINK_TTL)
            )
            if not sent:
                # The account exists but no email went out. Keep the response
                # generic (no enumeration), but make the failure loud in logs
                # and visible via /api/account/email-health.
                logger.warning(
                    "Signup created the account but verification email delivery FAILED. "
                    "Check /api/account/email-health (Resend key / sender domain / base URL)."
                )
        return {"message": "Check your email — we sent a confirmation link."}

    @router.post("/verify-email", response_model=AccountResponse)
    def verify_email(payload: TokenRequest, response: Response) -> AccountResponse:
        email = read_link_token(payload.token, VERIFY_EMAIL)
        if not email:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link.")
        store.mark_verified(email)
        acc = store.get_by_email(email)
        if acc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
        _set_session_cookie(response, acc)
        return _public(acc)

    @router.post("/login", response_model=AccountResponse)
    def login(payload: LoginRequest, response: Response) -> AccountResponse:
        email = (payload.email or "").strip().lower()
        acc = store.get_by_email(email)
        if acc is None or not verify_password(payload.password, acc.password_hash):
            # Identical message whether the email exists or the password is wrong.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
        if not acc.is_verified:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify your email before signing in.")
        _set_session_cookie(response, acc)
        return _public(acc)

    @router.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"message": "Signed out."}

    @router.get("/me", response_model=AccountResponse)
    def me(acc: Account = Depends(current_account)) -> AccountResponse:  # noqa: B008 — FastAPI idiom
        return _public(acc)

    @router.post("/password/forgot")
    def password_forgot(payload: EmailRequest) -> dict[str, str]:
        email = (payload.email or "").strip().lower()
        # Always generic — never reveal whether the account exists.
        if store.get_by_email(email) is not None:
            send_reset_email(email, make_link_token(email, RESET_PASSWORD, ttl_seconds=_LINK_TTL))
        return {"message": "If an account exists, we'll send a reset link."}

    @router.post("/password/reset", response_model=AccountResponse)
    def password_reset(payload: ResetRequest, response: Response) -> AccountResponse:
        email = read_link_token(payload.token, RESET_PASSWORD)
        if not email:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link.")
        try:
            pw = hash_password(payload.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        store.set_password(email, pw)
        acc = store.get_by_email(email)
        if acc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
        _set_session_cookie(response, acc)
        return _public(acc)

    return router


__all__ = ["build_account_router", "current_account"]
