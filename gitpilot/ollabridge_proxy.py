# gitpilot/ollabridge_proxy.py
"""OllaBridge Cloud proxy endpoints for GitPilot.

Provides server-side proxy for OllaBridge Cloud device pairing
and model discovery, avoiding CORS issues when the frontend
calls remote OllaBridge instances.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ollabridge", tags=["ollabridge"])


class PairRequest(BaseModel):
    base_url: str
    code: str


class PairResponse(BaseModel):
    success: bool
    token: str | None = None
    error: str | None = None


@router.post("/pair", response_model=PairResponse)
async def proxy_pair(req: PairRequest):
    """Proxy device pairing request to OllaBridge Cloud.

    Forwards the pairing code to the OllaBridge /device/pair-simple endpoint
    and returns the device token on success.
    """
    base = req.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base}/device/pair-simple",
                json={"code": req.code},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    return PairResponse(
                        success=True,
                        token=data.get("device_token"),
                    )
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
        return PairResponse(success=False, error=str(exc))


class ModelsResponse(BaseModel):
    models: list[str]
    error: str | None = None


@router.get("/models", response_model=ModelsResponse)
async def proxy_models(base_url: str = "https://ruslanmv-ollabridge.hf.space", api_key: str = ""):
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
async def proxy_health(base_url: str = "https://ruslanmv-ollabridge.hf.space"):
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
