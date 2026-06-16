"""Transactional email via Resend (verification + password reset).

Resend is only the delivery provider, not the auth system. In dev (no
``RESEND_API_KEY``) this is a safe no-op that logs the link instead of sending,
so local sign-up flows work without a mail account.

Delivery is observable: every attempt records a redacted result (success id or
error message, recipient *domain* only) that ``email_config()`` exposes via the
``/api/account/email-health`` diagnostic endpoint — so "signup says 202 but no
email arrived" is debuggable without leaking secrets or full addresses.
"""

from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger("gitpilot.auth.email")

# Last delivery attempt (redacted) — surfaced by the email-health endpoint.
_last_lock = threading.Lock()
_last_send: dict[str, object] = {
    "attempted": False,
    "ok": None,
    "provider": None,
    "to_domain": None,
    "id": None,
    "error": None,
}


def email_from() -> str:
    return os.getenv("GITPILOT_EMAIL_FROM", "GitPilot <onboarding@resend.dev>")


def _domain_of(addr: str) -> str:
    m = re.search(r"@([^>\s]+)", addr or "")
    return (m.group(1) if m else "").strip().lower()


def public_base_url() -> str:
    return os.getenv("GITPILOT_PUBLIC_BASE_URL", "http://localhost:5173").rstrip("/")


def _record(**kw: object) -> None:
    with _last_lock:
        _last_send.update(kw)


def email_config() -> dict[str, object]:
    """Redacted email wiring report (no API key, no full recipient addresses)."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    frm = email_from()
    from_domain = _domain_of(frm)
    base = public_base_url()

    issues: list[str] = []
    if not api_key:
        issues.append(
            "RESEND_API_KEY is not set — verification emails are logged, not delivered. "
            "Set RESEND_API_KEY in the Space secrets."
        )
    if from_domain in ("resend.dev", ""):
        issues.append(
            "Sender uses the shared resend.dev domain — Resend only delivers to your own "
            "Resend account email in this mode. Verify a domain in Resend and set "
            "GITPILOT_EMAIL_FROM (e.g. 'GitPilot <noreply@ruslanmv.com>')."
        )
    if base.startswith(("http://localhost", "http://127.")):
        issues.append(
            "GITPILOT_PUBLIC_BASE_URL is a localhost default — confirmation links will be "
            "broken. Set it to the public site URL (e.g. https://ruslanmv-gitpilot.hf.space)."
        )

    with _last_lock:
        last = dict(_last_send)

    return {
        "provider": "resend" if api_key else "dev-noop",
        "from": frm,
        "from_domain": from_domain,
        "public_base_url": base,
        "ok": not issues,
        "issues": issues,
        "last_send": last,
    }


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email; returns True on success / dev no-op, False on failure.

    Records a redacted result for the email-health diagnostic either way.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    to_domain = _domain_of(to)

    if not api_key:
        logger.info("[dev email] to=%s subject=%s (no RESEND_API_KEY; not sent)", to, subject)
        _record(
            attempted=True, ok=True, provider="dev-noop",
            to_domain=to_domain, id=None, error=None,
        )
        return True

    try:
        import resend  # noqa: PLC0415 — lazy, optional dependency

        resend.api_key = api_key
        resp = resend.Emails.send(
            {"from": email_from(), "to": [to], "subject": subject, "html": html}
        )
        msg_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
        if not msg_id:
            err = str(resp)[:300]
            logger.error("Resend returned no message id for %s: %s", to, err)
            _record(
                attempted=True, ok=False, provider="resend",
                to_domain=to_domain, id=None, error=err,
            )
            return False
        logger.info("Resend accepted email to %s (id=%s)", to, msg_id)
        _record(
            attempted=True, ok=True, provider="resend",
            to_domain=to_domain, id=msg_id, error=None,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — never let email delivery break signup
        logger.exception("Resend send failed for %s", to)
        _record(
            attempted=True, ok=False, provider="resend",
            to_domain=to_domain, id=None, error=str(exc)[:300],
        )
        return False


def _link(path: str, token: str) -> str:
    return f"{public_base_url()}{path}?token={token}"


def send_verification_email(to: str, token: str) -> bool:
    link = _link("/auth/verify-email", token)
    html = (
        "<h2>Confirm your GitPilot email</h2>"
        "<p>Welcome to GitPilot. Confirm your email to activate your account:</p>"
        f'<p><a href="{link}">Confirm email</a></p>'
        "<p>This link expires in 15 minutes. If you didn't create an account, "
        "ignore this email.</p>"
    )
    return send_email(to, "Confirm your GitPilot email", html)


def send_reset_email(to: str, token: str) -> bool:
    link = _link("/auth/reset-password", token)
    html = (
        "<h2>Reset your GitPilot password</h2>"
        f'<p><a href="{link}">Choose a new password</a></p>'
        "<p>This link expires in 15 minutes. If you didn't request this, ignore this email.</p>"
    )
    return send_email(to, "Reset your GitPilot password", html)


__all__ = [
    "send_email",
    "send_verification_email",
    "send_reset_email",
    "public_base_url",
    "email_config",
]
