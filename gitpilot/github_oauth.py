# gitpilot/github_oauth.py

"""GitHub OAuth 2.0 authentication flow implementation (Web + Device Flow)."""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode

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

# In-memory OAuth state storage (For Web Flow)
_oauth_states: dict[str, OAuthState] = {}


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

# ============================================================================
# WEB FLOW (Standard OAuth2 - Requires Client Secret)
# ============================================================================

def generate_authorization_url() -> tuple[str, str]:
    """
    Generate GitHub OAuth authorization URL with PKCE (Web Flow).
    Returns: (authorization_url, state)
    """
    config = get_oauth_config()
    
    # 1. State for CSRF protection
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(32)

    # 2. Store state
    _oauth_states[state] = OAuthState(
        state=state,
        code_verifier=code_verifier,
        timestamp=time.time(),
    )
    _cleanup_old_states()

    # 3. Build URL
    params = {
        "client_id": config.client_id,
        "scope": "repo user:email",
        "state": state,
        "allow_signup": "true",
    }

    auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return auth_url, state


async def exchange_code_for_token(code: str, state: str) -> AuthSession:
    """
    Exchange authorization code for access token (Web Flow).
    Requires GITHUB_CLIENT_SECRET to be set.
    """
    config = get_oauth_config()
    
    if not config.client_secret:
        raise ValueError("Web Flow requires GITHUB_CLIENT_SECRET. Please use Device Flow or configure the secret.")

    # 1. Validate State
    if state not in _oauth_states:
        logger.error(f"State mismatch or expiration. Received: {state}")
        raise ValueError("Invalid OAuth state. The session may have expired. Please try again.")

    oauth_state = _oauth_states.pop(state)
    if time.time() - oauth_state.timestamp > 600:
        raise ValueError("OAuth interaction timed out.")

    # 2. Exchange Code
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            token_data = token_response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP Error contacting GitHub: {e}")
            raise ValueError("Failed to contact GitHub authentication server.")

        if "error" in token_data:
            raise ValueError(f"GitHub refused the connection: {token_data.get('error_description')}")

        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("No access_token returned from GitHub.")

        # 3. Fetch User
        user = await _fetch_user_profile(client, access_token)

        return AuthSession(
            access_token=access_token,
            token_type=token_data.get("token_type", "bearer"),
            scope=token_data.get("scope", ""),
            user=user,
        )

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


def _cleanup_old_states():
    """Remove OAuth states older than 10 minutes to prevent memory leaks."""
    current_time = time.time()
    expired_states = [
        state for state, data in _oauth_states.items()
        if current_time - data.timestamp > 600
    ]
    for state in expired_states:
        _oauth_states.pop(state, None)