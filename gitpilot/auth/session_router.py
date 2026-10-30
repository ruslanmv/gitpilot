"""Web session, provider connection and OAuth routes (``/api/auth/...``).

The browser never receives a GitHub or GitLab access token from these routes:
OAuth codes are exchanged server-side and the tokens go into the encrypted
session vault. The SPA learns *who* is connected from ``GET /api/auth/session``.

Routes
------
``GET  /api/auth/session``                      session status, providers, CSRF token
``GET  /api/auth/providers``                    which sign-in methods this server offers
``GET  /api/auth/oauth/{provider}/start``       begin OAuth (state + S256 PKCE)
``POST /api/auth/oauth/{provider}/callback``    finish OAuth, connect the provider
``POST /api/auth/session/import``               move a legacy browser-stored token server-side
``POST /api/auth/providers/{provider}/disconnect``  revoke and forget one provider
``POST /api/auth/logout``                       revoke the session and its credentials
``POST /api/auth/ws-ticket``                    single-use WebSocket ticket
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import oauth
from .web_session import (
    ProviderCredential,
    WebSession,
    clear_session_cookie,
    current_session,
    get_store,
    set_session_cookie,
)

logger = logging.getLogger("gitpilot.auth.session_router")

_PROVIDERS = ("github", "gitlab")


def _provider(name: str) -> str:
    name = (name or "").strip().lower()
    if name not in _PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider.")
    return name


def _scheme(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded or request.url.scheme


def frontend_origin(request: Request, explicit: Optional[str] = None) -> Optional[str]:
    """The allowed origin of the frontend that started an OAuth flow, or ``None``.

    Taken from the ``origin`` query parameter the SPA sends, else the
    ``Origin`` / ``Referer`` headers. The API's own origin (a frontend served by
    this backend) is always allowed; any other must be on the allowlist.
    """
    from .deployment import _origin_of, allowed_frontend_origin

    candidate = explicit or request.headers.get("origin") or request.headers.get("referer")
    normalized = _origin_of(candidate or "")
    if not normalized:
        return None
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").lower()
    if host and normalized.split("://", 1)[1] == host:
        return normalized
    return allowed_frontend_origin(normalized)


def accounts_enabled() -> bool:
    """Whether email/password accounts are mounted on this server."""
    return (os.getenv("GITPILOT_ENABLE_ACCOUNTS") or "").strip().lower() in ("1", "true", "yes", "on")


def _account_public(account_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not account_id or not accounts_enabled():
        return None
    try:
        from .accounts import get_account_store

        acc = get_account_store().get_by_id(account_id)
    except Exception:  # noqa: BLE001 - account store unavailable
        return None
    if acc is None:
        return None
    return {"id": acc.id, "email": acc.email, "name": acc.name, "email_verified": acc.is_verified}


def session_payload(sess: Optional[WebSession], *, session_token: Optional[str] = None) -> Dict[str, Any]:
    """Public description of a session. Never contains a provider token."""
    if sess is None:
        return {"authenticated": False, "account": None, "providers": {}, "csrf_token": None}
    account = _account_public(sess.account_id)
    providers = {name: cred.public() for name, cred in sess.credentials.items()}
    out: Dict[str, Any] = {
        "authenticated": bool(account or providers),
        "account": account,
        "providers": providers,
        "csrf_token": sess.csrf_token,
        "expires_at": sess.expires_at,
        "transport": sess.transport,
    }
    if session_token:
        out["session_token"] = session_token
    return out


def connect_credential(
    request: Request,
    response: Response,
    cred: ProviderCredential,
    *,
    bound_session_hash: Optional[str] = None,
    require_bound: bool = False,
) -> tuple[WebSession, str]:
    """Store ``cred`` on the request's session (rotated) or on a new session.

    ``bound_session_hash`` is the session an OAuth flow was started from. A
    callback that arrives with a different session is refused: that is what a
    login-CSRF (someone else's code + state replayed in your browser) looks like.
    """
    store = get_store()
    current = current_session()
    if bound_session_hash and (current is None or current.id_hash != bound_session_hash):
        raise HTTPException(status_code=400,
                            detail="This sign-in was started in another browser session. Please start again.")
    if require_bound and not bound_session_hash and current is not None:
        current = None
    if current is not None:
        raw = store.rotate(current)
        sess = current
    else:
        raw, sess = store.create()
    store.save_credential(sess, cred)
    set_session_cookie(response, raw, scheme=_scheme(request))
    return sess, raw


class OAuthCallbackBody(BaseModel):
    """Authorization response values forwarded by the SPA's /auth page."""

    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=16, max_length=512)


class ImportBody(BaseModel):
    """A token the browser stored before server-side sessions existed."""

    provider: str
    access_token: str = Field(min_length=8, max_length=4096)


async def complete_oauth(provider: str, code: str, state: str, request: Request,
                         response: Response) -> Dict[str, Any]:
    """Exchange the code, store the credential server-side, describe the session."""
    tokens, pending = await oauth.exchange(provider, code, state)
    profile = await oauth.fetch_profile(provider, tokens.access_token)
    cred = ProviderCredential(provider=provider, access_token=tokens.access_token,
                              refresh_token=tokens.refresh_token, expires_at=tokens.expires_at,
                              scope=tokens.scope, profile=profile)
    sess, raw = connect_credential(request, response, cred, bound_session_hash=pending.get("session_hash"),
                                   require_bound=True)
    payload = session_payload(sess, session_token=raw)
    payload.update({"provider": provider, "user": profile, "return_to": pending.get("return_to")})
    return payload


def build_session_router() -> APIRouter:
    """Routes for sessions, provider connections and OAuth."""
    router = APIRouter(tags=["auth"])

    @router.get("/api/auth/session")
    async def get_session(request: Request, response: Response) -> Dict[str, Any]:
        sess = current_session()
        if sess is None and accounts_enabled():
            migrated = _migrate_legacy_account_session(request, response)
            if migrated is not None:
                return migrated
        return session_payload(sess)

    @router.get("/api/auth/providers")
    async def providers() -> Dict[str, Any]:
        from ..github_oauth import get_oauth_config
        from ..gitlab_api import gitlab_host

        try:
            host = gitlab_host()
        except HTTPException:
            host = None
        return {
            "github": {"web": oauth.web_flow_available("github"),
                       "device": bool(get_oauth_config().client_id)},
            "gitlab": {"web": oauth.web_flow_available("gitlab"), "host": host},
            "accounts": accounts_enabled(),
        }

    @router.get("/api/auth/oauth/{provider}/start")
    async def oauth_start(
        provider: str,
        request: Request,
        return_to: Optional[str] = Query(None, max_length=300),
        origin: Optional[str] = Query(None, max_length=300),
    ) -> Dict[str, str]:
        return oauth.start(_provider(provider), current_session(), return_to=return_to,
                           frontend_origin=frontend_origin(request, origin))

    @router.post("/api/auth/oauth/{provider}/callback")
    async def oauth_callback(provider: str, body: OAuthCallbackBody, request: Request,
                             response: Response) -> Dict[str, Any]:
        return await complete_oauth(_provider(provider), body.code, body.state, request, response)

    @router.post("/api/auth/session/import")
    async def import_token(body: ImportBody, request: Request, response: Response) -> Dict[str, Any]:
        """One-time migration of a token a previous GitPilot version kept in localStorage.

        The token is validated against its provider before it is stored, and the
        SPA deletes its copy afterwards.
        """
        provider = _provider(body.provider)
        profile = await oauth.fetch_profile(provider, body.access_token.strip())
        cred = ProviderCredential(provider=provider, access_token=body.access_token.strip(), profile=profile)
        sess, raw = connect_credential(request, response, cred)
        payload = session_payload(sess, session_token=raw)
        payload.update({"provider": provider, "user": profile})
        return payload

    @router.post("/api/auth/providers/{provider}/disconnect")
    async def disconnect(provider: str) -> Dict[str, Any]:
        provider = _provider(provider)
        sess = current_session()
        if sess is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        cred = get_store().delete_credential(sess, provider)
        revoked = await oauth.revoke(provider, cred) if cred else False
        payload = session_payload(sess)
        payload["revoked"] = revoked
        return payload

    @router.post("/api/auth/logout")
    async def logout(request: Request, response: Response) -> Dict[str, Any]:
        sess = current_session()
        if sess is not None:
            for name, cred in list(sess.credentials.items()):
                await oauth.revoke(name, cred)
            get_store().revoke(sess)
        clear_session_cookie(response, scheme=_scheme(request))
        return {"authenticated": False}

    @router.post("/api/auth/ws-ticket")
    async def ws_ticket() -> Dict[str, str]:
        sess = current_session()
        if sess is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        return {"ticket": get_store().issue_ws_ticket(sess)}

    return router


def _migrate_legacy_account_session(request: Request, response: Response) -> Optional[Dict[str, Any]]:
    """Turn a pre-vault signed account token into a revocable server-side session."""
    from .security import SESSION_COOKIE as LEGACY_COOKIE
    from .security import read_session

    token = request.headers.get("x-gitpilot-session") or request.cookies.get(LEGACY_COOKIE)
    if not token or token.startswith("gps_"):
        return None
    claims = read_session(token)
    if not claims or not claims.get("uid"):
        return None
    if _account_public(claims["uid"]) is None:
        return None
    store = get_store()
    raw, sess = store.create(account_id=claims["uid"])
    set_session_cookie(response, raw, scheme=_scheme(request))
    return session_payload(sess, session_token=raw)


__all__ = ["build_session_router", "complete_oauth", "connect_credential", "session_payload",
           "accounts_enabled"]
