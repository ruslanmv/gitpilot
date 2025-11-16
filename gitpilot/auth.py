"""
OAuth authentication module for GitPilot.

Provides GitHub OAuth user authentication and session management.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from .settings import get_settings


class OAuthConfig(BaseModel):
    """OAuth configuration for GitHub."""
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str = "read:user,repo"


class UserSession(BaseModel):
    """User session data."""
    user_id: int
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    access_token: str
    created_at: float
    expires_at: Optional[float] = None


class SessionStore:
    """In-memory session store. For production, use Redis or database."""

    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}
        self._csrf_tokens: Dict[str, float] = {}

    def create_session(self, session_id: str, user_session: UserSession) -> None:
        """Create a new session."""
        self._sessions[session_id] = user_session

    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get session by ID."""
        session = self._sessions.get(session_id)
        if session and session.expires_at and time.time() > session.expires_at:
            # Session expired
            self.delete_session(session_id)
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self._sessions.pop(session_id, None)

    def create_csrf_token(self, token: str) -> None:
        """Create a CSRF token with 10-minute expiry."""
        self._csrf_tokens[token] = time.time() + 600  # 10 minutes

    def validate_csrf_token(self, token: str) -> bool:
        """Validate and consume a CSRF token."""
        expiry = self._csrf_tokens.pop(token, None)
        if expiry is None:
            return False
        return time.time() < expiry

    def cleanup_expired(self) -> None:
        """Clean up expired sessions and CSRF tokens."""
        now = time.time()
        # Clean sessions
        expired = [
            sid for sid, sess in self._sessions.items()
            if sess.expires_at and now > sess.expires_at
        ]
        for sid in expired:
            del self._sessions[sid]
        # Clean CSRF tokens
        expired_csrf = [token for token, exp in self._csrf_tokens.items() if now > exp]
        for token in expired_csrf:
            del self._csrf_tokens[token]


# Global session store (for production, use Redis)
session_store = SessionStore()


def get_oauth_config() -> OAuthConfig:
    """Get OAuth configuration from settings or environment."""
    settings = get_settings()
    github_config = settings.github

    # Check if OAuth is configured
    if not github_config.app_client_id or not github_config.app_client_secret:
        raise HTTPException(
            status_code=500,
            detail="GitHub OAuth not configured. Set GITPILOT_GH_APP_CLIENT_ID and GITPILOT_GH_APP_CLIENT_SECRET"
        )

    # Use environment variable for redirect URI or construct from host
    import os
    redirect_uri = os.getenv("GITPILOT_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")

    return OAuthConfig(
        client_id=github_config.app_client_id,
        client_secret=github_config.app_client_secret,
        redirect_uri=redirect_uri,
    )


def get_github_oauth_url(state: str) -> str:
    """Generate GitHub OAuth authorization URL."""
    config = get_oauth_config()
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": config.scope,
        "state": state,
    }
    return f"https://github.com/login/oauth/authorize?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> str:
    """Exchange OAuth code for access token."""
    config = get_oauth_config()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
        )

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange code for token")

    data = response.json()
    access_token = data.get("access_token")

    if not access_token:
        error = data.get("error_description") or data.get("error") or "Unknown error"
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    return access_token


async def get_github_user(access_token: str) -> Dict[str, Any]:
    """Get GitHub user information using access token."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch user information")

    return response.json()


def create_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def create_csrf_token() -> str:
    """Generate a secure CSRF token."""
    return secrets.token_urlsafe(32)


def set_session_cookie(response: Response, session_id: str, max_age: int = 86400 * 30) -> None:
    """Set secure session cookie (30 days default)."""
    response.set_cookie(
        key="gitpilot_session",
        value=session_id,
        max_age=max_age,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie."""
    response.delete_cookie(key="gitpilot_session")


def get_session_from_request(request: Request) -> Optional[UserSession]:
    """Extract and validate session from request cookies."""
    session_id = request.cookies.get("gitpilot_session")
    if not session_id:
        return None

    return session_store.get_session(session_id)


async def require_auth(request: Request) -> UserSession:
    """Dependency to require authentication. Raises 401 if not authenticated."""
    session = get_session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session
