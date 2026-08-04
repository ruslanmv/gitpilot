"""Which MCP Context Forge GitPilot talks to, and how it authenticates.

Until now the gateway address lived in environment variables, which meant
pointing GitPilot at a different Forge — a colleague's, a staging one, the one
HomePilot already started — required editing a file and restarting. This module
makes the connection a first-class, editable setting that the desktop UI, the
mobile UI and the VS Code extension all read and write through one API.

Three rules it is built around:

1. **A secret that has been saved is never handed back.** Reads return
   ``has_password`` / ``has_api_token`` booleans, never the values. A settings
   screen that can display a password is a settings screen that can leak one.

2. **Empty means "leave it alone", not "clear it".** Saving a form whose
   password field renders blank must not wipe the stored password. Clearing is
   an explicit act (``clear_password: true``), so it can never happen by
   accident.

3. **The credential is proved, not trusted.** "Test connection" performs the
   real probe against the real gateway and reports what it found, so a
   misconfiguration is caught in the settings screen rather than in the middle
   of a coding run.

Resolution order for every field: the saved profile, then the environment, then
the built-in default. Configuring from the UI therefore overrides a deployment's
env vars, and clearing a field falls back to them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILE_ENV = "GITPILOT_MCP_GATEWAY_FILE"
DEFAULT_CONFIG_FILE = Path.home() / ".gitpilot" / "mcp_gateway.json"

ENV_URL = "GITPILOT_MCP_FORGE_URL"
ENV_TOKEN = "GITPILOT_MCP_FORGE_TOKEN"  # noqa: S105 - env var name, not a secret
ENV_EMAIL = "GITPILOT_MCP_FORGE_EMAIL"
ENV_PASSWORD = "GITPILOT_MCP_FORGE_PASSWORD"  # noqa: S105 - env var name, not a secret

DEFAULT_URL = "http://localhost:4444"

#: auto  — probe the gateway and use whatever it accepts (the sane default)
#: none  — send no credential (a gateway with AUTH_REQUIRED=false)
#: token — send the stored API token
#: login — exchange email + password for a JWT
AUTH_MODES = ("auto", "none", "token", "login")


@dataclass
class GatewayProfile:
    """One saved connection. Secrets live here; they never leave this process."""

    url: str = ""
    auth_mode: str = "auto"
    email: str = ""
    password: str = ""
    api_token: str = ""
    #: Free text so a user with several gateways can tell them apart.
    label: str = ""

    def resolved_url(self) -> str:
        return (self.url or os.environ.get(ENV_URL) or DEFAULT_URL).rstrip("/")

    def resolved_email(self) -> str:
        return self.email or os.environ.get(ENV_EMAIL, "")

    def resolved_password(self) -> str:
        return self.password or os.environ.get(ENV_PASSWORD, "")

    def resolved_api_token(self) -> str:
        return self.api_token or os.environ.get(ENV_TOKEN, "")

    def source_of_url(self) -> str:
        if self.url:
            return "settings"
        if os.environ.get(ENV_URL):
            return "environment"
        return "default"

    def public_dict(self) -> dict[str, Any]:
        """Everything a UI may see. Deliberately excludes every secret."""
        return {
            "url": self.resolved_url(),
            "savedUrl": self.url,
            "urlSource": self.source_of_url(),
            "authMode": self.auth_mode,
            "email": self.resolved_email(),
            "label": self.label,
            "hasPassword": bool(self.resolved_password()),
            "hasApiToken": bool(self.resolved_api_token()),
            # True when the value comes from the environment, so the UI can say
            # "set by this deployment" instead of offering to clear it.
            "passwordFromEnv": not self.password and bool(os.environ.get(ENV_PASSWORD)),
            "apiTokenFromEnv": not self.api_token and bool(os.environ.get(ENV_TOKEN)),
        }


@dataclass
class GatewayUpdate:
    """A partial edit. ``None`` means "not submitted"; empty string means "".

    The distinction matters: a settings form always posts a blank password
    field, and that must not erase a stored one.
    """

    url: str | None = None
    auth_mode: str | None = None
    email: str | None = None
    password: str | None = None
    api_token: str | None = None
    label: str | None = None
    clear_password: bool = False
    clear_api_token: bool = False


def config_path() -> Path:
    override = os.environ.get(CONFIG_FILE_ENV)
    return Path(override) if override else DEFAULT_CONFIG_FILE


def load_profile(path: Path | None = None) -> GatewayProfile:
    target = path or config_path()
    if not target.exists():
        return GatewayProfile()
    try:
        with target.open(encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # A corrupt file must not brick the settings screen; defaults still work.
        logger.warning("invalid MCP gateway config %s, ignoring (%s)", target, exc)
        return GatewayProfile()
    mode = str(blob.get("auth_mode") or "auto")
    return GatewayProfile(
        url=str(blob.get("url") or ""),
        auth_mode=mode if mode in AUTH_MODES else "auto",
        email=str(blob.get("email") or ""),
        password=str(blob.get("password") or ""),
        api_token=str(blob.get("api_token") or ""),
        label=str(blob.get("label") or ""),
    )


def save_profile(profile: GatewayProfile, path: Path | None = None) -> None:
    """Persist atomically, owner-only: this file holds live credentials."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    previous_umask = os.umask(0o077)
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(asdict(profile), fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        tmp.replace(target)
    finally:
        os.umask(previous_umask)


def apply_update(profile: GatewayProfile, update: GatewayUpdate) -> GatewayProfile:
    """Fold a partial edit into a profile, honouring the empty-vs-clear rule."""
    if update.auth_mode is not None and update.auth_mode not in AUTH_MODES:
        raise ValueError(
            f"unknown auth mode {update.auth_mode!r}; expected one of {', '.join(AUTH_MODES)}"
        )

    updated = GatewayProfile(**asdict(profile))
    if update.url is not None:
        updated.url = update.url.strip().rstrip("/")
    if update.auth_mode is not None:
        updated.auth_mode = update.auth_mode
    if update.email is not None:
        updated.email = update.email.strip()
    if update.label is not None:
        updated.label = update.label.strip()

    # A submitted, non-empty secret replaces; a blank one leaves the stored
    # value untouched; clearing is explicit.
    if update.password:
        updated.password = update.password
    if update.clear_password:
        updated.password = ""
    if update.api_token:
        updated.api_token = update.api_token
    if update.clear_api_token:
        updated.api_token = ""
    return updated


# ── connection test ─────────────────────────────────────────────────────────

@dataclass
class GatewayTestResult:
    """What the gateway actually said, in terms a settings screen can render."""

    ok: bool = False
    url: str = ""
    reachable: bool = False
    auth_required: bool | None = None
    authenticated: bool = False
    auth_mode_used: str = ""
    #: One sentence naming the next action when something is wrong.
    detail: str = ""
    server_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "url": self.url,
            "reachable": self.reachable,
            "authRequired": self.auth_required,
            "authenticated": self.authenticated,
            "authModeUsed": self.auth_mode_used,
            "detail": self.detail,
            "serverCount": self.server_count,
            "warnings": self.warnings,
        }


async def probe_gateway(  # noqa: PLR0911 - one return per distinct outcome the UI reports
    profile: GatewayProfile, timeout: float = 8.0
) -> GatewayTestResult:
    """Probe a gateway the way the registration path will, and report back.

    Deliberately mirrors ``scripts/lib/forge-auth.sh``: reachability first, then
    what the gateway accepts, then whether our credential is one of those
    things. A credential that is merely *configured* is never reported as
    working.
    """
    import httpx

    url = profile.resolved_url()
    result = GatewayTestResult(url=url)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            health = await client.get(f"{url}/health")
        except Exception as exc:
            result.detail = f"Could not reach {url}: {exc}"
            return result
        if health.status_code != 200:
            result.detail = f"{url}/health returned HTTP {health.status_code}."
            return result
        result.reachable = True

        # What does this gateway want? Ask it, unauthenticated.
        try:
            anon = await client.get(f"{url}/gateways", headers={"Accept": "application/json"})
        except Exception as exc:
            result.detail = f"Reachable, but /gateways failed: {exc}"
            return result

        if anon.status_code == 200:
            result.auth_required = False
            if profile.auth_mode in {"none", "auto"}:
                result.ok = True
                result.authenticated = True
                result.auth_mode_used = "none"
                result.detail = "Connected. This gateway has authentication disabled."
                result.server_count = _count(anon)
                return result
            # The user asked for a credential on a gateway that needs none.
            result.warnings.append(
                "This gateway accepts anonymous access; "
                "the credential you configured is not needed."
            )

        result.auth_required = (
            anon.status_code in (401, 403, 302) or result.auth_required is not False
        )

        token, mode, problem = await _resolve_token(client, profile, url)
        if not token:
            result.auth_mode_used = mode
            result.detail = problem or "This gateway needs a credential and none is configured."
            return result

        try:
            check = await client.get(
                f"{url}/gateways",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except Exception as exc:
            result.detail = f"Credential obtained, but the gateway did not answer: {exc}"
            return result

        if check.status_code == 200:
            result.ok = True
            result.authenticated = True
            result.auth_mode_used = mode
            result.server_count = _count(check)
            result.detail = f"Connected and authenticated ({mode})."
            return result

        result.auth_mode_used = mode
        result.detail = (
            f"The gateway rejected the credential (HTTP {check.status_code}). "
            "Note that a JWT signing secret is not a bearer token."
        )
        return result


async def _resolve_token(client: Any, profile: GatewayProfile, url: str) -> tuple[str, str, str]:
    """(token, mode, problem). Tries the API token, then an email login."""
    api_token = profile.resolved_api_token()
    if profile.auth_mode in ("auto", "token") and api_token:
        return api_token, "token", ""

    email = profile.resolved_email()
    password = profile.resolved_password()
    if profile.auth_mode in ("auto", "login") and email and password:
        try:
            response = await client.post(
                f"{url}/auth/email/login",
                json={"email": email, "password": password},
            )
        except Exception as exc:
            return "", "login", f"Sign-in to {url} failed: {exc}"
        if response.status_code != 200:
            return "", "login", (
                f"Sign-in was rejected (HTTP {response.status_code}). "
                "Check the admin email and password."
            )
        token = str((response.json() or {}).get("access_token") or "")
        if not token:
            return "", "login", "Sign-in succeeded but returned no access token."
        return token, "login", ""

    if profile.auth_mode == "token":
        return "", "token", "No API token is configured for this gateway."
    if profile.auth_mode == "login":
        return "", "login", "No admin email and password are configured for this gateway."
    return "", "auto", (
        "This gateway needs a credential: add an API token, or an admin email and password."
    )


def _count(response: Any) -> int | None:
    try:
        payload = response.json()
    except Exception:
        return None
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "gateways", "servers"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None
