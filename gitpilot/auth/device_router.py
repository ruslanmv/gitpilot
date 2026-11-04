"""GitHub device authorization endpoints.

Keep transport/protocol failures distinct from rejected device codes and from
session-vault failures. The browser can retry an upstream outage, but it must
not keep polling when GitHub denied the device code or storage is unavailable.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..github_oauth import DevicePollSlowDown, initiate_device_flow, poll_device_token

logger = logging.getLogger(__name__)


def _github_unavailable() -> JSONResponse:
    return JSONResponse(
        {"error": "GitHub authorization is temporarily unavailable.", "code": "github_unavailable"},
        status_code=503,
        headers={"Retry-After": "5"},
    )


def build_device_router() -> APIRouter:
    """Register GitHub device-flow routes (also used by CLI and VS Code)."""
    router = APIRouter(tags=["auth"])

    @router.post("/api/auth/device/code")
    async def device_code():
        try:
            return await initiate_device_flow()
        except httpx.HTTPStatusError as exc:
            logger.warning("GitHub device-code request returned HTTP %s", exc.response.status_code)
            if exc.response.status_code in (400, 401, 403):
                return JSONResponse(
                    {"error": "GitHub rejected the device flow. Check GITHUB_CLIENT_ID and "
                              "enable Device Flow in the GitHub App settings."},
                    status_code=400,
                )
            return _github_unavailable()
        except httpx.HTTPError:
            logger.warning("GitHub device-code request failed", exc_info=True)
            return _github_unavailable()

    @router.post("/api/auth/device/poll")
    async def device_poll(payload: dict, request: Request, response: Response):
        """Poll GitHub; a web client with store=true never receives the token."""
        code = payload.get("device_code")
        if not isinstance(code, str) or not code.strip():
            return JSONResponse({"error": "Missing device_code"}, status_code=400)

        try:
            authorized = await poll_device_token(code)
        except DevicePollSlowDown as exc:
            body = {"error": "slow_down"}
            if exc.interval is not None:
                body["interval"] = exc.interval
            return JSONResponse(body, status_code=429)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.HTTPError:
            logger.warning("GitHub device-token poll failed", exc_info=True)
            return _github_unavailable()

        if authorized is None:
            return JSONResponse({"status": "pending"}, status_code=202)
        if not payload.get("store"):
            return authorized

        try:
            from .session_router import connect_credential, session_payload
            from .web_session import ProviderCredential

            profile = {**authorized.user.model_dump(), "provider": "github", "host": "github.com"}
            cred = ProviderCredential(
                provider="github", access_token=authorized.access_token,
                scope=authorized.scope, profile=profile,
            )
            sess, raw = connect_credential(request, response, cred)
            result = session_payload(sess, session_token=raw)
            result.update({"provider": "github", "user": profile})
            return result
        except Exception:
            logger.exception("GitHub device authorization succeeded but the session could not be saved")
            return JSONResponse(
                {"error": "GitHub authorized the connection, but the server could not save "
                          "your sign-in. Please try again or contact the administrator.",
                 "code": "session_storage_unavailable"},
                status_code=503,
            )

    return router
