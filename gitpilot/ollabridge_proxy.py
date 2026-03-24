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

    Forwards the pairing code to the OllaBridge /pair endpoint
    and returns the device token on success.
    """
    base = req.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base}/pair",
                json={"user_code": req.code},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return PairResponse(
                    success=True,
                    token=data.get("device_token") or data.get("token"),
                )
            # Try to extract error message
            try:
                err_data = resp.json()
                detail = err_data.get("detail") or err_data.get("error")
                # detail may be a list (Pydantic validation errors) or a string
                if isinstance(detail, list):
                    err_msg = "; ".join(
                        e.get("msg", str(e)) if isinstance(e, dict) else str(e)
                        for e in detail
                    )
                elif detail:
                    err_msg = str(detail)
                else:
                    err_msg = f"HTTP {resp.status_code}"
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
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
