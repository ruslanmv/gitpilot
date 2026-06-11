"""MatrixLab sandbox HTTP client.

Implements :class:`~gitpilot.sandbox_providers.base.SandboxProvider` against
the MatrixLab contract:

* ``GET  {MATRIXLAB_URL}/health``
* ``POST {MATRIXLAB_URL}/repo/run``
* ``POST {MATRIXLAB_URL}/repo/validate-patch``

Body::

    {client_id, workspace_id, repo_url, branch, profile,
     commands?, timeout_seconds?, artifacts?}

Response::

    {run_id, status, exit_code, stdout, stderr, duration_ms,
     artifacts:[{name,url}]}

Reads ``MATRIXLAB_URL`` (default ``http://localhost:8765``) and
``MATRIXLAB_TOKEN``.  Degrades gracefully: :meth:`health` returns ``False``
when unreachable instead of raising.
"""

from __future__ import annotations

import os
from typing import Any

from .base import SandboxProvider, SandboxResult

DEFAULT_MATRIXLAB_URL = "http://localhost:8765"


class MatrixLabClient(SandboxProvider):
    name = "matrixlab"

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("MATRIXLAB_URL") or DEFAULT_MATRIXLAB_URL
        ).rstrip("/")
        self.token = token or os.getenv("MATRIXLAB_TOKEN") or ""
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _httpx(self):  # pragma: no cover - trivial lazy import
        import httpx

        return httpx

    # ------------------------------------------------------------------ #
    # SandboxProvider API
    # ------------------------------------------------------------------ #
    def health(self) -> bool:
        try:
            httpx = self._httpx()
        except Exception:
            return False
        try:
            resp = httpx.get(
                f"{self.base_url}/health", headers=self._headers(), timeout=5.0
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _post(self, path: str, payload: dict[str, Any]) -> SandboxResult:
        try:
            httpx = self._httpx()
        except Exception:
            return SandboxResult.skipped_result("httpx unavailable")
        try:
            resp = httpx.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # network / parse error
            return SandboxResult(
                status="error", reason=f"matrixlab request failed: {exc}"
            )
        if not isinstance(data, dict):
            return SandboxResult(status="error", reason="malformed matrixlab response")
        return SandboxResult.from_response(data)

    def run(self, **payload: Any) -> SandboxResult:
        return self._post("/repo/run", self._build_payload(payload))

    def validate_patch(self, **payload: Any) -> SandboxResult:
        return self._post("/repo/validate-patch", self._build_payload(payload))

    # ------------------------------------------------------------------ #
    # Payload helper
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_payload(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "client_id",
            "workspace_id",
            "repo_url",
            "branch",
            "profile",
            "commands",
            "timeout_seconds",
            "artifacts",
            "patch",
        )
        return {k: v for k, v in payload.items() if k in allowed and v is not None}
