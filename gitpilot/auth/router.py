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

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
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


class UpdateProfileRequest(BaseModel):
    name: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class DeleteAccountRequest(BaseModel):
    password: str | None = None


class AccountResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    provider: str = "password"
    email_verified: bool = False
    # Portable session token. Returned on login / verify / reset so SPAs on a
    # different origin than the API (e.g. Vercel frontend + HF backend) can
    # authenticate via the X-GitPilot-Session header — cross-site cookies are
    # unreliable (SameSite + third-party cookie blocking). Cookie is still set
    # for same-origin deployments.
    session_token: str | None = None


def _public(acc: Account, *, session_token: str | None = None) -> AccountResponse:
    return AccountResponse(
        id=acc.id,
        email=acc.email,
        name=acc.name,
        provider=acc.provider,
        email_verified=acc.is_verified,
        session_token=session_token,
    )


def _valid_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter a valid email address.")
    return e


def _issue_session(response: Response, acc: Account) -> str:
    """Mint a session, set the cookie, and return the token for the body.

    Production runs the SPA and API on different sites (Vercel + HF Space), so
    the cookie is set ``SameSite=None; Secure`` to survive cross-site requests,
    and the same token is returned in the body for header-based auth. Local /
    same-origin deployments fall back to ``SameSite=Lax``.
    """
    token = make_session(acc.id, acc.email)
    https = public_base_url().startswith("https")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=https,
        samesite="none" if https else "lax",
        path="/",
    )
    return token


# --- dependency -------------------------------------------------------------


def current_account(
    gitpilot_session: str | None = Cookie(default=None),
    x_gitpilot_session: str | None = Header(default=None),
) -> Account:
    """The signed‑in account, or 401.

    Accepts the session from the ``X-GitPilot-Session`` header (cross-origin
    SPAs) or the HttpOnly cookie (same-origin), in that order.
    """
    claims = read_session(x_gitpilot_session) or read_session(gitpilot_session)
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
        token = _issue_session(response, acc)
        return _public(acc, session_token=token)

    @router.post("/resend-verification")
    def resend_verification(payload: EmailRequest) -> dict[str, str]:
        """Re‑send the confirmation link for an unverified account.

        Generic response (no account enumeration): only actually emails when an
        account exists and is still unverified.
        """
        email = (payload.email or "").strip().lower()
        acc = store.get_by_email(email)
        if acc is not None and not acc.is_verified:
            send_verification_email(
                email, make_link_token(email, VERIFY_EMAIL, ttl_seconds=_LINK_TTL)
            )
        return {"message": "If your account needs confirmation, we've sent a new link."}

    @router.post("/login", response_model=AccountResponse)
    def login(payload: LoginRequest, response: Response) -> AccountResponse:
        email = (payload.email or "").strip().lower()
        acc = store.get_by_email(email)
        if acc is None or not verify_password(payload.password, acc.password_hash):
            # Identical message whether the email exists or the password is wrong.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
        if not acc.is_verified:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify your email before signing in.")
        token = _issue_session(response, acc)
        return _public(acc, session_token=token)

    @router.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"message": "Signed out."}

    @router.get("/me", response_model=AccountResponse)
    def me(acc: Account = Depends(current_account)) -> AccountResponse:  # noqa: B008 — FastAPI idiom
        return _public(acc)

    @router.patch("/profile", response_model=AccountResponse)
    def update_profile(
        payload: UpdateProfileRequest,
        acc: Account = Depends(current_account),  # noqa: B008 — FastAPI idiom
    ) -> AccountResponse:
        """Update editable account fields (currently the display name)."""
        new_name = (payload.name or "").strip() or None
        store.set_name(acc.email, new_name)
        updated = store.get_by_email(acc.email)
        return _public(updated or acc)

    @router.post("/password/change", response_model=AccountResponse)
    def password_change(
        payload: ChangePasswordRequest,
        response: Response,
        acc: Account = Depends(current_account),  # noqa: B008 — FastAPI idiom
    ) -> AccountResponse:
        """Change password for the signed‑in user (verifies the current one)."""
        if not verify_password(payload.current_password, acc.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")
        try:
            pw = hash_password(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        store.set_password(acc.email, pw)
        # Re-issue the session so the change refreshes the active token.
        token = _issue_session(response, acc)
        return _public(acc, session_token=token)

    @router.post("/delete")
    def delete_account(
        payload: DeleteAccountRequest,
        response: Response,
        acc: Account = Depends(current_account),  # noqa: B008 — FastAPI idiom
    ) -> dict[str, str]:
        """Permanently delete the signed‑in account.

        Password‑based accounts must confirm with their current password so a
        stolen/leftover session can't nuke the account.
        """
        if acc.password_hash and not verify_password(payload.password or "", acc.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password is incorrect.")
        store.delete_by_email(acc.email)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"message": "Your account has been permanently deleted."}

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
        token = _issue_session(response, acc)
        return _public(acc, session_token=token)

    return router


__all__ = ["build_account_router", "current_account"]
