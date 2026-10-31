# gitpilot/ollabridge_proxy.py
"""OllaBridge Cloud proxy endpoints for GitPilot.

Provides server-side proxy for OllaBridge Cloud device pairing
and model discovery, avoiding CORS issues when the frontend
calls remote OllaBridge instances.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .settings import update_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ollabridge", tags=["ollabridge"])


class PairRequest(BaseModel):
    base_url: str = ""
    code: str


class PairResponse(BaseModel):
    success: bool
    #: Only for single-user installs (the VS Code extension stores it); a
    #: multi-user deployment keeps the token on the web session and never
    #: returns it.
    token: str | None = None
    error: str | None = None
    #: ``"session"`` (this user's GitPilot session) or ``"server"`` (settings).
    stored: str | None = None
    base_url: str | None = None
    device_id: str | None = None


def _pair_base_url(raw: str) -> str:
    """Validate the OllaBridge server to pair with (defaults to OllaBridge Cloud)."""
    from urllib.parse import urlsplit

    from .auth.deployment import multi_user
    from .ollabridge_auth import OLLABRIDGE_CLOUD_URL, normalize_base_url

    base = normalize_base_url(raw) or OLLABRIDGE_CLOUD_URL
    parts = urlsplit(base)
    local = (parts.hostname or "") in ("localhost", "127.0.0.1", "::1")
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise HTTPException(status_code=400, detail="Enter the OllaBridge server URL, e.g. https://app.ollabridge.com")
    if multi_user() and (parts.scheme != "https" or local):
        # A shared server must not be steered at its own network.
        raise HTTPException(status_code=400, detail="Use the https:// address of your OllaBridge server.")
    return base


def _client_block() -> dict:
    """How GitPilot names itself in the user's OllaBridge device list."""
    from . import __version__

    return {"name": "GitPilot", "version": str(__version__)[:32], "platform": "web", "vendor": "GitPilot"}


@router.post("/pair", response_model=PairResponse)
async def proxy_pair(req: PairRequest):
    """Pair GitPilot with an OllaBridge account (device pairing).

    Exchanges the code shown on the OllaBridge pairing screen for a device
    token (``POST {base}/device/pair-simple``). Where the token goes:

    * multi-user deployments: encrypted on the signed-in user's web session,
      used only for that user's OllaBridge calls and only with this server;
      a caller without a session is refused *before* the single-use code is
      spent;
    * single-user installs: the server settings (``base_url`` + ``api_key``),
      and the token is returned for clients that keep their own copy.
    """
    import time

    from .auth.deployment import multi_user
    from .auth.web_session import ProviderCredential, current_session, get_store
    from .ollabridge_auth import VAULT_PROVIDER

    base = _pair_base_url(req.base_url)
    code = "".join(req.code.split()).upper()
    if not code:
        return PairResponse(success=False, error="Enter the pairing code shown by OllaBridge.")
    session = current_session()
    shared = multi_user()
    if shared and session is None:
        raise HTTPException(status_code=401, detail="Sign in to GitPilot before pairing your OllaBridge account.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base}/device/pair-simple",
                json={"code": code, "client": _client_block()},
                headers={"Content-Type": "application/json", "X-OllaBridge-Client": "GitPilot"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok" and data.get("device_token"):
                    token, device_id = data["device_token"], data.get("device_id")
                    if shared:
                        get_store().save_credential(session, ProviderCredential(
                            provider=VAULT_PROVIDER, access_token=token, scope="ollabridge",
                            profile={"base_url": base, "device_id": device_id, "paired_at": int(time.time())},
                        ))
                        return PairResponse(success=True, stored="session", base_url=base, device_id=device_id)
                    update_settings({"ollabridge": {"base_url": base, "api_key": token}})
                    return PairResponse(success=True, token=token, stored="server", base_url=base,
                                        device_id=device_id)
                # Endpoint returned an error in the response body
                return PairResponse(
                    success=False,
                    error=data.get("error") or "Pairing failed",
                )
            # Try to extract error message from non-200 responses
            try:
                err_data = resp.json()
                detail = err_data.get("detail") or err_data.get("error")
                if isinstance(detail, list):
                    err_msg = "; ".join(
                        e.get("msg", str(e)) if isinstance(e, dict) else str(e)
                        for e in detail
                    )
                elif detail:
                    err_msg = str(detail)
                else:
                    err_msg = f"OllaBridge returned HTTP {resp.status_code}"
            except Exception:
                # Response is not JSON (e.g. HTML error page from HF Spaces)
                body_preview = resp.text[:200] if resp.text else ""
                logger.warning(
                    "OllaBridge pair: HTTP %d, non-JSON body: %s",
                    resp.status_code,
                    body_preview,
                )
                if resp.status_code == 503:
                    err_msg = "OllaBridge is starting up. Please try again in a moment."
                elif resp.status_code >= 500:
                    err_msg = (
                        f"OllaBridge server error (HTTP {resp.status_code}). "
                        "The service may be restarting — try again shortly."
                    )
                elif resp.status_code == 422:
                    err_msg = "Invalid pairing request format."
                else:
                    err_msg = f"OllaBridge returned HTTP {resp.status_code}"
            return PairResponse(success=False, error=err_msg)
    except httpx.ConnectError:
        return PairResponse(success=False, error=f"Cannot reach {base}")
    except httpx.TimeoutException:
        return PairResponse(success=False, error="Connection timed out")
    except Exception as exc:
        logger.warning("OllaBridge pair proxy error: %s", exc)
        return PairResponse(success=False, error="Pairing failed. Please try again.")


@router.delete("/pair")
async def proxy_unpair() -> dict:
    """Forget the paired OllaBridge token (this user's, or the server's on a single-user install)."""
    from .auth.deployment import multi_user
    from .auth.web_session import current_session, get_store
    from .ollabridge_auth import VAULT_PROVIDER

    if multi_user():
        session = current_session()
        if session is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        get_store().delete_credential(session, VAULT_PROVIDER)
        return {"paired": False, "scope": "session"}
    update_settings({"ollabridge": {"api_key": ""}})
    return {"paired": False, "scope": "server"}


class ModelsResponse(BaseModel):
    models: list[str]
    error: str | None = None


class OllaBridgeNormalizedHealth(BaseModel):
    status: str  # "ok" or "error"
    base_url: str
    effective_api_base: str
    models_available: bool = False
    auth_mode: str = "unknown"
    warning: str | None = None


@router.get("/models", response_model=ModelsResponse)
async def proxy_models(base_url: str = "https://app.ollabridge.com", api_key: str = ""):
    """Proxy model listing request to an OllaBridge instance."""
    base = base_url.rstrip("/")
    try:
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    models = sorted({m.get("id", "") for m in data["data"] if m.get("id")})
                    return ModelsResponse(models=models)
                if isinstance(data, dict) and "models" in data:
                    models = sorted({
                        m.get("name", m.get("model", ""))
                        for m in data["models"]
                        if m.get("name") or m.get("model")
                    })
                    return ModelsResponse(models=models)
            return ModelsResponse(models=[], error=f"HTTP {resp.status_code}")
    except Exception as exc:
        return ModelsResponse(models=[], error=str(exc))


@router.get("/health")
async def proxy_health(base_url: str = "https://app.ollabridge.com"):
    """Check OllaBridge instance health."""
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/health")
            if resp.status_code == 200:
                return {"status": "ok", "url": base, "data": resp.json()}
            return {"status": "error", "url": base, "http_status": resp.status_code}
    except Exception as exc:
        return {"status": "error", "url": base, "error": str(exc)}


@router.get("/normalized-health")
async def ollabridge_normalized_health(
    base_url: str = Query(default="http://127.0.0.1:8000"),
    api_key: str | None = Query(default=None),
):
    """Normalized health check with machine-friendly fields for the redesigned UI."""
    effective_base = base_url.rstrip("/")
    warning = None
    if effective_base.endswith("/v1"):
        effective_base = effective_base[:-3]
        warning = "Do not include /v1; GitPilot adds it automatically."

    effective_api_base = f"{effective_base}/v1"

    auth_mode = "local"
    if api_key:
        auth_mode = "api_key"

    try:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{effective_api_base}/models", headers=headers)
            models_available = resp.status_code == 200
            status = "ok" if resp.status_code == 200 else "error"
    except Exception:
        status = "error"
        models_available = False

    return OllaBridgeNormalizedHealth(
        status=status,
        base_url=base_url,
        effective_api_base=effective_api_base,
        models_available=models_available,
        auth_mode=auth_mode,
        warning=warning,
    )
