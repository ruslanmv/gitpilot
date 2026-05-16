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
    Examples: 'npm test', 'python -m pytest', 'make build', 'ls -la'.

    When the user has selected a non-local sandbox in Settings (e.g.
    MatrixLab), this tool transparently delegates to that backend so
    the agent's autonomous build/test loop runs in the same isolation
    the chat UI's Run button uses. With the default ``subprocess``
    backend the call still goes through :class:`SubprocessSandbox`,
    which jails cwd to the workspace and scrubs secrets — strictly
    stronger than the previous host-direct path."""
    ws = _require_workspace()
    timeout_int = _coerce_timeout(timeout)
    try:
        # Prefer the configured sandbox.  Falls back to the legacy
        # TerminalSession path only on import errors so an environment
        # without httpx still runs the agent (existing behaviour).
        return _run_via_sandbox(command, timeout_int, ws.path)
    except _SandboxFallback:
        pass
    try:
        session = TerminalSession(workspace_path=ws.path)
        result = _run_async(_executor.execute(session, command, timeout_int))
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


@tool("Run code in sandbox")
def run_in_sandbox(language: str, code: str, timeout: str = "120") -> str:
    """Execute a self-contained code snippet in the configured sandbox.

    Use this when you want to verify that code you produced *actually
    works* before handing it back to the user — write the snippet,
    call this tool, read the captured stdout / stderr / exit code,
    and iterate. The snippet runs in an ephemeral tempdir (not the
    workspace), so file-system side effects don't pollute the repo.

    Supported languages: python, javascript (or js/node), bash (or
    sh/shell). Returns a single text block with the exit code,
    stdout, stderr, duration, and backend (Local subprocess /
    MatrixLab) so you can tell which sandbox executed the snippet.

    Error retrieval is the point of this tool: when the snippet
    fails, the full stderr trace comes back verbatim — the agent
    should read it, decide how to fix the bug, and re-run."""
    timeout_int = _coerce_timeout(timeout)
    try:
        return _run_snippet_via_sandbox(language, code, timeout_int)
    except _SandboxFallback as exc:
        return f"Error: sandbox unavailable ({exc}); cannot run snippet."


# ---------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------

class _SandboxFallback(Exception):
    """Raised when the sandbox path is unusable and the caller should
    fall back to the legacy TerminalSession executor."""


def _coerce_timeout(value: object) -> int:
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        return 120
    if n <= 0:
        return 120
    return min(n, 600)


def _format_sandbox_output(result, label: str) -> str:
    """Render a SandboxResult / SandboxRunResponse-shaped object as the
    same text block the agent has been reading from ``run_command``,
    so existing prompt parsing keeps working — just with the backend
    line appended so the agent (and the user reading the trace) can
    see which sandbox ran the command."""
    backend = getattr(result, "backend", None) or "subprocess"
    pretty = {
        "subprocess": "local subprocess",
        "matrixlab": "MatrixLab",
        "off": "pass-through (host)",
    }.get(backend, backend)
    lines = [f"Sandbox: {pretty}", f"Command: {label}", f"Exit code: {result.exit_code}"]
    if getattr(result, "duration_ms", None) is not None:
        lines.append(f"Duration: {result.duration_ms} ms")
    if result.stdout:
        lines.append("--- stdout ---")
        lines.append(result.stdout)
    if result.stderr:
        lines.append("--- stderr ---")
        lines.append(result.stderr)
    if getattr(result, "timed_out", False):
        lines.append("WARNING: Command timed out")
    if getattr(result, "truncated", False):
        lines.append("WARNING: Output was truncated")
    sbid = getattr(result, "sandbox_id", None)
    if sbid:
        lines.append(f"sandbox_id: {sbid}")
    return "\n".join(lines) + "\n"


def _run_via_sandbox(command: str, timeout: int, workspace_path) -> str:
    """Route ``run_command`` through the configured sandbox backend.

    Raises :class:`_SandboxFallback` so the caller can drop to the
    legacy TerminalSession path if the sandbox can't be constructed
    (e.g. httpx missing in a stripped runtime)."""
    try:
        from pathlib import Path

        from .sandbox import (
            BACKEND_MATRIXLAB,
            BACKEND_OFF,
            BACKEND_SUBPROCESS,
            MatrixLabSandbox,
            NullSandbox,
            SandboxPolicy,
            SandboxRunError,
            SandboxUnavailableError,
            SubprocessSandbox,
        )
        from .settings import get_settings
    except Exception as exc:  # noqa: BLE001
        raise _SandboxFallback(str(exc)) from exc

    cfg = get_settings().sandbox
    backend = (cfg.backend or BACKEND_SUBPROCESS).strip().lower()

    # MatrixLab's /repo/run endpoint requires a real ``repo_url`` —
    # that's the contract for cloning + running CI against a remote
    # repo, not for arbitrary in-workspace shell commands.  Route
    # workspace commands through the snippet path instead (POST
    # /api/sandbox/run with language=bash), which already dispatches
    # to MatrixLab /code/run.  Keeps the agent's run_command working
    # whichever backend the user picked.
    if backend == BACKEND_MATRIXLAB:
        return _run_snippet_via_sandbox("bash", command, timeout)

    policy = SandboxPolicy(
        workspace=Path(workspace_path),
        timeout_sec=timeout,
        allow_network=cfg.allow_network,
        image=cfg.matrixlab_image or None,
    )
    if backend == BACKEND_OFF:
        sb = NullSandbox(policy)
    else:
        sb = SubprocessSandbox(policy)

    # Run + close in a SINGLE event loop.  MatrixLabSandbox would
    # lazily build an httpx.AsyncClient on first use; closing it in a
    # different loop than it was created in is the textbook asyncio
    # antipattern (RuntimeError: Event loop is closed).  Two separate
    # asyncio.run() calls would do exactly that.  (For the
    # subprocess/null path the close is a no-op, but keeping the
    # pattern uniform means future backends can rely on it.)
    async def _run_and_close():
        try:
            return await sb.run(command, timeout=timeout)
        finally:
            aclose = getattr(sb, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass
    try:
        result = _run_async(_run_and_close())
    except SandboxUnavailableError as exc:
        return f"Error: sandbox backend {backend!r} unreachable: {exc}\n"
    except SandboxRunError as exc:
        return f"Error: sandbox backend {backend!r} reported an error: {exc}\n"
    except PermissionError as exc:
        return f"Permission denied by sandbox policy: {exc}\n"
    return _format_sandbox_output(result, command)


def _run_snippet_via_sandbox(language: str, code: str, timeout: int) -> str:
    """Execute a fenced snippet by POSTing to GitPilot's own
    /api/sandbox/run endpoint so the agent and the chat UI share one
    code path. Going via the HTTP surface (rather than reaching into
    sandbox_api internals) keeps the lifecycle / cleanup behaviour
    identical between the two callers."""
    try:
        import os

        import httpx
    except Exception as exc:  # noqa: BLE001
        raise _SandboxFallback(str(exc)) from exc

    port = os.environ.get("GITPILOT_PORT") or "8765"
    base = os.environ.get("GITPILOT_INTERNAL_URL") or f"http://127.0.0.1:{port}"
    body = {"language": language, "code": code, "timeout_sec": timeout}
    try:
        with httpx.Client(timeout=timeout + 10) as client:
            resp = client.post(f"{base}/api/sandbox/run", json=body)
    except httpx.HTTPError as exc:
        return f"Error: could not reach the in-process sandbox API: {exc}\n"
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa: BLE001
            detail = resp.text
        return f"Sandbox error ({resp.status_code}): {detail}\n"
    data = resp.json()

    class _R:
        backend = data.get("backend")
        exit_code = data.get("exit_code")
        stdout = data.get("stdout", "")
        stderr = data.get("stderr", "")
        duration_ms = data.get("duration_ms")
        timed_out = data.get("timed_out", False)
        truncated = data.get("truncated", False)
        sandbox_id = data.get("sandbox_id")

    return _format_sandbox_output(_R(), f"{language} <snippet>")


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
    run_in_sandbox,
]

LOCAL_TOOLS = LOCAL_FILE_TOOLS + LOCAL_GIT_TOOLS + LOCAL_SHELL_TOOLS
