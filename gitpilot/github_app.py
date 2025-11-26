"""GitHub App authentication and installation token management.

This module handles GitHub App authentication including:
- JWT generation for App authentication
- Installation discovery for repositories
- Installation token minting with caching
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import httpx

try:
    import jwt
except ImportError:
    jwt = None

logger = logging.getLogger("gitpilot.github_app")

# Simple in-memory cache to prevent API spam
_installation_cache: Dict[str, Dict[str, Any]] = {}
_token_cache: Dict[int, Dict[str, Any]] = {}

CACHE_TTL_SECONDS = 300  # 5 minutes for installation lookup
TOKEN_CACHE_BUFFER = 60  # Refresh token 1 minute before expiry


class GitHubAppConfig:
    """Configuration for GitHub App authentication."""
    
    def __init__(self):
        self.app_id = os.getenv("GITHUB_APP_ID")
        self.private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        
        # Support loading private key from file path as fallback
        if not self.private_key and os.getenv("GITHUB_APP_PRIVATE_KEY_PATH"):
            key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
            try:
                with open(key_path, 'r') as f:
                    self.private_key = f.read()
            except Exception as e:
                logger.error(f"Failed to load private key from {key_path}: {e}")
    
    @property
    def is_configured(self) -> bool:
        """Check if GitHub App is properly configured."""
        return bool(self.app_id and self.private_key)


def get_app_config() -> GitHubAppConfig:
    """Get GitHub App configuration."""
    return GitHubAppConfig()


def generate_jwt() -> str:
    """
    Generate a JWT for GitHub App authentication.
    
    The JWT is valid for 10 minutes and is used to authenticate
    as the GitHub App itself (not as an installation).
    
    Returns:
        JWT token string
        
    Raises:
        ValueError: If App configuration is missing or jwt library not installed
    """
    if jwt is None:
        raise ValueError(
            "PyJWT library not installed. Install with: pip install pyjwt[crypto]"
        )
    
    config = get_app_config()
    
    if not config.is_configured:
        raise ValueError(
            "GitHub App not configured. Please set GITHUB_APP_ID and "
            "GITHUB_APP_PRIVATE_KEY environment variables."
        )
    
    # JWT payload - valid for 10 minutes
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60 seconds in the past to account for clock skew
        "exp": now + 600,  # Expires in 10 minutes
        "iss": config.app_id,
    }
    
    # Sign JWT with RS256
    token = jwt.encode(payload, config.private_key, algorithm="RS256")
    return token


async def get_installation_for_repo(owner: str, repo: str) -> Optional[Dict[str, Any]]:
    """
    Find the GitHub App installation for a specific repository.
    
    Uses in-memory caching to prevent excessive API calls.
    
    Args:
        owner: Repository owner username/org
        repo: Repository name
        
    Returns:
        Installation data dict with 'id', 'account', etc. or None if not installed
    """
    cache_key = f"{owner}/{repo}"
    
    # Check cache first
    if cache_key in _installation_cache:
        cached = _installation_cache[cache_key]
        if time.time() - cached["timestamp"] < CACHE_TTL_SECONDS:
            logger.debug(f"Using cached installation for {cache_key}")
            return cached["data"]
    
    try:
        app_jwt = generate_jwt()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get installation for this specific repo
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/installation",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            
            if response.status_code == 404:
                logger.info(f"GitHub App not installed on {owner}/{repo}")
                # Cache negative result too
                _installation_cache[cache_key] = {
                    "data": None,
                    "timestamp": time.time(),
                }
                return None
            
            response.raise_for_status()
            installation = response.json()
            
            # Cache the result
            _installation_cache[cache_key] = {
                "data": installation,
                "timestamp": time.time(),
            }
            
            logger.info(f"Found installation {installation['id']} for {owner}/{repo}")
            return installation
            
    except Exception as e:
        logger.error(f"Failed to get installation for {owner}/{repo}: {e}")
        return None


async def get_installation_token_for_repo(owner: str, repo: str) -> Optional[str]:
    """
    Get an installation access token for a specific repository.
    
    This token has the permissions granted to the GitHub App installation
    and can be used to make API calls on behalf of the installation.
    
    Tokens are cached until near expiry to minimize API calls.
    
    Args:
        owner: Repository owner username/org
        repo: Repository name
        
    Returns:
        Access token string or None if App is not installed
    """
    # First, get the installation
    installation = await get_installation_for_repo(owner, repo)
    if not installation:
        return None
    
    installation_id = installation["id"]
    
    # Check if we have a valid cached token
    if installation_id in _token_cache:
        cached_token = _token_cache[installation_id]
        expires_at = datetime.fromisoformat(cached_token["expires_at"].rstrip("Z"))
        
        # If token is still valid (with buffer), use it
        if datetime.utcnow() < expires_at - timedelta(seconds=TOKEN_CACHE_BUFFER):
            logger.debug(f"Using cached token for installation {installation_id}")
            return cached_token["token"]
    
    # Need to mint a new token
    try:
        app_jwt = generate_jwt()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Request installation token
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
                json={
                    "repositories": [repo],  # Limit scope to just this repo
                },
            )
            
            response.raise_for_status()
            token_data = response.json()
            
            # Cache the token
            _token_cache[installation_id] = {
                "token": token_data["token"],
                "expires_at": token_data["expires_at"],
            }
            
            logger.info(f"Minted new installation token for {owner}/{repo} (expires: {token_data['expires_at']})")
            return token_data["token"]
            
    except Exception as e:
        logger.error(f"Failed to get installation token for {owner}/{repo}: {e}")
        return None


async def check_repo_write_access(owner: str, repo: str, user_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if we have write access to a repository via User token or GitHub App.
    
    This is useful for the frontend to determine if it should show
    installation prompts.
    
    Args:
        owner: Repository owner
        repo: Repository name
        user_token: User's OAuth token (optional)
        
    Returns:
        Dict with 'can_write', 'app_installed', 'auth_type'
    """
    result = {
        "can_write": False,
        "app_installed": False,
        "auth_type": "none",
    }
    
    # Check User Token first (if provided)
    if user_token:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}",
                    headers={
                        "Authorization": f"Bearer {user_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "gitpilot",
                    },
                )
                
                if response.status_code == 200:
                    repo_data = response.json()
                    permissions = repo_data.get("permissions", {})
                    
                    if permissions.get("push"):
                        result["can_write"] = True
                        result["auth_type"] = "user_token"
                        logger.debug(f"User has push access to {owner}/{repo}")
                        return result
                    
        except Exception as e:
            logger.debug(f"User token check failed for {owner}/{repo}: {e}")
    
    # Check GitHub App Installation
    installation = await get_installation_for_repo(owner, repo)
    if installation:
        result["app_installed"] = True
        result["can_write"] = True
        result["auth_type"] = "github_app"
        logger.debug(f"GitHub App installed on {owner}/{repo}")
    
    return result


def clear_cache():
    """Clear all cached installation and token data. Useful for testing."""
    _installation_cache.clear()
    _token_cache.clear()
    logger.info("Cleared GitHub App cache")