"""Standalone GitPilot HTTP repair API.

This is an *additive* FastAPI application (separate from the main ``gitpilot``
app) that exposes the generic GitPilot repair pipeline over HTTP so that
SelfRepair, matrix-maintainer, CI, or a developer can request a repair plan
to be turned into a (dry-run) patch preview.

Endpoints
---------
``GET  /health``  -> liveness + demo-mode + version.
``POST /repair``  -> accepts a repair-plan JSON (the :class:`RepairRequest`
                     shape), runs the repair pipeline, and returns the
                     :class:`RepairResponse` as a dict.

Safety
------
* Defaults to **dry-run**: a body with no ``mode`` is treated as ``dry_run``.
* **Never opens a real PR**: ``draft_pr`` is downgraded to ``dry_run`` unless
  ``GITPILOT_DRAFT_PR_ENABLED=true`` (defaults false).
* Inference reaches models only via OllaBridge (``OPENAI_BASE_URL`` /
  ``OPENAI_API_KEY``); ``GITPILOT_DEMO_MODE=true`` produces a deterministic
  offline stub patch. ``HF_TOKEN`` is never read here.
* Empty / violated ``allowed_paths`` fail closed (``status="blocked"``), never
  a 500.

Run
---
``uvicorn gitpilot.api.repair_server:app``
``python -m gitpilot.api.repair_server``  (host 0.0.0.0, port ``$PORT`` or 9000)
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

try:  # package version is best-effort; never fatal
    from gitpilot.version import __version__ as _PKG_VERSION
except Exception:  # pragma: no cover - defensive
    _PKG_VERSION = "0.1.0"

from gitpilot.repair.schema import RepairMode, RepairRequest
from gitpilot.repair.service import run_repair


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _demo_mode() -> bool:
    return _truthy(os.getenv("GITPILOT_DEMO_MODE"))


def _draft_pr_enabled() -> bool:
    """Real draft PRs are disabled by default (fail-safe)."""
    return _truthy(os.getenv("GITPILOT_DRAFT_PR_ENABLED"))


def create_app() -> FastAPI:
    """Build the GitPilot repair FastAPI application."""
    app = FastAPI(
        title="GitPilot Repair API",
        description="Generic GitPilot repair pipeline over HTTP (dry-run safe).",
        version=_PKG_VERSION or "0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "gitpilot",
            "version": _PKG_VERSION or "0.1.0",
            "demo_mode": _demo_mode(),
        }

    # Share the bearer-gated coder router (GET /repair/health + POST /repair).
    from gitpilot.repair_router import build_repair_router

    app.include_router(build_repair_router())
    return app


# Module-level app for ``uvicorn gitpilot.api.repair_server:app``.
app = create_app()


def main() -> None:
    """Run the repair server with uvicorn (``python -m ...``)."""
    import uvicorn

    port = int(os.getenv("PORT", "9000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover - manual/CLI entrypoint
    main()
