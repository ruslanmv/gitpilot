"""
Agent Tools for GitPilot Multi-Agent System
Provides CrewAI-compatible tools for agents to explore and analyze repositories.
"""
import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple

from crewai.tools import tool

from .github_api import get_repo_tree, get_file


def _sanitize_tool_arg(value: Any, fallback_key: str = "description") -> str:
    """Fix CrewAI tool argument format bug.

    Smaller LLMs (deepseek-r1, qwen, phi) sometimes send tool arguments
    as a dict copying the schema definition instead of the actual value:
        {"description": "README.md", "type": "str"}
    instead of:
        "README.md"

    Worst case: the LLM copies the schema verbatim with a literal
    ``"None"`` value (because the tool exposes ``description: None``):
        {"description": "None", "type": "str"}

    This helper unwraps every variant we have seen in production and
    returns a plain string.  Raises ``ValueError`` only when the value
    cannot be recovered (e.g. the LLM passed a list or an empty dict)
    so the caller can surface a clear error instead of querying
    GitHub with a stringified Python dict.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # 1. Try the most likely human-supplied keys.
        for key in (fallback_key, "description", "value", "default", "title", "path"):
            v = value.get(key)
            if isinstance(v, str) and v and v.lower() != "none":
                return v
        # 2. Any other string field on the dict that isn't the schema
        #    ``type`` marker.
        for key, v in value.items():
            if key in {"type", "anyOf", "format"}:
                continue
            if isinstance(v, str) and v and v.lower() != "none":
                return v
        raise ValueError(
            f"tool argument arrived as a schema-shaped dict with no "
            f"usable value (got keys: {sorted(value.keys())!r}). "
            f"Pass the parameter as a plain string."
        )
    if value is None:
        raise ValueError("tool argument is required but received None")
    if isinstance(value, (list, tuple, set)):
        raise ValueError(
            f"tool argument expected a string, got a {type(value).__name__}; "
            f"pass a single value, not a sequence."
        )
    return str(value)

# Global context for current repository
# Now includes 'token' to ensure tools can authenticate even in threads
# AND includes 'branch' to ensure tools operate on the correct ref (not default HEAD/main)
_current_repo_context: Dict[str, Any] = {}
_context_lock = threading.RLock()


def set_repo_context(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    branch: Optional[str] = None,
):
    """Set the current repository context for tools."""
    global _current_repo_context
    with _context_lock:
        _current_repo_context = {
            "owner": owner,
            "repo": repo,
            "token": token,
            "branch": branch or "HEAD",
        }


def get_repo_context() -> Tuple[str, str, Optional[str], str]:
    """Get the current repository context including token and branch."""
    with _context_lock:
        owner = _current_repo_context.get("owner", "")
        repo = _current_repo_context.get("repo", "")
        token = _current_repo_context.get("token")
        branch = _current_repo_context.get("branch", "HEAD")

    if not owner or not repo:
        raise ValueError("Repository context not set. Call set_repo_context first.")
    return owner, repo, token, branch


async def get_repository_context_summary(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    branch: str = "HEAD",
) -> Dict[str, Any]:
    """Programmatically gather repository context."""
    try:
        # Pass token + ref explicitly
        tree = await get_repo_tree(owner, repo, token=token, ref=branch)

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
            if any(
                k in path_lower
                for k in ["readme", "package.json", "requirements.txt", "dockerfile", "makefile"]
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
        return {"error": str(e), "total_files": 0}


@tool("List all files in repository")
def list_repository_files() -> str:
    """Lists all files in the current repository."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"Repository is empty - no files found. (Branch: {branch})"

        result = f"Repository: {owner}/{repo} (Branch: {branch})\nFiles:\n"
        for item in sorted(tree, key=lambda x: x["path"]):
            result += f"  - {item['path']}\n"
        return result
    except Exception as e:
        return f"Error listing files: {str(e)}"


@tool("Get directory structure")
def get_directory_structure() -> str:
    """Gets the hierarchical directory structure."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"No files. (Branch: {branch})"

        # Simple structure generation
        paths = [t["path"] for t in tree]
        return f"Structure for {owner}/{repo} (Branch: {branch}):\n" + "\n".join(sorted(paths))
    except Exception as e:
        return f"Error: {str(e)}"


@tool("Read file content")
def read_file(file_path: Any) -> str:
    """Read the content of a file from the active repository.

    file_path: the file's path relative to the repository root, e.g.
    "README.md" or "src/main.py".  Pass a plain string — do **not** pass
    a dict like ``{"description": "...", "type": "str"}`` (that is the
    parameter's schema, not its value).
    """
    file_path = _sanitize_tool_arg(file_path)
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            content = loop.run_until_complete(get_file(owner, repo, file_path, token=token, ref=branch))
        finally:
            loop.close()

        return f"Content of {file_path}:\n---\n{content}\n---"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


@tool("Get repository summary")
def get_repository_summary() -> str:
    """Provides a comprehensive summary of the repository."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        return f"Summary for {owner}/{repo} (Branch: {branch}): {len(tree)} files found."
    except Exception as e:
        return f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# Write tools — allow agents to create, update, and delete files via GitHub API
# ---------------------------------------------------------------------------

@tool("Write or update a file in the repository")
def write_file(file_path: Any, content: Any, commit_message: Any) -> str:
    """Create or update a file in the repository.

    file_path: path relative to the repo root (plain string, e.g.
    ``"src/main.py"``).  content: the full new file content (plain
    string).  commit_message: a short imperative commit summary.  Do
    **not** wrap any of these in a ``{description, type}`` schema dict.
    """
    file_path = _sanitize_tool_arg(file_path)
    content = _sanitize_tool_arg(content, fallback_key="value")
    commit_message = _sanitize_tool_arg(commit_message, fallback_key="value")
    try:
        owner, repo, token, branch = get_repo_context()
        from .github_api import put_file

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                put_file(owner, repo, file_path, content, commit_message, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return f"File '{file_path}' written successfully. Commit: {sha[:8]}"
    except Exception as e:
        return f"Error writing file {file_path}: {str(e)}"


@tool("Delete a file from the repository")
def delete_repo_file(file_path: Any, commit_message: Any) -> str:
    """Delete a file from the repository.

    file_path: the path relative to the repo root (plain string, e.g.
    ``"docs/old.md"``).  commit_message: a short imperative commit
    summary.  Both are plain strings — never wrap them in a schema dict.
    """
    file_path = _sanitize_tool_arg(file_path)
    commit_message = _sanitize_tool_arg(commit_message, fallback_key="value")
    try:
        owner, repo, token, branch = get_repo_context()
        from .github_api import delete_file

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                delete_file(owner, repo, file_path, commit_message, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return f"File '{file_path}' deleted. Commit: {sha[:8]}"
    except Exception as e:
        return f"Error deleting file {file_path}: {str(e)}"


@tool("Create a new branch in the repository")
def create_repo_branch(branch_name: str) -> str:
    """Creates a new branch from the current HEAD."""
    branch_name = _sanitize_tool_arg(branch_name)
    try:
        owner, repo, token, _branch = get_repo_context()
        from .github_api import create_branch

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                create_branch(owner, repo, branch_name, from_ref="HEAD", token=token)
            )
        finally:
            loop.close()

        return f"Branch '{branch_name}' created successfully."
    except Exception as e:
        if "already exists" in str(e).lower() or "422" in str(e):
            return f"Branch '{branch_name}' already exists (OK to use)."
        return f"Error creating branch: {str(e)}"


# Export tools
REPOSITORY_TOOLS = [list_repository_files, get_directory_structure, read_file, get_repository_summary]
WRITE_TOOLS = [write_file, delete_repo_file, create_repo_branch]
