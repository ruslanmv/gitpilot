"""
GitHub App Installation Management - OAuth-Based (No Private Keys Required)

This module properly checks if the GitHub App is ACTUALLY installed on repositories.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger("gitpilot.github_app")

# Simple in-memory cache for installation checks
_installation_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


class GitHubAppConfig:
    """Configuration for GitHub App (no private key needed)."""
    
    def __init__(self):
        self.app_id = os.getenv("GITHUB_APP_ID", "2313985")
        self.client_id = os.getenv("GITHUB_CLIENT_ID", "Iv23litmRp80Z6wmlyRn")
        self.app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    
    @property
    def is_configured(self) -> bool:
        """Check if GitHub App is configured."""
        return bool(self.app_id and self.client_id)


def get_app_config() -> GitHubAppConfig:
    """Get GitHub App configuration."""
    return GitHubAppConfig()


async def check_app_installation(
    owner: str, 
    repo: str, 
    user_token: str
) -> bool:
    """
    Check if the GitHub App is ACTUALLY installed on a repository.
    
    This uses the /repos/{owner}/{repo}/installation endpoint which returns
    the installation info if the App is installed.
    
    Args:
        owner: Repository owner
        repo: Repository name
        user_token: User's OAuth token
        
    Returns:
        True if App is installed, False otherwise
    """
    cache_key = f"app_install:{owner}/{repo}"
    
    # Check cache
    if cache_key in _installation_cache:
        cached = _installation_cache[cache_key]
        import time
        if time.time() - cached.get("timestamp", 0) < CACHE_TTL_SECONDS:
            return cached.get("installed", False)
    
    try:
        config = get_app_config()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if the App is installed on this repo
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/installation",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            
            is_installed = response.status_code == 200
            
            # Cache the result
            import time
            _installation_cache[cache_key] = {
                "installed": is_installed,
                "timestamp": time.time(),
            }
            
            if is_installed:
                installation_data = response.json()
                logger.info(f"GitHub App IS installed on {owner}/{repo} (installation_id: {installation_data.get('id')})")
            else:
                logger.info(f"GitHub App NOT installed on {owner}/{repo} (status: {response.status_code})")
            
            return is_installed
            
    except Exception as e:
        logger.error(f"Failed to check app installation for {owner}/{repo}: {e}")
        return False


async def check_user_permissions(
    owner: str, 
    repo: str, 
    user_token: str
) -> Dict[str, bool]:
    """
    Check the user's direct permissions on a repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        user_token: User's OAuth token
        
    Returns:
        Dict with 'has_push' and 'has_admin'
    """
    cache_key = f"user_perms:{owner}/{repo}"
    
    # Check cache
    if cache_key in _installation_cache:
        cached = _installation_cache[cache_key]
        import time
        if time.time() - cached.get("timestamp", 0) < CACHE_TTL_SECONDS:
            return cached.get("permissions", {"has_push": False, "has_admin": False})
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                
                result = {
                    "has_push": permissions.get("push", False),
                    "has_admin": permissions.get("admin", False),
                }
                
                # Cache the result
                import time
                _installation_cache[cache_key] = {
                    "permissions": result,
                    "timestamp": time.time(),
                }
                
                return result
            else:
                return {"has_push": False, "has_admin": False}
                
    except Exception as e:
        logger.error(f"Failed to check user permissions for {owner}/{repo}: {e}")
        return {"has_push": False, "has_admin": False}


async def check_repo_write_access(
    owner: str, 
    repo: str, 
    user_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Check if user has write access to a repository.
    
    CRITICAL FIX: This now properly checks if the GitHub App is ACTUALLY installed,
    not just if the user has personal push access.
    
    Args:
        owner: Repository owner
        repo: Repository name
        user_token: User's OAuth token
        
    Returns:
        Dict with 'can_write', 'app_installed', 'auth_type'
    """
    result = {
        "can_write": False,
        "app_installed": False,
        "auth_type": "none",
    }
    
    if not user_token:
        return result
    
    try:
        # Check BOTH: user permissions AND app installation
        user_perms = await check_user_permissions(owner, repo, user_token)
        app_installed = await check_app_installation(owner, repo, user_token)
        
        # Determine access level
        has_push = user_perms.get("has_push", False)
        
        if app_installed:
            # App is installed - this is the BEST case for agent operations
            result["can_write"] = True
            result["app_installed"] = True
            result["auth_type"] = "github_app"
            logger.info(f"✅ {owner}/{repo}: App installed (can_write=True)")
        elif has_push:
            # User has personal push access but App NOT installed
            # Agent operations will likely fail with "Resource not accessible by integration"
            result["can_write"] = False  # ← CRITICAL: Set to False because agent ops will fail
            result["app_installed"] = False
            result["auth_type"] = "user_token_only"
            logger.warning(f"⚠️  {owner}/{repo}: User has push but App NOT installed (agent operations will fail)")
        else:
            # User has no push access and App NOT installed
            result["can_write"] = False
            result["app_installed"] = False
            result["auth_type"] = "none"
            logger.info(f"❌ {owner}/{repo}: No push access, App not installed")
                
    except Exception as e:
        logger.error(f"Error checking access for {owner}/{repo}: {e}")
        result["can_write"] = False
        result["app_installed"] = False
        result["auth_type"] = "error"
    
    return result


def clear_cache():
    """Clear installation cache."""
    _installation_cache.clear()
    logger.info("Cleared installation cache")