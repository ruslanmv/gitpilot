"""Where GitPilot is deployed, for the auth layer: public URL, trusted origins, storage.

Explicit configuration always wins. When it is absent, a Hugging Face Space is
recognised from the variables the Spaces runtime sets for every container
(``SPACE_ID``, ``SPACE_HOST`` — e.g. ``ruslanmv-gitpilot.hf.space``), so a Space
gets working OAuth redirect URIs, email links, ``Secure`` cookies and CSRF
origin checks with only its OAuth client secrets configured:

* public base URL: ``GITPILOT_PUBLIC_BASE_URL``, else ``https://$SPACE_HOST``;
* auth storage: ``GITPILOT_AUTH_DIR``, else the Space's persistent ``/data``
  volume when it is attached and writable, else ``<config dir>/auth``. Without
  persistent storage a Space restart starts with no sessions (users sign in
  again); stored tokens survive restarts only with ``/data`` or an external
  ``GITPILOT_AUTH_DB``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional


def space_host() -> Optional[str]:
    """The Space's public host name (``owner-name.hf.space``), if running on Spaces."""
    host = (os.getenv("SPACE_HOST") or "").strip().strip("/")
    if not host:
        return None
    return host.split("://", 1)[-1]


def on_hf_space() -> bool:
    """True inside a Hugging Face Spaces container."""
    return bool(os.getenv("SPACE_ID") or space_host())


def multi_user() -> bool:
    """True when several people share this server (hosted deployments).

    A Hugging Face Space, enabled accounts, or ``GITPILOT_MULTI_USER=true``.
    Per-person credentials then belong on the person's web session, never in
    the server-wide ``settings.json``.
    """
    def _on(name: str) -> bool:
        return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")

    return on_hf_space() or _on("GITPILOT_ENABLE_ACCOUNTS") or _on("GITPILOT_MULTI_USER")


def public_base_url(default: Optional[str] = None) -> Optional[str]:
    """Public URL of the web app (no trailing slash)."""
    explicit = (os.getenv("GITPILOT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = space_host()
    if host:
        return f"https://{host}"
    return default


def trusted_public_origins() -> List[str]:
    """Origins of this deployment's own web app (for CSRF / WebSocket origin checks)."""
    origins: List[str] = []
    base = public_base_url()
    if base:
        scheme, _, rest = base.partition("://")
        origins.append(f"{scheme}://{rest.split('/', 1)[0]}")
    host = space_host()
    if host:
        origins.append(f"https://{host}")
    return origins


def _origin_of(url: str) -> Optional[str]:
    scheme, sep, rest = (url or "").strip().partition("://")
    if not sep or scheme not in ("http", "https") or not rest:
        return None
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not host or "@" in host:
        return None
    return f"{scheme}://{host}".lower()


def frontend_origins() -> List[str]:
    """Frontends allowed to receive OAuth redirects (``<origin>/auth``).

    One backend can serve several frontends — e.g. the production site on Vercel
    and the Space's own UI — and each must get the provider redirect back on its
    own origin, where the tab's pending sign-in state lives. Allowed: this
    deployment's public origin(s), explicit ``CORS_ORIGINS`` entries (never
    ``*``), ``GITPILOT_OAUTH_REDIRECT_ORIGINS`` and the origins of
    ``GITHUB_REDIRECT_URI`` / ``GITLAB_REDIRECT_URI``.
    """
    candidates = list(trusted_public_origins())
    candidates += [o for o in (os.getenv("CORS_ORIGINS") or "").split(",") if o.strip() != "*"]
    candidates += (os.getenv("GITPILOT_OAUTH_REDIRECT_ORIGINS") or "").split(",")
    candidates += [os.getenv("GITHUB_REDIRECT_URI") or "", os.getenv("GITLAB_REDIRECT_URI") or ""]
    out: List[str] = []
    for item in candidates:
        origin = _origin_of(item.strip())
        if origin and origin not in out:
            out.append(origin)
    return out


def allowed_frontend_origin(origin: Optional[str]) -> Optional[str]:
    """``origin`` normalised, if it may receive an OAuth redirect; else ``None``."""
    normalized = _origin_of(origin or "")
    if not normalized:
        return None
    if normalized in frontend_origins():
        return normalized
    pattern = (os.getenv("CORS_ORIGIN_REGEX") or "").strip()
    if pattern and re.fullmatch(pattern, normalized):
        return normalized
    return None


def persistent_auth_dir() -> Optional[Path]:
    """Directory for sessions and the vault key that survives restarts, if known."""
    explicit = (os.getenv("GITPILOT_AUTH_DIR") or "").strip()
    if explicit:
        return Path(explicit)
    data = Path("/data")
    if on_hf_space() and data.is_dir() and os.access(data, os.W_OK):
        return data / "gitpilot" / "auth"
    return None


__all__ = ["space_host", "on_hf_space", "multi_user", "public_base_url", "trusted_public_origins", "frontend_origins",
           "allowed_frontend_origin", "persistent_auth_dir"]
