"""
Agent Tools for GitPilot Multi-Agent System

Provides CrewAI-compatible tools for agents to explore and analyze repositories,
similar to Claude Code's repository exploration capabilities.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from crewai.tools import tool

from .github_api import get_repo_tree, get_file


# Global context for current repository
_current_repo_context: Dict[str, str] = {}


def set_repo_context(owner: str, repo: str):
    """Set the current repository context for tools."""
    global _current_repo_context
    _current_repo_context = {"owner": owner, "repo": repo}


def get_repo_context() -> tuple[str, str]:
    """Get the current repository context."""
    owner = _current_repo_context.get("owner", "")
    repo = _current_repo_context.get("repo", "")
    if not owner or not repo:
        raise ValueError("Repository context not set. Call set_repo_context first.")
    return owner, repo


async def get_repository_context_summary(owner: str, repo: str) -> Dict[str, Any]:
    """
    Programmatically gather repository context without using agent tools.
    This ensures we have actual repository state before planning.

    Returns:
        Dictionary with repository information:
        - all_files: list of all file paths
        - total_files: count of files
        - extensions: dict of extension -> count
        - directories: set of top-level directories
        - key_files: list of important files (README, etc.)
    """
    try:
        tree = await get_repo_tree(owner, repo)

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

            # Count extensions
            if "." in path:
                ext = "." + path.rsplit(".", 1)[1]
                extensions[ext] = extensions.get(ext, 0) + 1

            # Track directories
            if "/" in path:
                dir_path = path.rsplit("/", 1)[0]
                directories.add(dir_path.split("/")[0])

            # Identify key files
            path_lower = path.lower()
            if any(
                key in path_lower
                for key in [
                    "readme",
                    "license",
                    "makefile",
                    "dockerfile",
                    "requirements",
                    "package.json",
                    ".gitignore",
                ]
            ):
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
        return {
            "all_files": [],
            "total_files": 0,
            "extensions": {},
            "directories": set(),
            "key_files": [],
            "error": str(e),
        }


@tool("List all files in repository")
def list_repository_files() -> str:
    """
    Lists all files in the current repository with their paths and types.
    Returns a formatted list of all files in the repository.

    Use this tool when you need to:
    - Understand the repository structure
    - Find what files exist before planning changes
    - Identify files to modify or delete

    Returns:
        A formatted string listing all files in the repository
    """
    try:
        owner, repo = get_repo_context()

        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo))
        finally:
            loop.close()

        if not tree:
            return "Repository is empty - no files found."

        # Format as a clear list
        result = f"Repository: {owner}/{repo}\n"
        result += f"Total files: {len(tree)}\n\n"
        result += "Files:\n"
        for item in sorted(tree, key=lambda x: x["path"]):
            result += f"  - {item['path']}\n"

        return result
    except Exception as e:
        return f"Error listing repository files: {str(e)}"


@tool("Get directory structure")
def get_directory_structure() -> str:
    """
    Gets the hierarchical directory structure of the repository.
    Returns a tree-like view of folders and files.

    Use this tool when you need to:
    - Visualize the repository organization
    - Understand folder hierarchy
    - Identify which files are in which directories

    Returns:
        A formatted tree structure of the repository
    """
    try:
        owner, repo = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo))
        finally:
            loop.close()

        if not tree:
            return "Repository is empty - no files found."

        # Build directory structure
        dirs: Dict[str, List[str]] = {}
        files_at_root: List[str] = []

        for item in tree:
            path = item["path"]
            if "/" in path:
                dir_path = path.rsplit("/", 1)[0]
                file_name = path.rsplit("/", 1)[1]
                if dir_path not in dirs:
                    dirs[dir_path] = []
                dirs[dir_path].append(file_name)
            else:
                files_at_root.append(path)

        # Format as tree
        result = f"Repository: {owner}/{repo}\n\n"
        result += "Directory Structure:\n"
        result += ".\n"

        # Root files
        for f in sorted(files_at_root):
            result += f"├── {f}\n"

        # Directories
        for dir_path in sorted(dirs.keys()):
            result += f"├── {dir_path}/\n"
            for f in sorted(dirs[dir_path]):
                result += f"│   ├── {f}\n"

        return result
    except Exception as e:
        return f"Error getting directory structure: {str(e)}"


@tool("Read file content")
def read_file(file_path: str) -> str:
    """
    Reads the content of a specific file from the repository.

    Args:
        file_path: The path to the file to read (e.g., "README.md", "src/main.py")

    Use this tool when you need to:
    - Read the current content of a file
    - Analyze existing code or documentation
    - Understand what changes are needed

    Returns:
        The content of the file
    """
    try:
        owner, repo = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            content = loop.run_until_complete(get_file(owner, repo, file_path))
        finally:
            loop.close()

        return f"Content of {file_path}:\n---\n{content}\n---"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


@tool("Search for files by pattern")
def search_files(pattern: str) -> str:
    """
    Searches for files matching a pattern in the repository.

    Args:
        pattern: A substring to search for in file paths (e.g., ".py", "test", "README")

    Use this tool when you need to:
    - Find all files of a certain type
    - Locate files containing specific keywords
    - Filter files by extension or name pattern

    Returns:
        A list of files matching the pattern
    """
    try:
        owner, repo = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo))
        finally:
            loop.close()

        # Filter files matching pattern
        pattern_lower = pattern.lower()
        matching_files = [
            item["path"]
            for item in tree
            if pattern_lower in item["path"].lower()
        ]

        if not matching_files:
            return f"No files found matching pattern: {pattern}"

        result = f"Files matching '{pattern}' ({len(matching_files)} found):\n"
        for path in sorted(matching_files):
            result += f"  - {path}\n"

        return result
    except Exception as e:
        return f"Error searching for files: {str(e)}"


@tool("List files in directory")
def list_directory_files(directory: str) -> str:
    """
    Lists all files within a specific directory.

    Args:
        directory: The directory path to list (e.g., "src", "docs", "tests")

    Use this tool when you need to:
    - See all files in a specific folder
    - Understand the contents of a directory
    - Check if a directory exists and what it contains

    Returns:
        A list of files in the specified directory
    """
    try:
        owner, repo = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo))
        finally:
            loop.close()

        # Normalize directory path
        dir_path = directory.rstrip("/")
        if dir_path:
            dir_path += "/"

        # Filter files in directory
        files_in_dir = [
            item["path"]
            for item in tree
            if item["path"].startswith(dir_path) and
               "/" not in item["path"][len(dir_path):]  # Only direct children
        ]

        if not files_in_dir:
            return f"Directory '{directory}' not found or is empty."

        result = f"Files in directory '{directory}' ({len(files_in_dir)} files):\n"
        for path in sorted(files_in_dir):
            file_name = path[len(dir_path):]
            result += f"  - {file_name}\n"

        return result
    except Exception as e:
        return f"Error listing directory: {str(e)}"


@tool("Get repository summary")
def get_repository_summary() -> str:
    """
    Provides a comprehensive summary of the repository including:
    - Total number of files
    - File types distribution
    - Directory structure overview
    - Key files (README, LICENSE, config files)

    Use this tool when you need to:
    - Get a quick overview of the repository
    - Understand the project structure
    - Identify the main components

    Returns:
        A formatted summary of the repository
    """
    try:
        owner, repo = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo))
        finally:
            loop.close()

        if not tree:
            return "Repository is empty - no files found."

        # Analyze repository
        total_files = len(tree)
        extensions: Dict[str, int] = {}
        directories: set = set()
        key_files: List[str] = []

        for item in tree:
            path = item["path"]

            # Count extensions
            if "." in path:
                ext = "." + path.rsplit(".", 1)[1]
                extensions[ext] = extensions.get(ext, 0) + 1

            # Track directories
            if "/" in path:
                dir_path = path.rsplit("/", 1)[0]
                directories.add(dir_path.split("/")[0])

            # Identify key files
            path_lower = path.lower()
            if any(key in path_lower for key in ["readme", "license", "makefile", "dockerfile", "requirements", "package.json", ".gitignore"]):
                key_files.append(path)

        # Format summary
        result = f"Repository Summary: {owner}/{repo}\n"
        result += "=" * 50 + "\n\n"
        result += f"Total Files: {total_files}\n"
        result += f"Total Directories: {len(directories)}\n\n"

        result += "Top Directories:\n"
        for d in sorted(directories)[:10]:
            result += f"  - {d}/\n"

        result += "\nFile Types:\n"
        for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True)[:10]:
            result += f"  {ext}: {count} files\n"

        if key_files:
            result += "\nKey Files:\n"
            for f in sorted(key_files):
                result += f"  - {f}\n"

        return result
    except Exception as e:
        return f"Error generating repository summary: {str(e)}"


# Export all tools as a list
REPOSITORY_TOOLS = [
    list_repository_files,
    get_directory_structure,
    read_file,
    search_files,
    list_directory_files,
    get_repository_summary,
]