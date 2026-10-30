"""ASGI middleware: resolve the web session, enforce CSRF, refresh provider tokens.

For every HTTP request and WebSocket upgrade it:

1. resolves the session from the ``X-GitPilot-Session`` header (cross-site SPA
   deployments), the HttpOnly ``gitpilot_session`` cookie, or — WebSocket only
   — a single-use ``?ticket=`` from ``POST /api/auth/ws-ticket``;
2. rejects a state-changing ``/api/`` request whose session arrived in the
   cookie unless it carries the session's ``X-GitPilot-CSRF`` token (a header
   other sites cannot set) or comes from a trusted origin — same-origin or an
   explicitly configured CORS origin, judged by ``Origin`` / ``Referer``
   (OWASP's standard-header check). A cross-site forgery has neither;
3. refreshes provider access tokens that are about to expire;
4. binds the session and the client's declared ``X-GitPilot-Git-Provider`` for
   the request, so token resolution never mixes providers.

It is a pure ASGI middleware, so the bound context reaches the endpoint and
streaming responses keep working.
"""
from __future__ import annotations

import json
import logging
import os
import re
from http.cookies import SimpleCookie
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import parse_qs

from .web_session import (
    CSRF_HEADER,
    PROVIDER_HEADER,
    SESSION_COOKIE,
    SESSION_HEADER,
    WebSession,
    bind,
    csrf_ok,
    get_store,
)

logger = logging.getLogger("gitpilot.auth.middleware")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Endpoints that establish identity from credentials the request itself
#: carries (password, emailed link, OAuth code + server-side state), so a
#: stale or missing CSRF token must not block them.
CSRF_EXEMPT = frozenset({
    "/api/account/login",
    "/api/account/signup",
    "/api/account/verify-email",
    "/api/account/resend-verification",
    "/api/account/password/forgot",
    "/api/account/password/reset",
})

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]


def _headers(scope: Scope) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = value.decode("latin-1")
    return out


def _cookie(headers: Dict[str, str], name: str) -> Optional[str]:
    raw = headers.get("cookie")
    if not raw:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # noqa: BLE001 - malformed cookie header
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


def _request_origin(headers: Dict[str, str]) -> Optional[str]:
    origin = (headers.get("origin") or "").strip()
    if origin and origin != "null":
        return origin
    referer = (headers.get("referer") or "").strip()
    match = re.match(r"^(https?://[^/?#]+)", referer)
    return match.group(1) if match else None


def _trusted_origin(origin: str, headers: Dict[str, str]) -> bool:
    """Same-origin, or listed explicitly in ``CORS_ORIGINS`` / ``CORS_ORIGIN_REGEX``.

    A ``*`` in ``CORS_ORIGINS`` is not trust: it only means "answer anyone".
    """
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").strip().lower()
    if host and origin.lower().split("://", 1)[-1] == host:
        return True
    allowed = [o.strip() for o in (os.getenv("CORS_ORIGINS") or "http://localhost:5173").split(",")
               if o.strip() and o.strip() != "*"]
    if origin in allowed:
        return True
    pattern = (os.getenv("CORS_ORIGIN_REGEX") or "").strip()
    return bool(pattern and re.fullmatch(pattern, origin))


def _origin_allowed(headers: Dict[str, str]) -> bool:
    """Whether a WebSocket upgrade's ``Origin`` may use the session cookie.

    Browsers attach cookies to cross-site WebSocket handshakes when the cookie
    is ``SameSite=None``, and CORS does not apply to WebSockets, so a hostile
    page could otherwise open an authenticated socket (cross-site WebSocket
    hijacking). Same-origin, the configured CORS origins/pattern, and non-browser
    clients (no ``Origin``) are allowed.
    """
    origin = (headers.get("origin") or "").strip()
    return not origin or _trusted_origin(origin, headers)


def _csrf_passes(session: WebSession, headers: Dict[str, str]) -> bool:
    if csrf_ok(session, headers.get(CSRF_HEADER)):
        return True
    origin = _request_origin(headers)
    return bool(origin) and _trusted_origin(origin, headers)


def _declared(headers: Dict[str, str]) -> Optional[str]:
    value = (headers.get(PROVIDER_HEADER) or "").strip().lower()
    return value if value in ("github", "gitlab") else None


class WebSessionMiddleware:
    """See module docstring."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        session = self._resolve(scope, headers)

        if session is not None and scope["type"] == "http":
            method = scope.get("method", "GET").upper()
            path = scope.get("path", "")
            if (
                method not in _SAFE_METHODS
                and session.transport == "cookie"
                and path.startswith("/api/")
                and path not in CSRF_EXEMPT
                and not _csrf_passes(session, headers)
            ):
                await self._reject(send, 403, "CSRF token missing or invalid. Reload the page and try again.")
                return

        if session is not None and session.credentials:
            try:
                from .oauth import ensure_fresh

                await ensure_fresh(session)
            except Exception:  # noqa: BLE001 - a refresh failure must not fail the request
                logger.warning("Provider token refresh failed", exc_info=True)

        with bind(session, _declared(headers)):
            await self.app(scope, receive, send)

    @staticmethod
    def _resolve(scope: Scope, headers: Dict[str, str]) -> Optional[WebSession]:
        try:
            store = get_store()
            header_value = headers.get(SESSION_HEADER)
            if header_value:
                found = store.resolve(header_value.strip(), transport="header")
                if found is not None:
                    return found
            cookie_value = _cookie(headers, SESSION_COOKIE)
            if cookie_value and scope["type"] == "websocket" and not _origin_allowed(headers):
                cookie_value = None  # cross-site socket: never ride the cookie
            if cookie_value:
                found = store.resolve(cookie_value, transport="cookie")
                if found is not None:
                    return found
            if scope["type"] == "websocket":
                query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
                ticket = (query.get("ticket") or [None])[0]
                if ticket:
                    return store.redeem_ws_ticket(ticket)
        except Exception:  # noqa: BLE001 - an unavailable store degrades to "no session"
            logger.exception("Web session lookup failed")
        return None

    @staticmethod
    async def _reject(send: Send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


__all__ = ["WebSessionMiddleware", "CSRF_EXEMPT"]
