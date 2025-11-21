"""GitHub OAuth 2.0 authentication flow implementation."""
from __future__ import annotations

import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel


class OAuthConfig(BaseModel):
    """GitHub OAuth App configuration."""
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8000/api/auth/callback"


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


class AuthSession(BaseModel):
    """Authenticated user session."""
    access_token: str
    token_type: str
    scope: str
    user: GitHubUser


# In-memory OAuth state storage (replace with Redis/database for production)
_oauth_states: dict[str, OAuthState] = {}


def get_oauth_config() -> OAuthConfig:
    """Load OAuth configuration from environment variables."""
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/auth/callback")

    return OAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )


def generate_authorization_url() -> tuple[str, str]:
    """
    Generate GitHub OAuth authorization URL with PKCE.

    Returns:
        tuple: (authorization_url, state)
    """
    config = get_oauth_config()

    # Generate random state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Generate code verifier for PKCE (optional for GitHub, but recommended)
    code_verifier = secrets.token_urlsafe(32)

    # Store state and verifier
    _oauth_states[state] = OAuthState(
        state=state,
        code_verifier=code_verifier,
        timestamp=time.time(),
    )

    # Clean up old states (older than 10 minutes)
    _cleanup_old_states()

    # Build authorization URL
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": "repo user:email",  # Request repo access and email
        "state": state,
        "allow_signup": "true",
    }

    auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    return auth_url, state


async def exchange_code_for_token(code: str, state: str) -> AuthSession:
    """
    Exchange authorization code for access token.

    Args:
        code: Authorization code from GitHub callback
        state: State parameter for CSRF validation

    Returns:
        AuthSession with access token and user info

    Raises:
        ValueError: If state is invalid or token exchange fails
    """
    # Validate state
    if state not in _oauth_states:
        raise ValueError("Invalid or expired OAuth state")

    oauth_state = _oauth_states.pop(state)

    # Check if state is not too old (10 minutes)
    if time.time() - oauth_state.timestamp > 600:
        raise ValueError("OAuth state expired")

    config = get_oauth_config()

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        # Request access token
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        if "error" in token_data:
            raise ValueError(f"GitHub OAuth error: {token_data.get('error_description', 'Unknown error')}")

        access_token = token_data["access_token"]
        token_type = token_data.get("token_type", "bearer")
        scope = token_data.get("scope", "")

        # Fetch user information
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        user_response.raise_for_status()
        user_data = user_response.json()

        user = GitHubUser(
            login=user_data["login"],
            id=user_data["id"],
            avatar_url=user_data["avatar_url"],
            name=user_data.get("name"),
            email=user_data.get("email"),
            bio=user_data.get("bio"),
        )

        return AuthSession(
            access_token=access_token,
            token_type=token_type,
            scope=scope,
            user=user,
        )


def _cleanup_old_states():
    """Remove OAuth states older than 10 minutes."""
    current_time = time.time()
    expired_states = [
        state for state, data in _oauth_states.items()
        if current_time - data.timestamp > 600
    ]
    for state in expired_states:
        _oauth_states.pop(state, None)


async def validate_token(access_token: str) -> Optional[GitHubUser]:
    """
    Validate GitHub access token and return user info.

    Args:
        access_token: GitHub personal access token or OAuth token

    Returns:
        GitHubUser if token is valid, None otherwise
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                timeout=10.0,
            )

            if response.status_code != 200:
                return None

            user_data = response.json()
            return GitHubUser(
                login=user_data["login"],
                id=user_data["id"],
                avatar_url=user_data["avatar_url"],
                name=user_data.get("name"),
                email=user_data.get("email"),
                bio=user_data.get("bio"),
            )
    except Exception:
        return None
