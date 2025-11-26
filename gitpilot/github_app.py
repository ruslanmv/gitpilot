"""
GitHub App Installation Management - PROPER FIX

This checks which repositories ACTUALLY have the GitHub App installed
by querying the user's app installations.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Dict, Any, Set

import httpx

logger = logging.getLogger("gitpilot.github_app")

# Cache for installed repositories
_installed_repos_cache: Dict[str, Set[str]] = {}
_cache_timestamp: Dict[str, float] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


class GitHubAppConfig:
    """Configuration for GitHub App."""
    
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


async def get_installed_repositories(user_token: str) -> Set[str]:
    """
    Get list of repositories where the GitHub App is installed.
    
    Uses /user/installations endpoint to get all installations,
    then fetches repositories for each installation.
    
    Returns:
        Set of repository full names (e.g., "owner/repo")
    """
    cache_key = "installed_repos"
    
    # Check cache
    import time
    if cache_key in _installed_repos_cache:
        if time.time() - _cache_timestamp.get(cache_key, 0) < CACHE_TTL_SECONDS:
            logger.debug(f"Using cached installed repositories ({len(_installed_repos_cache[cache_key])} repos)")
            return _installed_repos_cache[cache_key]
    
    installed_repos: Set[str] = set()
    
    try:
        config = get_app_config()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get user's app installations
            installations_response = await client.get(
                "https://api.github.com/user/installations",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            
            if installations_response.status_code != 200:
                logger.warning(f"Failed to get installations: {installations_response.status_code}")
                return installed_repos
            
            installations_data = installations_response.json()
            installations = installations_data.get("installations", [])
            
            logger.info(f"Found {len(installations)} app installations")
            
            # For each installation, get the repositories
            for installation in installations:
                installation_id = installation.get("id")
                
                # Get repositories for this installation
                repos_response = await client.get(
                    f"https://api.github.com/user/installations/{installation_id}/repositories",
                    headers={
                        "Authorization": f"Bearer {user_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "gitpilot",
                    },
                )
                
                if repos_response.status_code == 200:
                    repos_data = repos_response.json()
                    repositories = repos_data.get("repositories", [])
                    
                    for repo in repositories:
                        full_name = repo.get("full_name")  # e.g., "owner/repo"
                        if full_name:
                            installed_repos.add(full_name)
                            logger.debug(f"  ✓ App installed on: {full_name}")
            
            logger.info(f"GitHub App is installed on {len(installed_repos)} repositories")
            
            # Cache the results
            _installed_repos_cache[cache_key] = installed_repos
            _cache_timestamp[cache_key] = time.time()
            
            return installed_repos
            
    except Exception as e:
        logger.error(f"Error getting installed repositories: {e}")
        return installed_repos


async def check_repo_write_access(
    owner: str, 
    repo: str, 
    user_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Check if user has write access to a repository.
    
    PROPER FIX: Checks BOTH:
    1. User has push permissions
    2. GitHub App is ACTUALLY installed on this specific repository
    
    Args:
        owner: Repository owner
        repo: Repository name
        user_token: User's OAuth token
        
    Returns:
        Dict with 'can_write', 'app_installed', 'auth_type', 'reason'
    """
    result = {
        "can_write": False,
        "app_installed": False,
        "auth_type": "none",
        "reason": "No token provided",
    }
    
    if not user_token:
        return result
    
    full_repo_name = f"{owner}/{repo}"
    
    try:
        # Step 1: Check user's push permissions
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "gitpilot",
                },
            )
            
            if response.status_code != 200:
                result["reason"] = f"Cannot access repository (status: {response.status_code})"
                logger.warning(f"❌ {full_repo_name}: {result['reason']}")
                return result
            
            repo_data = response.json()
            permissions = repo_data.get("permissions", {})
            has_push = permissions.get("push", False)
        
        # Step 2: Check if GitHub App is installed on this repo
        installed_repos = await get_installed_repositories(user_token)
        app_installed = full_repo_name in installed_repos
        
        # Step 3: Determine write access
        if app_installed:
            # App IS installed - agent can write!
            result["can_write"] = True
            result["app_installed"] = True
            result["auth_type"] = "github_app"
            result["reason"] = "GitHub App installed with write access"
            logger.info(f"✅ {full_repo_name}: App installed (agent can write)")
        elif has_push:
            # User has push but App NOT installed - agent operations will FAIL
            result["can_write"] = False
            result["app_installed"] = False
            result["auth_type"] = "user_only"
            result["reason"] = "User has push access but GitHub App NOT installed (install app for agent operations)"
            logger.warning(f"⚠️  {full_repo_name}: User can push but app NOT installed - agent will get 403 errors")
        else:
            # User has no push and App NOT installed
            result["can_write"] = False
            result["app_installed"] = False
            result["auth_type"] = "read_only"
            result["reason"] = "No push access and GitHub App not installed"
            logger.info(f"ℹ️  {full_repo_name}: Read-only access")
                
    except Exception as e:
        result["reason"] = f"Error checking access: {str(e)}"
        logger.error(f"❌ Error checking {full_repo_name}: {e}")
    
    return result


def clear_cache():
    """Clear all caches."""
    _installed_repos_cache.clear()
    _cache_timestamp.clear()
    logger.info("Cleared installation cache")


async def check_installation_for_repo(
    owner: str, 
    repo: str, 
    user_token: str
) -> Optional[Dict[str, Any]]:
    """
    Legacy function - kept for compatibility.
    """
    result = await check_repo_write_access(owner, repo, user_token)
    if result["app_installed"]:
        return {
            "installed": True,
            "owner": owner,
            "repo": repo,
        }
    return None