"""OAuth 2.0 authorization-code flow with PKCE for GitHub and GitLab.

One implementation serves both providers:

* ``state`` is 256 bits of randomness, stored server-side (hashed) with its PKCE
  verifier, the redirect URI and the web session that started the flow. It is
  single-use and expires after ten minutes, and any worker can complete it.
* PKCE uses S256 for both providers (GitHub has supported it since 2025; GitLab
  for longer). The verifier never leaves the server.
* Tokens are exchanged server-side with the client secret and stored in the
  session vault (:mod:`gitpilot.auth.web_session`), never returned to the
  browser. Refresh tokens are kept and used before an access token expires
  (GitLab access tokens live two hours; GitHub App user tokens eight).

GitLab may be GitLab.com or one self-managed instance chosen by the operator
(``GITLAB_BASE_URL``); the host is never taken from a request.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .web_session import ProviderCredential, WebSession, WebSessionStore, get_store

logger = logging.getLogger("gitpilot.auth.oauth")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class OAuthProvider:
    """Static OAuth settings of one provider, resolved from the environment."""

    name: str
    client_id: str
    client_secret: Optional[str]
    authorize_url: str
    token_url: str
    scope: str
    redirect_uri: Optional[str]

    @property
    def web_flow(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _frontend_auth_url(explicit_env: str) -> Optional[str]:
    explicit = (os.getenv(explicit_env) or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = (os.getenv("GITPILOT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/auth" if base else None


def provider_config(name: str) -> OAuthProvider:
    """OAuth settings for ``github`` or ``gitlab``."""
    if name == "github":
        from ..github_oauth import get_oauth_config

        cfg = get_oauth_config()
        return OAuthProvider(
            name="github", client_id=cfg.client_id, client_secret=cfg.client_secret,
            authorize_url=GITHUB_AUTHORIZE_URL, token_url=GITHUB_TOKEN_URL,
            scope=os.getenv("GITHUB_OAUTH_SCOPES", "repo user:email"),
            redirect_uri=_frontend_auth_url("GITHUB_REDIRECT_URI"),
        )
    if name == "gitlab":
        from ..gitlab_api import gitlab_base_url

        base = gitlab_base_url()
        return OAuthProvider(
            name="gitlab",
            client_id=(os.getenv("GITLAB_CLIENT_ID") or "").strip(),
            client_secret=(os.getenv("GITLAB_CLIENT_SECRET") or "").strip() or None,
            authorize_url=f"{base}/oauth/authorize", token_url=f"{base}/oauth/token",
            scope=os.getenv("GITLAB_OAUTH_SCOPES", "api read_user"),
            redirect_uri=_frontend_auth_url("GITLAB_REDIRECT_URI"),
        )
    raise HTTPException(status_code=400, detail=f"Unknown provider: {name}")


def web_flow_available(name: str) -> bool:
    """Whether the browser OAuth flow is configured for ``name``."""
    try:
        cfg = provider_config(name)
    except HTTPException:
        return False
    if name == "gitlab":
        return cfg.web_flow and bool(cfg.redirect_uri)
    return cfg.web_flow


def _require_web_flow(name: str) -> OAuthProvider:
    cfg = provider_config(name)
    if not cfg.web_flow:
        label = "GitLab" if name == "gitlab" else "GitHub"
        raise HTTPException(status_code=503, detail=f"{label} sign-in is not configured on this server.")
    if name == "gitlab" and not cfg.redirect_uri:
        raise HTTPException(status_code=503,
                            detail="GitLab sign-in needs GITLAB_REDIRECT_URI or GITPILOT_PUBLIC_BASE_URL.")
    return cfg


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _safe_return_to(value: Optional[str]) -> Optional[str]:
    # Only same-app relative paths; never an absolute or protocol-relative URL.
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value[:300]


def start(name: str, session: Optional[WebSession], *, return_to: Optional[str] = None,
          store: Optional[WebSessionStore] = None) -> Dict[str, str]:
    """Begin an authorization: returns ``{authorization_url, state}``."""
    cfg = _require_web_flow(name)
    store = store or get_store()
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    store.put_oauth_state(provider=name, state=state, verifier=verifier, redirect_uri=cfg.redirect_uri,
                          session=session, return_to=_safe_return_to(return_to))
    params = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "scope": cfg.scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.redirect_uri:
        params["redirect_uri"] = cfg.redirect_uri
    if name == "github":
        params["allow_signup"] = "true"
    return {"authorization_url": f"{cfg.authorize_url}?{urlencode(params)}", "state": state}


@dataclass
class TokenSet:
    """Tokens returned by a provider's token endpoint."""

    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[float]
    scope: str


def _token_set(data: Dict[str, Any], *, previous_refresh: Optional[str] = None) -> TokenSet:
    access = data.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="The provider did not return an access token.")
    expires_in = data.get("expires_in")
    try:
        expires_at = time.time() + float(expires_in) if expires_in else None
    except (TypeError, ValueError):
        expires_at = None
    return TokenSet(access_token=access, refresh_token=data.get("refresh_token") or previous_refresh,
                    expires_at=expires_at, scope=str(data.get("scope") or ""))


async def _post_token(cfg: OAuthProvider, form: Dict[str, str]) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            resp = await client.post(cfg.token_url, data=form, headers={"Accept": "application/json"})
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="The sign-in provider is unreachable.") from exc
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {}
    data: Dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    # GitHub reports OAuth errors with HTTP 200 and an ``error`` field.
    if resp.status_code >= 400 or data.get("error"):
        logger.info("%s token endpoint refused the request: %s", cfg.name, data.get("error") or resp.status_code)
        raise HTTPException(status_code=400, detail="The authorization was not accepted. Please sign in again.")
    return data


async def exchange(name: str, code: str, state: str, *,
                   store: Optional[WebSessionStore] = None) -> tuple[TokenSet, Dict[str, Any]]:
    """Validate ``state`` (single use, unexpired, right provider) and exchange ``code``."""
    cfg = _require_web_flow(name)
    store = store or get_store()
    pending = store.take_oauth_state(name, state)
    if not pending:
        raise HTTPException(status_code=400,
                            detail="This sign-in link has expired or was already used. Please start again.")
    if not code or len(code) > 512:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    form = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret or "",
        "code": code,
        "grant_type": "authorization_code",
        "code_verifier": pending["verifier"],
    }
    if pending.get("redirect_uri"):
        form["redirect_uri"] = pending["redirect_uri"]
    return _token_set(await _post_token(cfg, form)), pending


async def refresh(name: str, refresh_token: str) -> TokenSet:
    """Use a refresh token (rotated by GitLab on every use)."""
    cfg = _require_web_flow(name)
    form = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret or "",
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if name == "gitlab" and cfg.redirect_uri:
        form["redirect_uri"] = cfg.redirect_uri
    return _token_set(await _post_token(cfg, form), previous_refresh=refresh_token)


async def revoke(name: str, cred: ProviderCredential) -> bool:
    """Best-effort revocation at the provider (on disconnect / logout)."""
    try:
        cfg = provider_config(name)
    except HTTPException:
        return False
    if not cfg.web_flow:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            if name == "gitlab":
                resp = await client.post(cfg.token_url.replace("/oauth/token", "/oauth/revoke"), data={
                    "client_id": cfg.client_id, "client_secret": cfg.client_secret or "",
                    "token": cred.access_token,
                })
            else:
                resp = await client.request(
                    "DELETE", f"{GITHUB_API}/applications/{cfg.client_id}/token",
                    auth=(cfg.client_id, cfg.client_secret or ""),
                    json={"access_token": cred.access_token},
                    headers={"Accept": "application/vnd.github+json"},
                )
        return resp.status_code < 400
    except httpx.RequestError:
        return False


async def fetch_profile(name: str, access_token: str) -> Dict[str, Any]:
    """The signed-in user's identity, normalised for the UI."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            if name == "gitlab":
                from ..gitlab_api import gitlab_api_url

                resp = await client.get(f"{gitlab_api_url()}/user",
                                        headers={"Authorization": f"Bearer {access_token}"})
            else:
                resp = await client.get(f"{GITHUB_API}/user", headers={
                    "Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                })
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="The provider is unreachable.") from exc
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="The provider did not accept this credential.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not read the provider profile.")
    raw: Dict[str, Any] = resp.json()
    if name == "gitlab":
        from ..gitlab_api import gitlab_host

        return {"id": raw.get("id"), "login": raw.get("username"), "name": raw.get("name"),
                "email": raw.get("email"), "avatar_url": raw.get("avatar_url") or "",
                "html_url": raw.get("web_url") or "", "provider": "gitlab", "host": gitlab_host()}
    return {"id": raw.get("id"), "login": raw.get("login"), "name": raw.get("name"),
            "email": raw.get("email"), "avatar_url": raw.get("avatar_url") or "",
            "html_url": raw.get("html_url") or "", "provider": "github", "host": "github.com"}


_refresh_locks: Dict[str, asyncio.Lock] = {}


async def ensure_fresh(session: WebSession, *, store: Optional[WebSessionStore] = None) -> None:
    """Refresh any of the session's credentials that is about to expire.

    A credential that cannot be refreshed is removed, so the UI asks the user to
    reconnect instead of every call failing with 401.
    """
    store = store or get_store()
    for name, cred in list(session.credentials.items()):
        if not cred.expiring():
            continue
        lock = _refresh_locks.setdefault(f"{session.id_hash}:{name}", asyncio.Lock())
        async with lock:
            current = store.reload_credential(session, name)
            if current is None or not current.expiring():
                continue  # another request (or worker) already refreshed it
            if not current.refresh_token:
                if current.expires_at and time.time() >= current.expires_at:
                    store.delete_credential(session, name)
                continue
            try:
                tokens = await refresh(name, current.refresh_token)
            except HTTPException:
                again = store.reload_credential(session, name)
                if again is None or again.expiring(margin=0):
                    logger.info("%s credential could not be refreshed; the user must reconnect.", name)
                    store.delete_credential(session, name)
                continue
            store.save_credential(session, ProviderCredential(
                provider=name, access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at, scope=tokens.scope or current.scope, profile=current.profile,
            ))


__all__ = [
    "OAuthProvider", "TokenSet", "provider_config", "web_flow_available", "pkce_pair", "start",
    "exchange", "refresh", "revoke", "fetch_profile", "ensure_fresh",
]
