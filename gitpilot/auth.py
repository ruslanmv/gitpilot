"""
Authentication module for GitPilot.

Provides two-layer enterprise authentication:
1. User Authentication: GitHub OAuth for user identity
2. Repository Access: GitHub App installation for repository access

Features:
- Secure credential storage using system keyring
- GitHub OAuth device flow for CLI authentication
- GitHub App JWT token generation
- Repository access control based on installations
"""

from __future__ import annotations

import json
import time
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
import keyring
from pydantic import BaseModel

# Constants
GITHUB_API_BASE = "https://api.github.com"
GITHUB_OAUTH_BASE = "https://github.com"
KEYRING_SERVICE = "gitpilot"
KEYRING_USER_TOKEN = "github_user_token"
KEYRING_APP_PRIVATE_KEY = "github_app_private_key"


class GitHubUserToken(BaseModel):
    """GitHub user OAuth token."""
    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    expires_at: Optional[datetime] = None
    refresh_token: Optional[str] = None


class GitHubAppConfig(BaseModel):
    """GitHub App configuration."""
    app_id: str
    installation_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    slug: Optional[str] = None


class AuthManager:
    """Manages authentication for GitPilot."""

    def __init__(self):
        """Initialize the authentication manager."""
        self.service_name = KEYRING_SERVICE

    # ============================================================================
    # User OAuth Authentication
    # ============================================================================

    def save_user_token(self, token: GitHubUserToken) -> None:
        """Save user OAuth token securely to keyring."""
        keyring.set_password(
            self.service_name,
            KEYRING_USER_TOKEN,
            token.model_dump_json()
        )

    def get_user_token(self) -> Optional[GitHubUserToken]:
        """Retrieve user OAuth token from keyring."""
        token_json = keyring.get_password(self.service_name, KEYRING_USER_TOKEN)
        if not token_json:
            return None
        try:
            return GitHubUserToken.model_validate_json(token_json)
        except Exception:
            return None

    def delete_user_token(self) -> None:
        """Delete user OAuth token from keyring."""
        try:
            keyring.delete_password(self.service_name, KEYRING_USER_TOKEN)
        except keyring.errors.PasswordDeleteError:
            pass  # Token doesn't exist

    def is_user_authenticated(self) -> bool:
        """Check if user is authenticated."""
        token = self.get_user_token()
        if not token:
            return False

        # Check if token is expired
        if token.expires_at and token.expires_at < datetime.now(timezone.utc):
            return False

        return True

    async def login_device_flow(self) -> GitHubUserToken:
        """
        Authenticate user using GitHub OAuth device flow.

        This is the recommended flow for CLI applications.
        Returns the user token after successful authentication.
        """
        # Note: You need to register a GitHub OAuth App and get client_id
        # For now, this is a placeholder. In production, you would:
        # 1. Create a GitHub OAuth App
        # 2. Get client_id
        # 3. Implement device flow as shown here

        # This would be loaded from settings
        from .settings import get_github_oauth_client_id

        client_id = get_github_oauth_client_id()
        if not client_id:
            raise ValueError(
                "GitHub OAuth client_id not configured. "
                "Please set GITPILOT_OAUTH_CLIENT_ID in your environment."
            )

        async with httpx.AsyncClient() as client:
            # Step 1: Request device and user codes
            response = await client.post(
                f"{GITHUB_OAUTH_BASE}/login/device/code",
                data={"client_id": client_id, "scope": "repo,read:user"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            device_data = response.json()

            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            verification_uri = device_data["verification_uri"]
            interval = device_data.get("interval", 5)

            print(f"\nPlease visit: {verification_uri}")
            print(f"Enter code: {user_code}\n")

            # Step 2: Poll for authorization
            while True:
                time.sleep(interval)

                response = await client.post(
                    f"{GITHUB_OAUTH_BASE}/login/oauth/access_token",
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )

                data = response.json()

                if "access_token" in data:
                    # Success!
                    token = GitHubUserToken(
                        access_token=data["access_token"],
                        token_type=data.get("token_type", "bearer"),
                        scope=data.get("scope", ""),
                    )
                    self.save_user_token(token)
                    return token

                if data.get("error") == "authorization_pending":
                    # Keep waiting
                    continue

                if data.get("error") == "slow_down":
                    # Increase interval
                    interval += 5
                    continue

                # Other errors
                raise ValueError(f"OAuth error: {data.get('error_description', data.get('error'))}")

    def logout(self) -> None:
        """Logout user by deleting stored credentials."""
        self.delete_user_token()
        self.delete_app_private_key()

    # ============================================================================
    # GitHub App Authentication
    # ============================================================================

    def save_app_private_key(self, private_key: str) -> None:
        """Save GitHub App private key securely to keyring."""
        keyring.set_password(
            self.service_name,
            KEYRING_APP_PRIVATE_KEY,
            private_key
        )

    def get_app_private_key(self) -> Optional[str]:
        """Retrieve GitHub App private key from keyring."""
        return keyring.get_password(self.service_name, KEYRING_APP_PRIVATE_KEY)

    def delete_app_private_key(self) -> None:
        """Delete GitHub App private key from keyring."""
        try:
            keyring.delete_password(self.service_name, KEYRING_APP_PRIVATE_KEY)
        except keyring.errors.PasswordDeleteError:
            pass

    def generate_app_jwt(self, app_id: str, private_key: str) -> str:
        """
        Generate JWT token for GitHub App authentication.

        Args:
            app_id: GitHub App ID
            private_key: GitHub App private key (PEM format)

        Returns:
            JWT token string
        """
        now = int(time.time())

        payload = {
            "iat": now - 60,  # Issued at (60 seconds in the past to allow for clock drift)
            "exp": now + (10 * 60),  # Expires in 10 minutes
            "iss": app_id,  # Issuer (App ID)
        }

        # Decode private key if it's base64 encoded
        if not private_key.startswith("-----BEGIN"):
            try:
                private_key = b64decode(private_key).decode("utf-8")
            except Exception:
                pass  # Already decoded

        # Generate JWT
        token = jwt.encode(payload, private_key, algorithm="RS256")
        return token

    async def get_installation_token(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
    ) -> str:
        """
        Get installation access token for GitHub App.

        Args:
            app_id: GitHub App ID
            installation_id: Installation ID
            private_key: GitHub App private key

        Returns:
            Installation access token
        """
        # Generate JWT
        jwt_token = self.generate_app_jwt(app_id, private_key)

        # Exchange JWT for installation token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["token"]

    async def list_installations(self, app_id: str, private_key: str) -> list[dict]:
        """
        List all installations of the GitHub App.

        Args:
            app_id: GitHub App ID
            private_key: GitHub App private key

        Returns:
            List of installation objects
        """
        jwt_token = self.generate_app_jwt(app_id, private_key)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/app/installations",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            response.raise_for_status()
            return response.json()

    async def list_installation_repos(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
    ) -> list[dict]:
        """
        List repositories accessible to the GitHub App installation.

        Args:
            app_id: GitHub App ID
            installation_id: Installation ID
            private_key: GitHub App private key

        Returns:
            List of repository objects
        """
        token = await self.get_installation_token(app_id, installation_id, private_key)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/installation/repositories",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("repositories", [])


# Global instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get the global authentication manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
