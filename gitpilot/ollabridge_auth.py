"""Which OllaBridge server and token a request uses.

OllaBridge Cloud authenticates every ``/v1/*`` call with the device token that
device pairing issues (``Authorization: Bearer <token>``); the token belongs to
one OllaBridge account.

* **Multi-user deployments** (a Hugging Face Space, accounts enabled,
  ``GITPILOT_MULTI_USER``): each signed-in user pairs their own account. The
  token is stored encrypted on that user's GitPilot web session (the same vault
  as GitHub/GitLab tokens, provider ``"ollabridge"``) together with the server
  it was issued by, and is only ever sent to that server. It is never written
  to the shared ``settings.json`` and never returned to the browser.
* **Single-user installs**: pairing configures the server itself
  (``settings.ollabridge.base_url`` / ``api_key``), as before.

:func:`resolve` is the one place that answers "which base URL and token" for
model calls, model listing and health checks.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

#: The OllaBridge Cloud server its pairing screen tells users to use.
OLLABRIDGE_CLOUD_URL = "https://app.ollabridge.com"

#: Provider name of the paired token in the web-session vault.
VAULT_PROVIDER = "ollabridge"


def normalize_base_url(url: Optional[str]) -> str:
    """``https://host[:port][/path]`` without a trailing slash or ``/v1``."""
    base = (url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def session_pairing() -> Optional[Any]:
    """The current web session's paired OllaBridge credential, if any."""
    try:
        from .auth.web_session import current_session
    except Exception:  # pragma: no cover - auth package unavailable
        return None
    session = current_session()
    if session is None:
        return None
    return session.credentials.get(VAULT_PROVIDER)


def resolve(settings: Any) -> Tuple[str, str]:
    """``(base_url, api_key)`` for OllaBridge calls made for this request.

    The signed-in user's own pairing wins, with the server that issued it;
    otherwise the server settings, then ``OLLABRIDGE_BASE_URL`` /
    ``OLLABRIDGE_API_KEY``.
    """
    paired = session_pairing()
    if paired is not None and paired.access_token:
        base = normalize_base_url((paired.profile or {}).get("base_url")) or OLLABRIDGE_CLOUD_URL
        return base, paired.access_token

    from .settings import OLLABRIDGE_DEFAULT_BASE_URL

    ob = getattr(settings, "ollabridge", None)
    base = (getattr(ob, "base_url", None) or os.getenv("OLLABRIDGE_BASE_URL") or OLLABRIDGE_DEFAULT_BASE_URL)
    api_key = getattr(ob, "api_key", None) or os.getenv("OLLABRIDGE_API_KEY", "")
    return normalize_base_url(base), api_key


def pairing_status(settings: Any) -> Optional[Dict[str, Any]]:
    """What the settings page shows about pairing (never the token)."""
    paired = session_pairing()
    if paired is not None and paired.access_token:
        profile = paired.profile or {}
        return {"scope": "session", "base_url": profile.get("base_url"),
                "device_id": profile.get("device_id"), "paired_at": profile.get("paired_at")}
    ob = getattr(settings, "ollabridge", None)
    if getattr(ob, "api_key", None):
        return {"scope": "server", "base_url": normalize_base_url(getattr(ob, "base_url", None)) or None}
    return None


__all__ = ["OLLABRIDGE_CLOUD_URL", "VAULT_PROVIDER", "normalize_base_url", "session_pairing", "resolve",
           "pairing_status"]
