"""GitPilot coder API router — mountable into the main app, bearer-token gated.

Exposes the GitPilot repair pipeline as an ``APIRouter`` so it can be included
into the main GitPilot FastAPI app (served on the Hugging Face Space) and reused
by the standalone ``repair_server``.

Security model
--------------
* **Bearer token:** when ``GITPILOT_API_TOKEN`` is set, ``POST /repair`` requires
  ``Authorization: Bearer <token>`` (401 otherwise). When it is *not* set the
  coder API is open — so always set the token on any public deployment (we do on
  the HF Space). Comparison is constant-time.
* **Encrypted in transit:** the HF Space terminates TLS, so the bearer token and
  all payloads travel over HTTPS.
* **Dry-run safe:** missing ``mode`` -> ``dry_run``; ``draft_pr`` is downgraded
  to ``dry_run`` unless ``GITPILOT_DRAFT_PR_ENABLED=true``. Never opens a real PR.
* **Coder demo:** ``GITPILOT_CODER_DEMO`` (default **true**) makes the coder API
  return deterministic offline previews independent of the main app. Set it to
  ``false`` (with ``OPENAI_BASE_URL``/``OPENAI_API_KEY`` for OllaBridge) for real
  model-generated patches. ``HF_TOKEN`` is never read here.
"""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gitpilot.repair.schema import RepairMode, RepairRequest
from gitpilot.repair.service import run_repair


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _api_token() -> str:
    return (os.getenv("GITPILOT_API_TOKEN") or "").strip()


def _coder_demo() -> bool:
    v = os.getenv("GITPILOT_CODER_DEMO")
    return _truthy(v) if v is not None else True  # default: deterministic preview


def _draft_pr_enabled() -> bool:
    return _truthy(os.getenv("GITPILOT_DRAFT_PR_ENABLED"))


def _version() -> str:
    try:
        from gitpilot.version import __version__ as v

        return v
    except Exception:  # pragma: no cover
        return "0.1.0"


def _auth_error(request: Request) -> JSONResponse | None:
    """Return a 401 JSONResponse if the bearer token is required and bad/missing.

    When no GITPILOT_API_TOKEN is configured the API is open (dev/local). Set the
    token on public deployments to enforce authentication.
    """
    token = _api_token()
    if not token:
        return None
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return JSONResponse(status_code=401, content={"detail": "missing bearer token"})
    presented = header.split(" ", 1)[1].strip()
    if not hmac.compare_digest(presented, token):
        return JSONResponse(status_code=401, content={"detail": "invalid token"})
    return None


def build_repair_router() -> APIRouter:
    """Build the coder API router (``GET /repair/health`` + ``POST /repair``)."""
    router = APIRouter(tags=["coder"])

    @router.get("/repair/health")
    def repair_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "gitpilot-coder",
            "version": _version(),
            "auth_required": bool(_api_token()),
            "coder_demo": _coder_demo(),
        }

    @router.post("/repair")
    async def repair(request: Request) -> JSONResponse:
        denied = _auth_error(request)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=422, content={"detail": "body must be valid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=422, content={"detail": "body must be a JSON object (repair plan)"}
            )
        plan = dict(body)
        if not plan.get("mode"):
            plan["mode"] = RepairMode.dry_run.value
        if str(plan["mode"]) == RepairMode.draft_pr.value and not _draft_pr_enabled():
            plan["mode"] = RepairMode.dry_run.value
        try:
            req = RepairRequest.from_json(plan)
        except Exception as exc:
            return JSONResponse(status_code=422, content={"detail": f"invalid repair plan: {exc}"})
        try:
            resp = run_repair(req, demo_mode=_coder_demo())
        except Exception:  # pragma: no cover - defensive; no secrets leaked
            return JSONResponse(
                status_code=500, content={"detail": "internal error running repair pipeline"}
            )
        return JSONResponse(status_code=200, content=resp.to_dict())

    return router
