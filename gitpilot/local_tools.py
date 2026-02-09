# gitpilot/local_tools.py
"""CrewAI tools for local workspace file and shell operations.

These tools give agents the ability to read, write, search, and navigate
files on the local filesystem (within the sandboxed workspace directory),
and to run shell commands like test suites, linters, and build scripts.
"""
import asyncio
import concurrent.futures
import json
from typing import Optional

from crewai.tools import tool

from .workspace import WorkspaceManager, WorkspaceInfo
from .terminal import TerminalExecutor, TerminalSession

_ws_manager = WorkspaceManager()
_executor = TerminalExecutor()
_current_workspace: Optional[WorkspaceInfo] = None


def set_active_workspace(ws: WorkspaceInfo):
    global _current_workspace
    _current_workspace = ws


def get_active_workspace() -> Optional[WorkspaceInfo]:
    return _current_workspace


def _require_workspace() -> WorkspaceInfo:
    if _current_workspace is None:
        raise RuntimeError("No active workspace. Call set_active_workspace() first.")
    return _current_workspace


def _run_async(coro):
    """Bridge sync CrewAI tools to async workspace/terminal calls."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If a loop is already running (CrewAI thread), use a thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# -----------------------------------------------------------------------
# File operations
# -----------------------------------------------------------------------

@tool("Read local file")
def read_local_file(file_path: str) -> str:
    """Read a file from the local workspace. Returns the file content."""
    ws = _require_workspace()
    try:
        content = _run_async(_ws_manager.read_file(ws, file_path))
        return f"Content of {file_path}:\n---\n{content}\n---"
    except Exception as e:
        return f"Error reading {file_path}: {e}"


@tool("Write local file")
def write_local_file(file_path: str, content: str) -> str:
    """Write content to a file in the local workspace. Creates parent directories."""
    ws = _require_workspace()
    try:
        result = _run_async(_ws_manager.write_file(ws, file_path, content))
        return f"Written {result['size']} bytes to {result['path']}"
    except Exception as e:
        return f"Error writing {file_path}: {e}"


@tool("Delete local file")
def delete_local_file(file_path: str) -> str:
    """Delete a file from the local workspace."""
    ws = _require_workspace()
    try:
        deleted = _run_async(_ws_manager.delete_file(ws, file_path))
        return f"Deleted: {deleted}"
    except Exception as e:
        return f"Error deleting {file_path}: {e}"


@tool("List local files")
def list_local_files(directory: str = ".") -> str:
    """List all tracked and untracked files in a directory."""
    ws = _require_workspace()
    try:
        files = _run_async(_ws_manager.list_files(ws, directory))
        return "\n".join(files) if files else "No files found."
    except Exception as e:
        return f"Error listing files: {e}"


@tool("Search in files")
def search_in_files(pattern: str, path: str = ".") -> str:
    """Search for a text pattern across all files using git grep.
    Returns matching lines with file paths and line numbers."""
    ws = _require_workspace()
    try:
        matches = _run_async(_ws_manager.search_files(ws, pattern, path))
        if not matches:
            return "No matches found."
        lines = [f"{m['file']}:{m['line']}: {m['content']}" for m in matches[:50]]
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


# -----------------------------------------------------------------------
# Git operations
# -----------------------------------------------------------------------

@tool("Git diff")
def git_diff(staged: str = "false") -> str:
    """Show the current git diff (unstaged changes by default)."""
    ws = _require_workspace()
    try:
        return _run_async(_ws_manager.diff(ws, staged=staged.lower() == "true")) or "No changes."
    except Exception as e:
        return f"Error: {e}"


@tool("Git status")
def git_status() -> str:
    """Show the current git status."""
    ws = _require_workspace()
    try:
        status = _run_async(_ws_manager.status(ws))
        return json.dumps(status, indent=2)
    except Exception as e:
        return f"Error: {e}"


@tool("Git commit")
def git_commit(message: str, files: str = "") -> str:
    """Commit changes. Optionally specify files (comma-separated)."""
    ws = _require_workspace()
    try:
        file_list = [f.strip() for f in files.split(",") if f.strip()] or None
        result = _run_async(_ws_manager.commit(ws, message, file_list))
        return json.dumps(result)
    except Exception as e:
        return f"Error: {e}"


@tool("Git log")
def git_log(count: str = "10") -> str:
    """Show recent commit history."""
    ws = _require_workspace()
    try:
        commits = _run_async(_ws_manager.log(ws, int(count)))
        return json.dumps(commits, indent=2)
    except Exception as e:
        return f"Error: {e}"


# -----------------------------------------------------------------------
# Shell command execution
# -----------------------------------------------------------------------

@tool("Run shell command")
def run_command(command: str, timeout: str = "120") -> str:
    """Run a shell command in the workspace directory.
    Returns stdout, stderr, and exit code.
    Examples: 'npm test', 'python -m pytest', 'make build', 'ls -la'."""
    ws = _require_workspace()
    try:
        session = TerminalSession(workspace_path=ws.path)
        result = _run_async(_executor.execute(session, command, int(timeout)))
        output = f"Exit code: {result.exit_code}\n"
        if result.stdout:
            output += f"--- stdout ---\n{result.stdout}\n"
        if result.stderr:
            output += f"--- stderr ---\n{result.stderr}\n"
        if result.timed_out:
            output += "WARNING: Command timed out\n"
        if result.truncated:
            output += "WARNING: Output was truncated\n"
        return output
    except PermissionError as e:
        return f"Permission denied: {e}"
    except Exception as e:
        return f"Error: {e}"


# -----------------------------------------------------------------------
# Exports
# -----------------------------------------------------------------------

LOCAL_FILE_TOOLS = [
    read_local_file,
    write_local_file,
    delete_local_file,
    list_local_files,
    search_in_files,
]

LOCAL_GIT_TOOLS = [
    git_diff,
    git_status,
    git_commit,
    git_log,
]

LOCAL_SHELL_TOOLS = [
    run_command,
]

LOCAL_TOOLS = LOCAL_FILE_TOOLS + LOCAL_GIT_TOOLS + LOCAL_SHELL_TOOLS
