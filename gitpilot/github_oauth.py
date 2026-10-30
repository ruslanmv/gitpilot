# gitpilot/github_oauth.py

"""GitHub OAuth 2.0 authentication flow implementation (Web + Device Flow)."""
from __future__ import annotations

import logging
import os
from typing import Optional, Dict, Any

import httpx
from pydantic import BaseModel

# Configure logging
logger = logging.getLogger("gitpilot.auth")

class OAuthConfig(BaseModel):
    """GitHub OAuth App configuration."""
    client_id: str
    # Secret is now optional to allow Device Flow
    client_secret: Optional[str] = None

class OAuthState(BaseModel):
    """OAuth state management."""
    state: str
    code_verifier: str
    timestamp: float

class GitHubUser(BaseModel):
    """GitHub user information."""
    login: str
    id: int
    avatar_url: str
    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    html_url: Optional[str] = None

class AuthSession(BaseModel):
    """Authenticated user session."""
    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    user: GitHubUser



def get_oauth_config() -> OAuthConfig:
    """
    Load OAuth configuration from environment variables.
    """
    # Use your App's Client ID. 
    # NOTE: Ensure "Device Flow" is enabled in your GitHub App settings.
    client_id = os.getenv("GITHUB_CLIENT_ID", "Iv23litmRp80Z6wmlyRn")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    
    return OAuthConfig(
        client_id=client_id,
        # Convert empty string to None
        client_secret=client_secret if client_secret else None
    )


def web_flow_available() -> bool:
    """
    True when the standard OAuth Web Flow is usable.

    The Web Flow exchanges an authorization ``code`` for a token and that
    exchange requires ``GITHUB_CLIENT_SECRET``. When no secret is configured we
    must NOT advertise a web authorization URL — otherwise the browser is sent
    to GitHub and bounced back to the App's registered callback (often
    ``http://localhost:8000``), which is unreachable in production. In that
    case the caller should fall back to the Device Flow instead.
    """
    return bool(get_oauth_config().client_secret)


def resolve_redirect_uri(explicit: Optional[str] = None) -> Optional[str]:
    """
    Determine the OAuth callback URL the browser returns to after authorizing.

    Priority:
      1. ``explicit`` argument (caller-provided, e.g. from the request origin)
      2. ``GITHUB_REDIRECT_URI``         — full-URL override
      3. ``{GITPILOT_PUBLIC_BASE_URL}/auth`` — the deployed frontend /auth page
      4. ``None``                        — let GitHub use the App's registered
                                           default callback (legacy behaviour)

    The frontend ``/auth`` page (AuthPage) reads ``?code`` and ``?state`` from
    the URL and POSTs them to ``/api/auth/callback`` to finish the exchange, so
    the callback must point at the FRONTEND origin, not the API host.
    """
    candidate = (explicit or os.getenv("GITHUB_REDIRECT_URI", "")).strip()
    if candidate:
        return candidate.rstrip("/")

    base = os.getenv("GITPILOT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/auth"

    return None

# ============================================================================
# WEB FLOW (Standard OAuth2 - Requires Client Secret)
# ============================================================================

def generate_authorization_url(redirect_uri: Optional[str] = None) -> tuple[str, str]:
    """
    Generate a GitHub OAuth authorization URL (Web Flow) with S256 PKCE.
    Returns: (authorization_url, state)

    The state and PKCE verifier are stored server-side by
    :mod:`gitpilot.auth.oauth` (shared by every worker, single use, 10-minute
    lifetime). The redirect URI comes from ``GITHUB_REDIRECT_URI`` /
    ``GITPILOT_PUBLIC_BASE_URL``; ``redirect_uri`` is accepted for backward
    compatibility and must match it when given.
    """
    from .auth import oauth

    configured = resolve_redirect_uri()
    if redirect_uri and configured and redirect_uri.rstrip("/") != configured:
        raise ValueError("redirect_uri does not match the configured GitHub callback.")
    started = oauth.start("github", None)
    return started["authorization_url"], started["state"]


async def exchange_code_for_token(
    code: str,
    state: str,
    redirect_uri: Optional[str] = None,
) -> AuthSession:
    """
    Exchange an authorization code (with the stored PKCE verifier) for a token.

    Kept for API compatibility; the web app uses
    :func:`gitpilot.auth.session_router.complete_oauth`, which also stores the
    token server-side instead of returning it.
    """
    from fastapi import HTTPException

    from .auth import oauth

    try:
        tokens, _ = await oauth.exchange("github", code, state)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
    async with httpx.AsyncClient(timeout=15.0) as client:
        user = await _fetch_user_profile(client, tokens.access_token)
    return AuthSession(access_token=tokens.access_token, token_type="bearer", scope=tokens.scope, user=user)

# ============================================================================
# DEVICE FLOW (No Secret Required)
# ============================================================================

async def initiate_device_flow() -> Dict[str, Any]:
    """
    Step 1: Request a device code from GitHub.
    """
    config = get_oauth_config()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://github.com/login/device/code",
            data={
                "client_id": config.client_id,
                "scope": "repo user:email",
            },
            headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()


async def poll_device_token(device_code: str) -> Optional[AuthSession]:
    """
    Step 2: Exchange device code for token (Polling).
    
    Returns:
        AuthSession: If authentication is successful.
        None: If status is 'authorization_pending' or 'slow_down'.
    
    Raises:
        ValueError: If the code expired, access denied, or other errors.
    """
    config = get_oauth_config()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": config.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"}
        )
        data = response.json()

        # Handle GitHub Device Flow Errors
        if "error" in data:
            error_code = data["error"]
            # These are expected during polling
            if error_code in ["authorization_pending", "slow_down"]:
                return None 
            
            # These are actual failures
            desc = data.get("error_description", error_code)
            if error_code == "expired_token":
                raise ValueError("The device code has expired. Please try again.")
            if error_code == "access_denied":
                raise ValueError("Access denied by user.")
            
            raise ValueError(f"GitHub Auth Error: {desc}")

        access_token = data.get("access_token")
        if not access_token:
            return None

        # Success: Fetch User details
        user = await _fetch_user_profile(client, access_token)
        
        return AuthSession(
            access_token=access_token,
            token_type=data.get("token_type", "bearer"),
            scope=data.get("scope", ""),
            user=user
        )

# ============================================================================
# SHARED HELPERS
# ============================================================================

async def _fetch_user_profile(client: httpx.AsyncClient, token: str) -> GitHubUser:
    """Internal helper to fetch user profile with an existing client."""
    response = await client.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    u = response.json()
    
    return GitHubUser(
        login=u["login"], 
        id=u["id"], 
        avatar_url=u["avatar_url"],
        name=u.get("name"),
        email=u.get("email"),
        bio=u.get("bio"),
        html_url=u.get("html_url")
    )


async def validate_token(access_token: str) -> Optional[GitHubUser]:
    """
    Validate GitHub access token and return user info.
    Useful for checking if a stored session is still valid.
    """
    if not access_token:
        return None
        
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            return await _fetch_user_profile(client, access_token)
    except Exception as e:
        logger.debug(f"Token validation failed: {e}")
        return None
