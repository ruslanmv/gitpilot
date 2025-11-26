"""
Agent Tools for GitPilot Multi-Agent System
Provides CrewAI-compatible tools for agents to explore and analyze repositories.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from crewai.tools import tool

from .github_api import get_repo_tree, get_file

# Global context for current repository
# Now includes 'token' to ensure tools can authenticate even in threads
_current_repo_context: Dict[str, Any] = {}

def set_repo_context(owner: str, repo: str, token: Optional[str] = None):
    """Set the current repository context for tools."""
    global _current_repo_context
    _current_repo_context = {"owner": owner, "repo": repo, "token": token}

def get_repo_context() -> tuple[str, str, Optional[str]]:
    """Get the current repository context including token."""
    owner = _current_repo_context.get("owner", "")
    repo = _current_repo_context.get("repo", "")
    token = _current_repo_context.get("token")
    if not owner or not repo:
        raise ValueError("Repository context not set. Call set_repo_context first.")
    return owner, repo, token

async def get_repository_context_summary(owner: str, repo: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Programmatically gather repository context."""
    try:
        # Pass token explicitly
        tree = await get_repo_tree(owner, repo, token=token)

        if not tree:
            return {
                "all_files": [],
                "total_files": 0,
                "extensions": {},
                "directories": set(),
                "key_files": [],
            }

        all_files = [item["path"] for item in tree]
        extensions: Dict[str, int] = {}
        directories: set = set()
        key_files: List[str] = []

        for item in tree:
            path = item["path"]
            if "." in path:
                ext = "." + path.rsplit(".", 1)[1]
                extensions[ext] = extensions.get(ext, 0) + 1
            if "/" in path:
                directories.add(path.split("/")[0])
            
            path_lower = path.lower()
            if any(k in path_lower for k in ["readme", "package.json", "requirements.txt", "dockerfile", "makefile"]):
                key_files.append(path)

        return {
            "all_files": all_files,
            "total_files": len(all_files),
            "extensions": extensions,
            "directories": directories,
            "key_files": key_files,
        }

    except Exception as e:
        print(f"[Error] Failed to get repository context: {str(e)}")
        return {"error": str(e), "total_files": 0}

@tool("List all files in repository")
def list_repository_files() -> str:
    """Lists all files in the current repository."""
    try:
        owner, repo, token = get_repo_context()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token))
        finally:
            loop.close()

        if not tree:
            return "Repository is empty - no files found."

        result = f"Repository: {owner}/{repo}\nFiles:\n"
        for item in sorted(tree, key=lambda x: x["path"]):
            result += f"  - {item['path']}\n"
        return result
    except Exception as e:
        return f"Error listing files: {str(e)}"

@tool("Get directory structure")
def get_directory_structure() -> str:
    """Gets the hierarchical directory structure."""
    try:
        owner, repo, token = get_repo_context()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token))
        finally:
            loop.close()

        if not tree: return "No files."
        
        # Simple structure generation
        paths = [t["path"] for t in tree]
        return f"Structure for {owner}/{repo}:\n" + "\n".join(sorted(paths))
    except Exception as e:
        return f"Error: {str(e)}"

@tool("Read file content")
def read_file(file_path: str) -> str:
    """Reads the content of a specific file."""
    try:
        owner, repo, token = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            content = loop.run_until_complete(get_file(owner, repo, file_path, token=token))
        finally:
            loop.close()

        return f"Content of {file_path}:\n---\n{content}\n---"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"

@tool("Get repository summary")
def get_repository_summary() -> str:
    """Provides a comprehensive summary of the repository."""
    try:
        owner, repo, token = get_repo_context()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token))
        finally:
            loop.close()
            
        return f"Summary for {owner}/{repo}: {len(tree)} files found."
    except Exception as e:
        return f"Error: {str(e)}"

# Export tools
REPOSITORY_TOOLS = [list_repository_files, get_directory_structure, read_file, get_repository_summary]