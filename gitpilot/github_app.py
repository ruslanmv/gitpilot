# gitpilot/github_app.py
from __future__ import annotations

import os
import time
from typing import Optional, Dict

import httpx
from fastapi import HTTPException
import jwt  # PyJWT

# Define locally to avoid circular imports with github_api
GITHUB_API_BASE = "https://api.github.com"

# Simple in-memory cache for installation access tokens:
# { "<owner>/<repo>": {"token": str, "expires_at": float} }
_installation_token_cache: Dict[str, Dict[str, float]] = {}


def is_github_app_configured() -> bool:
    """Return True if GitHub App credentials are available."""
    return bool(os.getenv("GITHUB_APP_ID") and os.getenv("GITHUB_APP_PRIVATE_KEY"))


def _get_app_private_key() -> str:
    key = os.getenv("GITHUB_APP_PRIVATE_KEY")
    if not key:
        raise RuntimeError(
            "GITHUB_APP_PRIVATE_KEY is not configured. "
            "Set it in your environment."
        )
    # Support keys stored with literal '\n' (common in Docker/k8s)
    if "\\n" in key:
        key = key.replace("\\n", "\n")
    return key


def _create_app_jwt() -> str:
    """Create a short-lived JWT for the GitHub App."""
    app_id = os.getenv("GITHUB_APP_ID")
    if not app_id:
        raise RuntimeError("GITHUB_APP_ID is not configured.")

    private_key = _get_app_private_key()
    now = int(time.time())

    payload = {
        "iat": now - 60,      # issued 1 minute ago
        "exp": now + 9 * 60,  # expires in 9 minutes (max 10)
        "iss": app_id,
    }

    token = jwt.encode(payload, private_key, algorithm="RS256")
    # PyJWT v2 returns str, v1 bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


async def get_installation_for_repo(owner: str, repo: str) -> Optional[int]:
    """
    Return installation_id for the GitHub App on this repo, or None if not installed.
    """
    if not is_github_app_configured():
        return None

    jwt_token = _create_app_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient(base_url=GITHUB_API_BASE, headers=headers, timeout=10.0) as client:
        resp = await client.get(f"/repos/{owner}/{repo}/installation")

    if resp.status_code == 200:
        data = resp.json()
        return data.get("id")

    if resp.status_code == 404:
        # App not installed on this repo
        return None

    # Other errors usually mean config issues or API outages
    try:
        err_msg = resp.json().get("message", resp.text)
    except Exception:
        err_msg = resp.text

    print(f"[GitHub App] Check installation failed: {resp.status_code} - {err_msg}")
    return None


async def get_installation_token_for_repo(owner: str, repo: str) -> str:
    """
    Get (or mint) an installation access token for the GitHub App on this repo.

    Raises HTTPException if the app is not installed or credentials are missing.
    """
    if not is_github_app_configured():
        raise HTTPException(
            status_code=500,
            detail=(
                "GitHub App not configured on server "
                "(missing GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY)."
            ),
        )

    cache_key = f"{owner}/{repo}"
    now = time.time()

    # 1. Check cache
    cached = _installation_token_cache.get(cache_key)
    if cached and cached.get("expires_at", 0) - 60 > now:
        return cached["token"]

    # 2. Find installation id for this repo
    installation_id = await get_installation_for_repo(owner, repo)
    if not installation_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "GitHub App is not installed on this repository. "
                "Install the App via the 'Install App' button to enable write access."
            ),
        )

    # 3. Mint installation access token
    jwt_token = _create_app_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient(base_url=GITHUB_API_BASE, headers=headers, timeout=10.0) as client:
        resp = await client.post(f"/app/installations/{installation_id}/access_tokens")

    if resp.status_code != 201:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Failed to create app token: {resp.text}",
        )

    data = resp.json()
    token = data["token"]

    # Calculate expiry
    expires_at_str = data.get("expires_at")
    expires_at = now + 3600  # fallback 1h
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        expires_at = dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        pass

    _installation_token_cache[cache_key] = {
        "token": token,
        "expires_at": expires_at,
    }
    return token
