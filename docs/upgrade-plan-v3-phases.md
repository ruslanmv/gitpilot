# GitPilot v3 — Three-Phase Upgrade to Match & Surpass Claude Code

## Executive Summary

GitPilot currently operates exclusively through the GitHub REST API (remote-only).
Claude Code operates **locally on the user's machine** with full filesystem and shell
access. This document designs a 3-phase upgrade:

| Phase | Goal | Timeline | Test Count Target |
|-------|------|----------|-------------------|
| **Phase 1** | Feature parity with Claude Code | Core | +300 tests |
| **Phase 2** | Ecosystem & extensibility superiority | Growth | +200 tests |
| **Phase 3** | Innovation & intelligence superiority | Differentiation | +150 tests |

---

## Current Architecture (Baseline)

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  React UI   │────▶│  FastAPI API  │────▶│  GitHub REST API  │
│  (Vite)     │     │  (api.py)     │     │  (github_api.py)  │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────▼───────┐
                    │   CrewAI      │
                    │  (agentic.py) │
                    │  8 agents     │
                    │  18 tools     │
                    └──────────────┘
```

**Limitation:** Everything goes through GitHub API. No local files, no shell, no
persistent sessions, no hooks, no plugins.

---

# ═══════════════════════════════════════════════════════════════
# PHASE 1 — FEATURE PARITY WITH CLAUDE CODE
# ═══════════════════════════════════════════════════════════════

Phase 1 closes every critical gap so GitPilot can do everything Claude Code does.

## Architecture After Phase 1

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  React UI   │────▶│  FastAPI API  │────▶│  GitHub REST API  │
│  + Terminal │     │  (api.py)     │     │  (github_api.py)  │
│  + Diff View│     └──────┬───────┘     └──────────────────┘
│  + Sessions │            │
└─────────────┘     ┌──────▼───────┐     ┌──────────────────┐
                    │   CrewAI      │────▶│  Local Workspace  │
                    │  (agentic.py) │     │  (workspace.py)   │
                    │  12 agents    │     └────────┬─────────┘
                    │  35+ tools    │              │
                    └──────┬───────┘     ┌────────▼─────────┐
                           │             │  Terminal Exec    │
                    ┌──────▼───────┐     │  (terminal.py)    │
                    │  Sessions    │     └──────────────────┘
                    │  Checkpoints │
                    │  Hooks       │     ┌──────────────────┐
                    │  Permissions │     │  Context Memory   │
                    └──────────────┘     │  (memory.py)      │
                                         └──────────────────┘
```

---

## P1.1 — Local Workspace Manager

**File:** `gitpilot/workspace.py` (~350 lines)

The single most important missing feature. Claude Code works on local files;
GitPilot must too.

```python
# gitpilot/workspace.py
"""Local workspace manager — clone, sync, and operate on repositories locally.

Manages a workspace directory (~/.gitpilot/workspaces/{owner}/{repo}) where
repositories are cloned and kept in sync. All local file operations go through
this module to ensure consistency and security.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default workspace root
WORKSPACE_ROOT = Path.home() / ".gitpilot" / "workspaces"


@dataclass
class WorkspaceInfo:
    """Metadata about an active workspace."""
    owner: str
    repo: str
    path: Path
    branch: str
    remote_url: str
    is_dirty: bool = False
    last_sync: Optional[str] = None  # ISO timestamp


class WorkspaceManager:
    """Manages local git clones for repository operations.

    Key responsibilities:
    - Clone repositories on first access (shallow clone for speed)
    - Checkout and track branches
    - Provide file read/write/delete operations
    - Sync with remote (pull/push)
    - Create and manage feature branches
    - Garbage collection of stale workspaces
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = root or WORKSPACE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self._active: Dict[str, WorkspaceInfo] = {}

    def workspace_path(self, owner: str, repo: str) -> Path:
        """Return the local path for a repo workspace."""
        return self.root / owner / repo

    async def ensure_workspace(
        self,
        owner: str,
        repo: str,
        token: str,
        branch: Optional[str] = None,
    ) -> WorkspaceInfo:
        """Clone repo if not present, checkout branch, return workspace info.

        Uses shallow clone (depth=1) for initial speed.
        If workspace exists, fetches latest and checks out branch.
        """
        ws_path = self.workspace_path(owner, repo)
        remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        if not (ws_path / ".git").exists():
            # Fresh clone
            ws_path.mkdir(parents=True, exist_ok=True)
            await self._run_git(
                ["git", "clone", "--depth=1", remote_url, str(ws_path)],
                cwd=ws_path.parent,
            )
        else:
            # Fetch latest
            await self._run_git(
                ["git", "fetch", "origin"],
                cwd=ws_path,
                env={"GIT_ASKPASS": "echo", "GIT_TERMINAL_PROMPT": "0"},
            )

        # Checkout branch
        target_branch = branch or await self._default_branch(ws_path)
        await self._checkout(ws_path, target_branch)

        info = WorkspaceInfo(
            owner=owner,
            repo=repo,
            path=ws_path,
            branch=target_branch,
            remote_url=remote_url,
        )
        key = f"{owner}/{repo}"
        self._active[key] = info
        return info

    # --- File operations (the core of local editing) ---

    async def read_file(self, ws: WorkspaceInfo, file_path: str) -> str:
        """Read a file from the workspace."""
        full = ws.path / file_path
        if not full.resolve().is_relative_to(ws.path.resolve()):
            raise PermissionError(f"Path traversal blocked: {file_path}")
        return full.read_text(encoding="utf-8", errors="replace")

    async def write_file(
        self, ws: WorkspaceInfo, file_path: str, content: str
    ) -> Dict[str, Any]:
        """Write content to a file. Creates parent dirs if needed."""
        full = ws.path / file_path
        if not full.resolve().is_relative_to(ws.path.resolve()):
            raise PermissionError(f"Path traversal blocked: {file_path}")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"path": file_path, "size": len(content)}

    async def delete_file(self, ws: WorkspaceInfo, file_path: str) -> bool:
        """Delete a file from the workspace."""
        full = ws.path / file_path
        if not full.resolve().is_relative_to(ws.path.resolve()):
            raise PermissionError(f"Path traversal blocked: {file_path}")
        if full.exists():
            full.unlink()
            return True
        return False

    async def list_files(
        self, ws: WorkspaceInfo, directory: str = "."
    ) -> List[str]:
        """List files in directory (respects .gitignore via git ls-files)."""
        result = await self._run_git(
            ["git", "ls-files", "--cached", "--others",
             "--exclude-standard", directory],
            cwd=ws.path,
        )
        return [f for f in result.stdout.strip().split("\n") if f]

    async def search_files(
        self, ws: WorkspaceInfo, pattern: str, path: str = "."
    ) -> List[Dict[str, Any]]:
        """Grep for pattern in workspace (uses git grep for speed)."""
        try:
            result = await self._run_git(
                ["git", "grep", "-n", "--no-color", "-I", pattern, "--", path],
                cwd=ws.path,
                check=False,  # git grep returns 1 if no matches
            )
            matches = []
            for line in result.stdout.strip().split("\n"):
                if ":" in line and line:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        matches.append({
                            "file": parts[0],
                            "line": int(parts[1]),
                            "content": parts[2],
                        })
            return matches
        except Exception:
            return []

    # --- Git operations ---

    async def create_branch(
        self, ws: WorkspaceInfo, branch_name: str
    ) -> str:
        """Create and checkout a new branch."""
        await self._run_git(
            ["git", "checkout", "-b", branch_name], cwd=ws.path
        )
        ws.branch = branch_name
        return branch_name

    async def commit(
        self, ws: WorkspaceInfo, message: str, files: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """Stage files and commit. If files=None, stages all changes."""
        if files:
            await self._run_git(["git", "add"] + files, cwd=ws.path)
        else:
            await self._run_git(["git", "add", "-A"], cwd=ws.path)

        result = await self._run_git(
            ["git", "commit", "-m", message], cwd=ws.path
        )
        sha = await self._run_git(
            ["git", "rev-parse", "HEAD"], cwd=ws.path
        )
        return {"sha": sha.stdout.strip(), "message": message}

    async def push(
        self, ws: WorkspaceInfo, force: bool = False
    ) -> Dict[str, str]:
        """Push current branch to origin."""
        cmd = ["git", "push", "-u", "origin", ws.branch]
        if force:
            cmd.insert(2, "--force-with-lease")
        result = await self._run_git(cmd, cwd=ws.path)
        return {"branch": ws.branch, "status": "pushed"}

    async def diff(
        self, ws: WorkspaceInfo, staged: bool = False
    ) -> str:
        """Return git diff output."""
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        result = await self._run_git(cmd, cwd=ws.path)
        return result.stdout

    async def status(self, ws: WorkspaceInfo) -> Dict[str, Any]:
        """Return parsed git status."""
        result = await self._run_git(
            ["git", "status", "--porcelain=v2", "--branch"], cwd=ws.path
        )
        return self._parse_status(result.stdout)

    async def log(
        self, ws: WorkspaceInfo, count: int = 10
    ) -> List[Dict[str, str]]:
        """Return recent commit log."""
        result = await self._run_git(
            ["git", "log", f"-{count}", "--format=%H|%an|%ae|%s|%aI"],
            cwd=ws.path,
        )
        commits = []
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|", 4)
                commits.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "message": parts[3],
                    "date": parts[4] if len(parts) > 4 else "",
                })
        return commits

    async def stash(self, ws: WorkspaceInfo, pop: bool = False) -> str:
        """Stash or pop stashed changes."""
        cmd = ["git", "stash", "pop" if pop else "push"]
        result = await self._run_git(cmd, cwd=ws.path)
        return result.stdout.strip()

    async def merge(
        self, ws: WorkspaceInfo, branch: str
    ) -> Dict[str, Any]:
        """Merge a branch into current branch."""
        result = await self._run_git(
            ["git", "merge", branch], cwd=ws.path, check=False
        )
        has_conflicts = result.returncode != 0
        return {
            "success": not has_conflicts,
            "output": result.stdout,
            "conflicts": has_conflicts,
        }

    # --- Cleanup ---

    async def cleanup(self, owner: str, repo: str) -> bool:
        """Remove a workspace directory."""
        ws_path = self.workspace_path(owner, repo)
        if ws_path.exists():
            shutil.rmtree(ws_path)
            self._active.pop(f"{owner}/{repo}", None)
            return True
        return False

    # --- Private helpers ---

    async def _run_git(self, cmd, cwd=None, env=None, check=True):
        """Run a git command asynchronously."""
        full_env = {**os.environ, **(env or {})}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        stdout, stderr = await proc.communicate()
        result = type("Result", (), {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "returncode": proc.returncode,
        })()
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Git command failed: {' '.join(cmd)}\n{result.stderr}"
            )
        return result

    async def _default_branch(self, ws_path: Path) -> str:
        result = await self._run_git(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=ws_path, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("/")[-1]
        return "main"

    async def _checkout(self, ws_path: Path, branch: str):
        # Try checking out existing branch first
        result = await self._run_git(
            ["git", "checkout", branch], cwd=ws_path, check=False
        )
        if result.returncode != 0:
            # Try tracking remote branch
            await self._run_git(
                ["git", "checkout", "-b", branch, f"origin/{branch}"],
                cwd=ws_path, check=False,
            )

    @staticmethod
    def _parse_status(raw: str) -> Dict[str, Any]:
        modified, added, deleted, untracked = [], [], [], []
        branch_name = "unknown"
        for line in raw.split("\n"):
            if line.startswith("# branch.head"):
                branch_name = line.split()[-1]
            elif line.startswith("1 "):
                parts = line.split()
                xy = parts[1] if len(parts) > 1 else ""
                path = parts[-1] if parts else ""
                if "M" in xy:
                    modified.append(path)
                elif "A" in xy:
                    added.append(path)
                elif "D" in xy:
                    deleted.append(path)
            elif line.startswith("? "):
                untracked.append(line[2:])
        return {
            "branch": branch_name,
            "modified": modified,
            "added": added,
            "deleted": deleted,
            "untracked": untracked,
            "clean": not any([modified, added, deleted, untracked]),
        }
```

### Local Workspace CrewAI Tools

**File:** `gitpilot/local_tools.py` (~200 lines)

```python
# gitpilot/local_tools.py
"""CrewAI tools for local workspace file operations.

These tools give agents the ability to read, write, search, and navigate
files on the local filesystem (within the sandboxed workspace directory).
"""
from __future__ import annotations

import json
from crewai import tool
from .workspace import WorkspaceManager, WorkspaceInfo
from .agent_tools import get_repo_context

_ws_manager = WorkspaceManager()
_current_workspace: WorkspaceInfo | None = None


def set_active_workspace(ws: WorkspaceInfo):
    global _current_workspace
    _current_workspace = ws


def _require_workspace() -> WorkspaceInfo:
    if _current_workspace is None:
        raise RuntimeError("No active workspace. Call set_active_workspace() first.")
    return _current_workspace


def _run_async(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


@tool("Read local file")
def read_local_file(file_path: str) -> str:
    """Read a file from the local workspace. Returns the file content."""
    ws = _require_workspace()
    return _run_async(ws_manager.read_file(ws, file_path))


@tool("Write local file")
def write_local_file(file_path: str, content: str) -> str:
    """Write content to a file in the local workspace. Creates parent directories."""
    ws = _require_workspace()
    result = _run_async(_ws_manager.write_file(ws, file_path, content))
    return json.dumps(result)


@tool("Delete local file")
def delete_local_file(file_path: str) -> str:
    """Delete a file from the local workspace."""
    ws = _require_workspace()
    deleted = _run_async(_ws_manager.delete_file(ws, file_path))
    return f"Deleted: {deleted}"


@tool("List local files")
def list_local_files(directory: str = ".") -> str:
    """List all tracked and untracked files in a directory."""
    ws = _require_workspace()
    files = _run_async(_ws_manager.list_files(ws, directory))
    return "\n".join(files)


@tool("Search in files")
def search_in_files(pattern: str, path: str = ".") -> str:
    """Search for a text pattern across all files using git grep.
    Returns matching lines with file paths and line numbers."""
    ws = _require_workspace()
    matches = _run_async(_ws_manager.search_files(ws, pattern, path))
    if not matches:
        return "No matches found."
    lines = [f"{m['file']}:{m['line']}: {m['content']}" for m in matches]
    return "\n".join(lines[:50])  # Cap output


@tool("Git diff")
def git_diff(staged: str = "false") -> str:
    """Show the current git diff (unstaged changes by default)."""
    ws = _require_workspace()
    return _run_async(_ws_manager.diff(ws, staged=staged.lower() == "true"))


@tool("Git status")
def git_status() -> str:
    """Show the current git status (modified, added, deleted, untracked files)."""
    ws = _require_workspace()
    status = _run_async(_ws_manager.status(ws))
    return json.dumps(status, indent=2)


@tool("Git commit")
def git_commit(message: str, files: str = "") -> str:
    """Commit changes with a message. Optionally specify files (comma-separated)."""
    ws = _require_workspace()
    file_list = [f.strip() for f in files.split(",") if f.strip()] or None
    result = _run_async(_ws_manager.commit(ws, message, file_list))
    return json.dumps(result)


@tool("Git log")
def git_log(count: str = "10") -> str:
    """Show recent commit history."""
    ws = _require_workspace()
    commits = _run_async(_ws_manager.log(ws, int(count)))
    return json.dumps(commits, indent=2)


LOCAL_TOOLS = [
    read_local_file,
    write_local_file,
    delete_local_file,
    list_local_files,
    search_in_files,
    git_diff,
    git_status,
    git_commit,
    git_log,
]
```

---

## P1.2 — Terminal / Shell Executor

**File:** `gitpilot/terminal.py` (~250 lines)

Claude Code's ability to run arbitrary shell commands is its #1 power feature.

```python
# gitpilot/terminal.py
"""Sandboxed terminal command executor.

Runs shell commands within the workspace directory with configurable
timeout, size limits, and directory restrictions. Commands are executed
asynchronously and streamed to the caller.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Safety defaults
DEFAULT_TIMEOUT_SEC = 120       # 2 minutes
MAX_OUTPUT_BYTES = 512_000      # 512 KB
BLOCKED_COMMANDS = frozenset([
    "rm -rf /", "mkfs", "dd if=/dev/zero",
    ":(){ :|:& };:",  # fork bomb
])


@dataclass
class CommandResult:
    """Result of a terminal command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False


@dataclass
class TerminalSession:
    """Represents an active terminal session bound to a workspace."""
    workspace_path: Path
    env: Dict[str, str] = field(default_factory=dict)
    history: List[CommandResult] = field(default_factory=list)
    cwd: Path = field(default=None)

    def __post_init__(self):
        if self.cwd is None:
            self.cwd = self.workspace_path


class TerminalExecutor:
    """Execute shell commands safely within a workspace directory.

    Security measures:
    - Commands run in subprocess (not os.system)
    - Working directory locked to workspace
    - Configurable timeout (kills process on timeout)
    - Output size capping to prevent memory issues
    - Blocked command patterns
    - Optional allowlist mode for CI/CD
    """

    def __init__(
        self,
        allowed_commands: Optional[List[str]] = None,
        blocked_patterns: Optional[List[str]] = None,
    ):
        self.allowed_commands = allowed_commands  # None = allow all
        self.blocked_patterns = blocked_patterns or list(BLOCKED_COMMANDS)

    def _validate_command(self, command: str) -> bool:
        """Check command against security rules."""
        cmd_lower = command.lower().strip()
        for blocked in self.blocked_patterns:
            if blocked in cmd_lower:
                raise PermissionError(
                    f"Command blocked by security policy: {command}"
                )
        if self.allowed_commands is not None:
            base_cmd = cmd_lower.split()[0] if cmd_lower else ""
            if base_cmd not in self.allowed_commands:
                raise PermissionError(
                    f"Command not in allowlist: {base_cmd}"
                )
        return True

    async def execute(
        self,
        session: TerminalSession,
        command: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """Execute a shell command in the workspace.

        Returns CommandResult with stdout, stderr, exit_code, timing.
        Kills process if timeout exceeded.
        """
        self._validate_command(command)

        # Ensure CWD is within workspace
        resolved_cwd = session.cwd.resolve()
        ws_resolved = session.workspace_path.resolve()
        if not str(resolved_cwd).startswith(str(ws_resolved)):
            session.cwd = session.workspace_path

        full_env = {**os.environ, **session.env, **(env or {})}

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(session.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                preexec_fn=os.setsid,  # new process group for clean kill
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                timed_out = False
            except asyncio.TimeoutError:
                # Kill entire process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout_bytes, stderr_bytes = b"", b""
                timed_out = True

            duration_ms = int((time.monotonic() - start) * 1000)

            # Cap output size
            truncated = False
            if len(stdout_bytes) > MAX_OUTPUT_BYTES:
                stdout_bytes = stdout_bytes[:MAX_OUTPUT_BYTES]
                truncated = True
            if len(stderr_bytes) > MAX_OUTPUT_BYTES:
                stderr_bytes = stderr_bytes[:MAX_OUTPUT_BYTES]
                truncated = True

            result = CommandResult(
                command=command,
                exit_code=proc.returncode if not timed_out else -1,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                truncated=truncated,
                timed_out=timed_out,
            )

        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
            )

        session.history.append(result)
        return result

    async def execute_streaming(
        self,
        session: TerminalSession,
        command: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ):
        """Execute command and yield output lines as they arrive.

        Used for real-time terminal output in the UI.
        """
        self._validate_command(command)

        full_env = {**os.environ, **session.env}
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(session.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge streams
            env=full_env,
            preexec_fn=os.setsid,
        )

        start = time.monotonic()
        try:
            while True:
                if time.monotonic() - start > timeout:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    yield {"type": "error", "data": "Command timed out"}
                    break

                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=5.0
                )
                if not line:
                    break
                yield {
                    "type": "stdout",
                    "data": line.decode("utf-8", errors="replace"),
                }
        except asyncio.TimeoutError:
            yield {"type": "error", "data": "Read timeout"}
        finally:
            await proc.wait()
            yield {
                "type": "exit",
                "exit_code": proc.returncode,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
```

### Terminal CrewAI Tool

```python
# Addition to local_tools.py

@tool("Run shell command")
def run_command(command: str, timeout: str = "120") -> str:
    """Run a shell command in the workspace directory.
    Returns stdout, stderr, and exit code.
    Examples: 'npm test', 'python -m pytest', 'make build'."""
    ws = _require_workspace()
    from .terminal import TerminalExecutor, TerminalSession

    executor = TerminalExecutor()
    session = TerminalSession(workspace_path=ws.path)
    result = _run_async(executor.execute(session, command, int(timeout)))

    output = f"Exit code: {result.exit_code}\n"
    if result.stdout:
        output += f"--- stdout ---\n{result.stdout}\n"
    if result.stderr:
        output += f"--- stderr ---\n{result.stderr}\n"
    if result.timed_out:
        output += "⚠ Command timed out\n"
    return output
```

---

## P1.3 — Session Manager & Checkpoints

**File:** `gitpilot/session.py` (~300 lines)

Claude Code persists sessions and supports resume/fork/rewind.

```python
# gitpilot/session.py
"""Session persistence, resumption, and checkpoint management.

Sessions track the full conversation + workspace state. Checkpoints
snapshot the workspace at key moments so users can rewind.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SESSION_ROOT = Path.home() / ".gitpilot" / "sessions"


@dataclass
class Message:
    """A single message in the conversation."""
    role: str          # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    """A snapshot of workspace state at a point in time."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    message_index: int = 0           # index in messages list
    description: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    snapshot_path: Optional[str] = None  # path to tarball of workspace


@dataclass
class Session:
    """A persistent conversation session."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    name: Optional[str] = None       # user-given name
    repo_full_name: Optional[str] = None  # owner/repo
    branch: Optional[str] = None
    messages: List[Message] = field(default_factory=list)
    checkpoints: List[Checkpoint] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pr_number: Optional[int] = None  # linked PR
    status: str = "active"           # active | paused | completed
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **meta):
        self.messages.append(Message(role=role, content=content, metadata=meta))
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        data["messages"] = [Message(**m) for m in data.get("messages", [])]
        data["checkpoints"] = [Checkpoint(**c) for c in data.get("checkpoints", [])]
        return cls(**data)


class SessionManager:
    """Manages session lifecycle: create, save, load, list, fork, rewind."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or SESSION_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def create(
        self,
        repo_full_name: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Session:
        """Create a new session."""
        session = Session(
            repo_full_name=repo_full_name,
            branch=branch,
            name=name,
        )
        self.save(session)
        return session

    def save(self, session: Session):
        """Persist session to disk."""
        path = self._session_path(session.id)
        path.write_text(json.dumps(session.to_dict(), indent=2))

    def load(self, session_id: str) -> Session:
        """Load a session from disk."""
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        data = json.loads(path.read_text())
        return Session.from_dict(data)

    def list_sessions(
        self,
        repo_full_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by repo. Returns summaries."""
        sessions = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
                if repo_full_name and data.get("repo_full_name") != repo_full_name:
                    continue
                sessions.append({
                    "id": data["id"],
                    "name": data.get("name"),
                    "repo": data.get("repo_full_name"),
                    "branch": data.get("branch"),
                    "message_count": len(data.get("messages", [])),
                    "status": data.get("status", "active"),
                    "updated_at": data.get("updated_at"),
                    "pr_number": data.get("pr_number"),
                })
                if len(sessions) >= limit:
                    break
            except Exception:
                continue
        return sessions

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def fork(self, session_id: str, at_message: Optional[int] = None) -> Session:
        """Create a new session branching from an existing one.

        If at_message is given, only includes messages up to that index.
        """
        original = self.load(session_id)
        messages = original.messages
        if at_message is not None:
            messages = messages[:at_message + 1]

        forked = Session(
            repo_full_name=original.repo_full_name,
            branch=original.branch,
            name=f"Fork of {original.name or original.id}",
            messages=messages,
            metadata={"forked_from": original.id},
        )
        self.save(forked)
        return forked

    def create_checkpoint(
        self,
        session: Session,
        workspace_path: Optional[Path] = None,
        description: str = "",
    ) -> Checkpoint:
        """Create a checkpoint (snapshot) of the current state.

        If workspace_path is given, creates a tarball of the workspace.
        """
        checkpoint = Checkpoint(
            message_index=len(session.messages),
            description=description or f"Checkpoint at message {len(session.messages)}",
        )

        if workspace_path and workspace_path.exists():
            snap_dir = self.root / "snapshots" / session.id
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_path = snap_dir / f"{checkpoint.id}.tar.gz"
            shutil.make_archive(
                str(snap_path).replace(".tar.gz", ""),
                "gztar",
                root_dir=str(workspace_path),
            )
            checkpoint.snapshot_path = str(snap_path)

        session.checkpoints.append(checkpoint)
        self.save(session)
        return checkpoint

    def rewind_to_checkpoint(
        self,
        session: Session,
        checkpoint_id: str,
        workspace_path: Optional[Path] = None,
    ) -> Session:
        """Rewind session to a checkpoint.

        Truncates messages and optionally restores workspace from snapshot.
        """
        checkpoint = None
        for cp in session.checkpoints:
            if cp.id == checkpoint_id:
                checkpoint = cp
                break
        if checkpoint is None:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")

        # Truncate messages
        session.messages = session.messages[:checkpoint.message_index]

        # Restore workspace if snapshot exists
        if checkpoint.snapshot_path and workspace_path:
            snap_path = Path(checkpoint.snapshot_path)
            if snap_path.exists():
                # Clear workspace and extract snapshot
                if workspace_path.exists():
                    shutil.rmtree(workspace_path)
                workspace_path.mkdir(parents=True, exist_ok=True)
                shutil.unpack_archive(str(snap_path), str(workspace_path))

        # Remove checkpoints after this one
        idx = session.checkpoints.index(checkpoint)
        session.checkpoints = session.checkpoints[:idx + 1]

        self.save(session)
        return session
```

---

## P1.4 — Hook System

**File:** `gitpilot/hooks.py` (~200 lines)

```python
# gitpilot/hooks.py
"""Event hook system for workflow automation.

Allows users to register shell commands or Python callables that fire
on specific lifecycle events. Hooks are defined in .gitpilot/hooks.json
or programmatically.

Events:
  - session_start:     Session begins
  - session_end:       Session ends
  - pre_tool_use:      Before a tool runs (can block)
  - post_tool_use:     After a tool completes
  - pre_edit:          Before file edit (can block)
  - post_edit:         After file edit
  - pre_commit:        Before git commit (can block, like pre-commit)
  - post_commit:       After git commit
  - pre_push:          Before git push (can block)
  - user_message:      When user sends a message
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_EDIT = "pre_edit"
    POST_EDIT = "post_edit"
    PRE_COMMIT = "pre_commit"
    POST_COMMIT = "post_commit"
    PRE_PUSH = "pre_push"
    USER_MESSAGE = "user_message"


@dataclass
class HookDefinition:
    """A registered hook."""
    event: HookEvent
    name: str
    command: Optional[str] = None       # shell command to run
    callable: Optional[Callable] = None  # Python callable
    blocking: bool = False              # if True, can prevent the action
    timeout: int = 30                   # seconds


@dataclass
class HookResult:
    """Result of running a hook."""
    hook_name: str
    event: HookEvent
    success: bool
    output: str = ""
    blocked: bool = False  # True if hook blocked the action


class HookManager:
    """Register and fire lifecycle hooks."""

    def __init__(self):
        self._hooks: Dict[HookEvent, List[HookDefinition]] = {
            e: [] for e in HookEvent
        }

    def register(self, hook: HookDefinition):
        """Register a hook for an event."""
        self._hooks[hook.event].append(hook)
        logger.info("Registered hook '%s' for event '%s'", hook.name, hook.event)

    def unregister(self, event: HookEvent, name: str):
        """Remove a hook by name."""
        self._hooks[event] = [
            h for h in self._hooks[event] if h.name != name
        ]

    def load_from_file(self, path: Path):
        """Load hooks from a JSON config file.

        Format: [{"event": "post_edit", "name": "lint", "command": "ruff check ."}, ...]
        """
        if not path.exists():
            return
        try:
            hooks = json.loads(path.read_text())
            for h in hooks:
                self.register(HookDefinition(
                    event=HookEvent(h["event"]),
                    name=h["name"],
                    command=h.get("command"),
                    blocking=h.get("blocking", False),
                    timeout=h.get("timeout", 30),
                ))
        except Exception as e:
            logger.warning("Failed to load hooks from %s: %s", path, e)

    async def fire(
        self,
        event: HookEvent,
        context: Optional[Dict[str, Any]] = None,
        cwd: Optional[Path] = None,
    ) -> List[HookResult]:
        """Fire all hooks for an event. Returns results.

        If any blocking hook fails, the first failure's HookResult
        will have blocked=True.
        """
        results = []
        for hook in self._hooks.get(event, []):
            result = await self._run_hook(hook, context, cwd)
            results.append(result)
            if hook.blocking and not result.success:
                result.blocked = True
                break  # stop processing further hooks
        return results

    def is_blocked(self, results: List[HookResult]) -> bool:
        """Check if any hook blocked the action."""
        return any(r.blocked for r in results)

    async def _run_hook(
        self,
        hook: HookDefinition,
        context: Optional[Dict[str, Any]],
        cwd: Optional[Path],
    ) -> HookResult:
        """Execute a single hook."""
        try:
            if hook.command:
                return await self._run_command_hook(hook, context, cwd)
            elif hook.callable:
                output = hook.callable(context or {})
                return HookResult(
                    hook_name=hook.name, event=hook.event,
                    success=True, output=str(output),
                )
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=True, output="No action",
            )
        except Exception as e:
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=False, output=str(e),
            )

    async def _run_command_hook(
        self,
        hook: HookDefinition,
        context: Optional[Dict[str, Any]],
        cwd: Optional[Path],
    ) -> HookResult:
        """Run a shell command hook."""
        import os
        env = {**os.environ}
        if context:
            for k, v in context.items():
                env[f"GITPILOT_HOOK_{k.upper()}"] = str(v)

        proc = await asyncio.create_subprocess_shell(
            hook.command,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=hook.timeout
            )
            return HookResult(
                hook_name=hook.name,
                event=hook.event,
                success=proc.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=False, output="Hook timed out",
            )
```

---

## P1.5 — Context Memory System

**File:** `gitpilot/memory.py` (~180 lines)

Equivalent to Claude Code's `CLAUDE.md` — project-level conventions and memory.

```python
# gitpilot/memory.py
"""Project context memory — the GITPILOT.md system.

Loads project-specific conventions, rules, and context from:
  1. .gitpilot/GITPILOT.md     (project root — committed to repo)
  2. .gitpilot/rules/*.md      (modular rule files)
  3. .gitpilot/memory.json     (auto-learned patterns — local only)

The combined context is injected into agent system prompts so they
follow project conventions automatically.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MEMORY_FILE = "GITPILOT.md"
RULES_DIR = "rules"
AUTO_MEMORY_FILE = "memory.json"


@dataclass
class ProjectContext:
    """Combined project context for agent injection."""
    conventions: str = ""       # from GITPILOT.md
    rules: List[str] = field(default_factory=list)  # from rules/*.md
    auto_memory: Dict[str, Any] = field(default_factory=dict)  # learned patterns

    def to_system_prompt(self) -> str:
        """Format as a system prompt section."""
        parts = []
        if self.conventions:
            parts.append(f"## Project Conventions\n\n{self.conventions}")
        if self.rules:
            parts.append("## Project Rules\n\n" + "\n\n---\n\n".join(self.rules))
        if self.auto_memory:
            patterns = self.auto_memory.get("patterns", [])
            if patterns:
                parts.append(
                    "## Learned Patterns\n\n"
                    + "\n".join(f"- {p}" for p in patterns)
                )
        return "\n\n".join(parts)


from dataclasses import dataclass, field


class MemoryManager:
    """Load and manage project-level context and conventions."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.gitpilot_dir = workspace_path / ".gitpilot"

    def load_context(self) -> ProjectContext:
        """Load all context sources and return combined ProjectContext."""
        ctx = ProjectContext()

        # 1. Load GITPILOT.md
        md_path = self.gitpilot_dir / MEMORY_FILE
        if md_path.exists():
            ctx.conventions = md_path.read_text(encoding="utf-8")[:10_000]

        # 2. Load rules/*.md
        rules_dir = self.gitpilot_dir / RULES_DIR
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                content = rule_file.read_text(encoding="utf-8")[:5_000]
                ctx.rules.append(f"### {rule_file.stem}\n\n{content}")

        # 3. Load auto-learned memory
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        if auto_path.exists():
            try:
                ctx.auto_memory = json.loads(auto_path.read_text())
            except Exception:
                pass

        return ctx

    def save_auto_memory(self, memory: Dict[str, Any]):
        """Persist auto-learned patterns."""
        self.gitpilot_dir.mkdir(parents=True, exist_ok=True)
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        auto_path.write_text(json.dumps(memory, indent=2))

    def add_learned_pattern(self, pattern: str):
        """Add a pattern that the agent learned during a session."""
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        memory = {}
        if auto_path.exists():
            try:
                memory = json.loads(auto_path.read_text())
            except Exception:
                pass
        patterns = memory.setdefault("patterns", [])
        if pattern not in patterns:
            patterns.append(pattern)
            # Keep max 100 patterns
            memory["patterns"] = patterns[-100:]
            self.save_auto_memory(memory)

    def init_project(self):
        """Create .gitpilot/ with template GITPILOT.md."""
        self.gitpilot_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.gitpilot_dir / MEMORY_FILE
        if not md_path.exists():
            md_path.write_text(
                "# GitPilot Project Conventions\n\n"
                "<!-- Add your project conventions here. -->\n"
                "<!-- GitPilot agents will follow these automatically. -->\n\n"
                "## Code Style\n\n"
                "## Testing\n\n"
                "## Commit Messages\n\n"
            )
        (self.gitpilot_dir / RULES_DIR).mkdir(exist_ok=True)
```

---

## P1.6 — Permission System

**File:** `gitpilot/permissions.py` (~150 lines)

```python
# gitpilot/permissions.py
"""Fine-grained permission system for tool execution.

Controls what agents can do based on configurable policies.
Supports three modes:
  - NORMAL:  Ask user before risky operations
  - PLAN:    Read-only (no writes, no shell commands)
  - AUTO:    Allow everything automatically

Permissions are configured in .gitpilot/permissions.json or via API.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    NORMAL = "normal"    # ask before risky ops
    PLAN = "plan"        # read-only mode
    AUTO = "auto"        # approve everything


class Action(str, Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    RUN_COMMAND = "run_command"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    CREATE_ISSUE = "create_issue"
    CREATE_PR = "create_pr"
    MERGE_PR = "merge_pr"


# Default risk classification
RISKY_ACTIONS = frozenset([
    Action.DELETE_FILE,
    Action.GIT_PUSH,
    Action.MERGE_PR,
    Action.RUN_COMMAND,
])

READ_ONLY_ACTIONS = frozenset([
    Action.READ_FILE,
])


@dataclass
class PermissionPolicy:
    """Permission configuration."""
    mode: PermissionMode = PermissionMode.NORMAL
    allowed_actions: Optional[Set[Action]] = None  # None = all allowed
    blocked_paths: List[str] = field(default_factory=lambda: [
        ".env", "*.pem", "*.key", "credentials*", "secrets*",
    ])
    allowed_commands: Optional[List[str]] = None  # None = all commands
    require_confirmation: Set[Action] = field(
        default_factory=lambda: set(RISKY_ACTIONS)
    )


class PermissionManager:
    """Check and enforce permissions for agent actions."""

    def __init__(self, policy: Optional[PermissionPolicy] = None):
        self.policy = policy or PermissionPolicy()
        self._pending_approvals: Dict[str, bool] = {}

    def check(self, action: Action, context: Optional[Dict[str, Any]] = None) -> bool:
        """Check if an action is allowed under current policy.

        Returns True if allowed, raises PermissionError if blocked.
        """
        # Plan mode: only reads allowed
        if self.policy.mode == PermissionMode.PLAN:
            if action not in READ_ONLY_ACTIONS:
                raise PermissionError(
                    f"Action '{action}' blocked in plan mode (read-only)"
                )
            return True

        # Check blocked paths for file operations
        if context and "path" in context:
            self._check_path(context["path"])

        # Check allowed actions
        if self.policy.allowed_actions is not None:
            if action not in self.policy.allowed_actions:
                raise PermissionError(
                    f"Action '{action}' not in allowed actions"
                )

        # Auto mode: allow everything
        if self.policy.mode == PermissionMode.AUTO:
            return True

        # Normal mode: flag risky actions for confirmation
        if action in self.policy.require_confirmation:
            # In API mode, this returns a confirmation request
            # The UI should prompt the user
            return True  # Caller should check needs_confirmation()

        return True

    def needs_confirmation(self, action: Action) -> bool:
        """Check if an action requires user confirmation."""
        if self.policy.mode == PermissionMode.AUTO:
            return False
        if self.policy.mode == PermissionMode.PLAN:
            return False  # just blocked
        return action in self.policy.require_confirmation

    def _check_path(self, path: str):
        """Check if a file path is blocked by policy."""
        import fnmatch
        for pattern in self.policy.blocked_paths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(
                path.split("/")[-1], pattern
            ):
                raise PermissionError(
                    f"Access to '{path}' blocked by policy (matches '{pattern}')"
                )

    def load_from_file(self, path: Path):
        """Load permissions from .gitpilot/permissions.json."""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self.policy.mode = PermissionMode(data.get("mode", "normal"))
            if "blocked_paths" in data:
                self.policy.blocked_paths = data["blocked_paths"]
            if "allowed_commands" in data:
                self.policy.allowed_commands = data["allowed_commands"]
        except Exception as e:
            logger.warning("Failed to load permissions: %s", e)
```

---

## P1.7 — Headless / CI-CD Mode

**File:** `gitpilot/headless.py` (~120 lines)

```python
# gitpilot/headless.py
"""Headless execution mode for CI/CD pipelines.

Allows running GitPilot non-interactively from command line or
GitHub Actions / GitLab CI, outputting results as JSON.

Usage:
  gitpilot run --headless -r owner/repo -m "fix the login bug"
  gitpilot run --headless -r owner/repo --from-pr 42
  echo "add tests" | gitpilot run --headless -r owner/repo
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .agentic import dispatch_request, generate_plan, execute_plan
from .agent_tools import set_repo_context
from .workspace import WorkspaceManager
from .session import SessionManager


@dataclass
class HeadlessResult:
    """Result of a headless execution."""
    success: bool
    output: str
    session_id: Optional[str] = None
    pr_url: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps({
            "success": self.success,
            "output": self.output,
            "session_id": self.session_id,
            "pr_url": self.pr_url,
            "error": self.error,
        }, indent=2)


async def run_headless(
    repo_full_name: str,
    message: str,
    token: str,
    branch: Optional[str] = None,
    auto_pr: bool = False,
    from_pr: Optional[int] = None,
    output_format: str = "json",  # json | text | stream-json
) -> HeadlessResult:
    """Execute a request non-interactively.

    Suitable for CI/CD pipelines, GitHub Actions, or scripting.
    """
    owner, repo = repo_full_name.split("/")

    # Set up context
    set_repo_context(owner, repo, token=token, branch=branch or "main")

    # If from_pr, fetch PR details and use as context
    if from_pr:
        from .github_pulls import get_pull_request
        pr = await get_pull_request(owner, repo, from_pr, token=token)
        message = (
            f"PR #{from_pr}: {pr.get('title', '')}\n"
            f"{pr.get('body', '')}\n\n"
            f"User request: {message}"
        )

    try:
        # Use the dispatch system
        result = await dispatch_request(
            owner=owner,
            repo=repo,
            message=message,
            token=token,
            branch=branch,
        )

        return HeadlessResult(
            success=True,
            output=result if isinstance(result, str) else json.dumps(result),
        )

    except Exception as e:
        return HeadlessResult(
            success=False,
            output="",
            error=str(e),
        )
```

### CLI Integration

Add to `cli.py`:

```python
@cli.command()
def run(
    repo: str = typer.Option(..., "-r", "--repo", help="owner/repo"),
    message: str = typer.Option("", "-m", "--message"),
    headless: bool = typer.Option(False, help="Run non-interactively"),
    from_pr: Optional[int] = typer.Option(None, help="Context from PR number"),
    auto_pr: bool = typer.Option(False, help="Auto-create PR on completion"),
    output_format: str = typer.Option("json", help="json|text"),
):
    """Run GitPilot on a task non-interactively."""
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    if not message:
        typer.echo("Error: provide -m or pipe input", err=True)
        raise typer.Exit(1)

    token = os.environ.get("GITPILOT_GITHUB_TOKEN", "")
    result = asyncio.run(run_headless(
        repo, message, token,
        from_pr=from_pr, auto_pr=auto_pr,
        output_format=output_format,
    ))
    typer.echo(result.to_json() if output_format == "json" else result.output)
    raise typer.Exit(0 if result.success else 1)
```

---

## P1.8 — API Endpoints for Phase 1 Features

**Additions to `api.py`:**

```python
# --- Workspace endpoints ---
@app.post("/api/workspace/init")
async def init_workspace(req: WorkspaceInitRequest, ...):
    """Clone repo and initialize local workspace."""

@app.get("/api/workspace/status")
async def workspace_status(...):
    """Get workspace git status, branch, dirty state."""

@app.post("/api/workspace/file")
async def workspace_write_file(req: FileWriteRequest, ...):
    """Write a file in the local workspace."""

@app.get("/api/workspace/diff")
async def workspace_diff(...):
    """Get current workspace diff."""

# --- Terminal endpoints ---
@app.post("/api/terminal/execute")
async def terminal_execute(req: TerminalRequest, ...):
    """Execute a shell command in the workspace."""

@app.websocket("/api/terminal/stream")
async def terminal_stream(websocket: WebSocket):
    """WebSocket for streaming terminal output."""

# --- Session endpoints ---
@app.post("/api/sessions")
async def create_session(...):
    """Create a new session."""

@app.get("/api/sessions")
async def list_sessions(...):
    """List sessions, optionally filtered by repo."""

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Load a session with full conversation history."""

@app.post("/api/sessions/{session_id}/resume")
async def resume_session(session_id: str, ...):
    """Resume an existing session."""

@app.post("/api/sessions/{session_id}/fork")
async def fork_session(session_id: str, ...):
    """Fork a session at a specific point."""

@app.post("/api/sessions/{session_id}/checkpoint")
async def create_checkpoint(session_id: str, ...):
    """Create a checkpoint for rewinding."""

@app.post("/api/sessions/{session_id}/rewind")
async def rewind_to_checkpoint(session_id: str, ...):
    """Rewind to a specific checkpoint."""

# --- Hook endpoints ---
@app.get("/api/hooks")
async def list_hooks():
    """List all registered hooks."""

@app.post("/api/hooks")
async def register_hook(req: HookRequest):
    """Register a new hook."""

# --- Permission endpoints ---
@app.get("/api/permissions")
async def get_permissions():
    """Get current permission policy."""

@app.put("/api/permissions/mode")
async def set_permission_mode(mode: str):
    """Switch between normal/plan/auto modes."""

# --- Context/Memory endpoints ---
@app.get("/api/context")
async def get_project_context(...):
    """Get loaded GITPILOT.md + rules context."""

@app.post("/api/context/init")
async def init_project_context(...):
    """Initialize .gitpilot/ directory with template."""
```

---

## P1.9 — Frontend Additions for Phase 1

New React components needed:

```
frontend/components/
  TerminalPanel.jsx        — Embedded terminal with command input & output
  SessionSidebar.jsx       — Session list, resume, fork, rename
  DiffViewer.jsx           — Side-by-side diff display before committing
  CheckpointTimeline.jsx   — Visual timeline of checkpoints with rewind
  PermissionBanner.jsx     — Shows current mode (normal/plan/auto)
  ContextEditor.jsx        — Edit GITPILOT.md and rules
```

---

## P1 Integration into Existing Architecture

### Changes to `agentic.py`:

```python
# In dispatch_request(), after routing:
# 1. Initialize workspace if local mode
# 2. Load project context (GITPILOT.md)
# 3. Inject context into agent system prompts
# 4. Create checkpoint before execution
# 5. Fire pre_tool_use hooks before each tool
# 6. Fire post_tool_use hooks after each tool
# 7. Check permissions before risky actions
# 8. Save session after each message

# New agent builder using local tools:
def _build_local_code_writer(llm) -> Agent:
    return Agent(
        role="Local Code Writer",
        goal="Edit files directly on disk and run tests to verify",
        tools=LOCAL_TOOLS + [run_command],  # local file ops + shell
        ...
    )
```

### Changes to `agent_router.py`:

```python
# New category:
class RequestCategory(str, Enum):
    ...
    LOCAL_EDIT = "local_edit"      # direct file editing with verification
    TERMINAL = "terminal"          # shell command execution
    CONTEXT_QUERY = "context_query"  # questions about the project

# New patterns:
_LOCAL_EDIT_RE = re.compile(
    r"\b(edit|modify|change|update|fix|refactor)\b.*\b(file|code|function|class)\b",
    re.IGNORECASE,
)
_TERMINAL_RE = re.compile(
    r"\b(run|execute|test|build|install|deploy|npm|pip|make|docker)\b",
    re.IGNORECASE,
)
```

---

# ═══════════════════════════════════════════════════════════════
# PHASE 2 — ECOSYSTEM & EXTENSIBILITY SUPERIORITY
# ═══════════════════════════════════════════════════════════════

Phase 2 builds the ecosystem that makes GitPilot **better** than Claude Code
for teams and power users.

---

## P2.1 — MCP Client Support

**File:** `gitpilot/mcp_client.py` (~250 lines)

Connect to any MCP server (databases, Slack, Figma, Sentry, etc.)
and expose its tools to GitPilot agents.

```python
class MCPClient:
    """Connect to MCP (Model Context Protocol) servers.

    Supports:
    - HTTP/SSE remote servers
    - Local stdio subprocess servers
    - Tool discovery and lazy loading
    - OAuth authentication
    """
    async def connect(self, server_config: MCPServerConfig) -> MCPConnection
    async def list_tools(self, connection: MCPConnection) -> List[MCPTool]
    async def call_tool(self, connection, tool_name, params) -> Any
    def to_crewai_tools(self, connection) -> List[BaseTool]  # auto-wrap
```

**Config:** `.gitpilot/mcp.json`

```json
{
  "servers": [
    {"name": "postgres", "type": "stdio", "command": "npx @mcp/postgres", "args": ["--url", "$DATABASE_URL"]},
    {"name": "slack", "type": "http", "url": "https://mcp.slack.com/v1"},
    {"name": "sentry", "type": "sse", "url": "https://mcp.sentry.io/events"}
  ]
}
```

---

## P2.2 — Plugin & Skill System

**File:** `gitpilot/plugins.py` (~300 lines)

```python
class PluginManager:
    """Discover, install, and manage GitPilot plugins.

    Plugins can provide:
    - Skills (invocable commands)
    - Hooks (lifecycle automation)
    - MCP server configs
    - Custom agent types
    - UI components (React)
    """
    def install(self, source: str)         # git URL or local path
    def uninstall(self, plugin_name: str)
    def list_installed(self) -> List[PluginInfo]
    def load_skills(self) -> List[Skill]
    def load_hooks(self) -> List[HookDefinition]
```

**File:** `gitpilot/skills.py` (~200 lines)

```python
class Skill:
    """A reusable, invocable workflow.

    Defined as markdown files in .gitpilot/skills/:
      .gitpilot/skills/review.md
      .gitpilot/skills/deploy.md

    Invoked via /review, /deploy in chat.
    """
    name: str
    description: str
    prompt_template: str
    auto_trigger: bool = False  # trigger based on context
    required_tools: List[str] = []
```

---

## P2.3 — IDE Extensions

### VS Code Extension

**Package:** `gitpilot-vscode` (TypeScript)

```
Features:
- Sidebar panel connected to GitPilot API
- Inline chat (Ctrl+Shift+G)
- @file and @folder references in prompts
- Inline diff review
- Terminal integration
- Status bar showing active session
```

### JetBrains Plugin

**Package:** `gitpilot-jetbrains` (Kotlin)

```
Features:
- Tool window connected to GitPilot API
- Intention actions for code review
- Terminal integration
- Diff viewer integration
```

---

## P2.4 — Vision & Image Analysis

**File:** `gitpilot/vision.py` (~150 lines)

```python
class VisionAnalyzer:
    """Analyze images using multimodal LLM capabilities.

    Supports:
    - Screenshot analysis (UI bugs, design review)
    - Architecture diagram parsing
    - Error screenshot OCR
    - Design mockup → code generation
    """
    async def analyze_image(
        self, image_path: Path, prompt: str, llm_provider: str
    ) -> str

    async def compare_screenshots(
        self, before: Path, after: Path, prompt: str
    ) -> str

    async def extract_text(self, image_path: Path) -> str
```

---

## P2.5 — Multi-Model Smart Routing

**File:** `gitpilot/model_router.py` (~150 lines)

Route different tasks to different models for cost/quality optimization:

```python
class ModelRouter:
    """Route tasks to optimal models based on complexity.

    - Simple queries → fast/cheap model (Haiku, GPT-4o-mini)
    - Code generation → strong model (Sonnet, GPT-4o)
    - Complex reasoning → strongest model (Opus, o1)
    - Code review → specialized model
    """
    def select_model(self, task_category: RequestCategory, complexity: str) -> str
    def estimate_complexity(self, request: str) -> str  # low|medium|high
```

---

## P2.6 — Real-Time Collaboration

**File:** `gitpilot/collaboration.py` (~200 lines)

```python
class CollaborationManager:
    """Enable multiple users to share sessions and observe agents.

    Features:
    - Shared session viewing (read-only observers)
    - Session handoff between users
    - Team-wide skill/plugin sharing
    - Audit log of all agent actions
    """
    async def share_session(self, session_id, user_ids: List[str])
    async def broadcast_event(self, session_id, event: Dict)
    async def get_audit_log(self, repo, since: datetime) -> List[AuditEntry]
```

---

# ═══════════════════════════════════════════════════════════════
# PHASE 3 — INNOVATION & INTELLIGENCE SUPERIORITY
# ═══════════════════════════════════════════════════════════════

Phase 3 creates capabilities that **neither Claude Code nor GitHub Copilot have**.

---

## P3.1 — Agent Teams (Parallel Multi-Agent)

**File:** `gitpilot/agent_teams.py` (~300 lines)

```python
class AgentTeam:
    """Coordinate multiple agents working in parallel on subtasks.

    Unlike sequential CrewAI flows, teams:
    - Split large tasks into independent subtasks
    - Run agents in parallel (separate workspaces via git worktrees)
    - Merge results with conflict detection
    - Support peer-to-peer messaging between agents
    - Require plan approval from lead agent before execution

    Example: "Add authentication to the API"
    → Lead splits into: (1) user model, (2) auth middleware, (3) login endpoint, (4) tests
    → 4 agents work in parallel on 4 worktrees
    → Lead reviews and merges
    """
    async def plan_and_split(self, task, num_agents: int) -> List[SubTask]
    async def execute_parallel(self, subtasks: List[SubTask]) -> TeamResult
    async def merge_results(self, results: List[SubTaskResult]) -> MergeResult
```

---

## P3.2 — Self-Improving Agents (Learning Loop)

**File:** `gitpilot/learning.py` (~250 lines)

```python
class LearningEngine:
    """Agents that learn from execution outcomes.

    After each task:
    1. Evaluate outcome (did tests pass? was PR approved?)
    2. Extract patterns (what worked, what failed)
    3. Store in auto_memory for future sessions
    4. Adjust agent strategies based on repo-specific patterns

    Over time, GitPilot becomes specialized to each project.
    """
    async def evaluate_outcome(self, session: Session, result: Any) -> Evaluation
    async def extract_patterns(self, evaluation: Evaluation) -> List[str]
    async def update_strategies(self, repo: str, patterns: List[str])
    def get_repo_insights(self, repo: str) -> RepoInsights
```

---

## P3.3 — Cross-Repository Intelligence

**File:** `gitpilot/cross_repo.py` (~200 lines)

```python
class CrossRepoAnalyzer:
    """Analyze patterns across multiple repositories.

    Features:
    - Dependency graph across repos
    - Shared convention detection
    - Impact analysis (change in lib A affects services B, C, D)
    - Migration planning across repos
    - Monorepo-aware operations
    """
    async def analyze_dependencies(self, repos: List[str]) -> DependencyGraph
    async def impact_analysis(self, repo, change_description) -> ImpactReport
    async def suggest_migrations(self, repos, target_pattern) -> MigrationPlan
```

---

## P3.4 — Predictive Workflows

**File:** `gitpilot/predictions.py` (~200 lines)

```python
class PredictiveEngine:
    """Predict what the user needs next based on context.

    - After merging a PR → suggest updating changelog
    - After creating an issue → suggest assigning and labeling
    - After test failure → suggest debugging approach
    - After dependency update → suggest running full test suite
    - Before release → suggest version bump and changelog
    """
    def predict_next_actions(self, session: Session) -> List[SuggestedAction]
    def score_suggestions(self, actions: List[SuggestedAction]) -> List[ScoredAction]
```

---

## P3.5 — AI-Powered Security Scanner

**File:** `gitpilot/security.py` (~200 lines)

```python
class SecurityScanner:
    """Deep security analysis using AI + static analysis.

    Goes beyond basic SAST:
    - Understands business logic vulnerabilities
    - Detects auth/authz bypass patterns
    - Identifies data exposure risks
    - Checks dependency vulnerabilities (via OSV)
    - Generates security advisories
    - Suggests fixes with context
    """
    async def scan_repo(self, workspace: WorkspaceInfo) -> SecurityReport
    async def scan_diff(self, workspace, base_branch) -> DiffSecurityReport
    async def check_dependencies(self, workspace) -> DependencyReport
```

---

## P3.6 — Natural Language Database Queries

**File:** `gitpilot/nl_database.py` (~150 lines)

```python
class NLDatabaseAgent:
    """Query project databases using natural language.

    Connects via MCP to databases and translates natural language
    to SQL/queries. Shows results and can generate migration scripts.
    """
    async def query(self, question: str, db_connection) -> QueryResult
    async def generate_migration(self, description: str) -> MigrationScript
    async def explain_schema(self, db_connection) -> SchemaExplanation
```

---

# ═══════════════════════════════════════════════════════════════
# FILE INVENTORY & DEPENDENCY MAP
# ═══════════════════════════════════════════════════════════════

## New Files by Phase

### Phase 1 (7 new files, ~1,750 lines)
| File | Lines | Depends On |
|------|-------|------------|
| `gitpilot/workspace.py` | ~350 | git CLI |
| `gitpilot/local_tools.py` | ~200 | workspace.py, agent_tools.py |
| `gitpilot/terminal.py` | ~250 | asyncio |
| `gitpilot/session.py` | ~300 | json, shutil |
| `gitpilot/hooks.py` | ~200 | asyncio |
| `gitpilot/memory.py` | ~180 | pathlib |
| `gitpilot/permissions.py` | ~150 | json |
| `gitpilot/headless.py` | ~120 | agentic.py, workspace.py |

### Phase 2 (5 new files, ~1,050 lines)
| File | Lines | Depends On |
|------|-------|------------|
| `gitpilot/mcp_client.py` | ~250 | httpx, asyncio |
| `gitpilot/plugins.py` | ~300 | git, importlib |
| `gitpilot/skills.py` | ~200 | plugins.py |
| `gitpilot/vision.py` | ~150 | LLM provider |
| `gitpilot/model_router.py` | ~150 | settings.py |

### Phase 3 (6 new files, ~1,300 lines)
| File | Lines | Depends On |
|------|-------|------------|
| `gitpilot/agent_teams.py` | ~300 | workspace.py, agentic.py |
| `gitpilot/learning.py` | ~250 | session.py, memory.py |
| `gitpilot/cross_repo.py` | ~200 | workspace.py, github_api.py |
| `gitpilot/predictions.py` | ~200 | session.py, agent_router.py |
| `gitpilot/security.py` | ~200 | workspace.py, terminal.py |
| `gitpilot/nl_database.py` | ~150 | mcp_client.py |

## Modified Files (All Phases)
| File | Changes |
|------|---------|
| `agentic.py` | Local workspace integration, hook firing, context injection, team support |
| `agent_router.py` | New categories: LOCAL_EDIT, TERMINAL, CONTEXT_QUERY |
| `api.py` | ~25 new endpoints (workspace, terminal, sessions, hooks, permissions, context) |
| `a2a_adapter.py` | New methods for workspace, terminal, sessions |
| `cli.py` | `run` command for headless mode, `init` for project setup |
| `pyproject.toml` | New dependencies, CLI entry points |
| `Makefile` | Targets for IDE extension builds, plugin management |

## Frontend New Components
| Component | Phase | Purpose |
|-----------|-------|---------|
| `TerminalPanel.jsx` | P1 | Embedded terminal |
| `SessionSidebar.jsx` | P1 | Session management |
| `DiffViewer.jsx` | P1 | Side-by-side diffs |
| `CheckpointTimeline.jsx` | P1 | Checkpoint rewind |
| `PermissionBanner.jsx` | P1 | Permission mode display |
| `ContextEditor.jsx` | P1 | GITPILOT.md editor |
| `PluginBrowser.jsx` | P2 | Plugin marketplace |
| `SkillPalette.jsx` | P2 | /command palette |
| `ImageUpload.jsx` | P2 | Image analysis UI |
| `TeamMonitor.jsx` | P3 | Agent team dashboard |
| `SecurityDashboard.jsx` | P3 | Security scan results |
| `PredictionSuggestions.jsx` | P3 | Next-action suggestions |

---

# ═══════════════════════════════════════════════════════════════
# COMPETITIVE COMPARISON AFTER ALL PHASES
# ═══════════════════════════════════════════════════════════════

| Capability | Claude Code | GitPilot P1 | GitPilot P2 | GitPilot P3 |
|------------|-------------|-------------|-------------|-------------|
| Local file editing | ✅ | ✅ | ✅ | ✅ |
| Shell execution | ✅ | ✅ | ✅ | ✅ |
| Git operations | ✅ | ✅ | ✅ | ✅ |
| Session management | ✅ | ✅ | ✅ | ✅ |
| Checkpoints/rewind | ✅ | ✅ | ✅ | ✅ |
| Hook system | ✅ | ✅ | ✅ | ✅ |
| Permissions | ✅ | ✅ | ✅ | ✅ |
| Context memory | ✅ | ✅ | ✅ | ✅ |
| Headless/CI-CD | ✅ | ✅ | ✅ | ✅ |
| Diff preview | ✅ | ✅ | ✅ | ✅ |
| MCP support | ✅ | ❌ | ✅ | ✅ |
| Plugins/skills | ✅ | ❌ | ✅ | ✅ |
| IDE extensions | ✅ | ❌ | ✅ | ✅ |
| Vision/images | ✅ | ❌ | ✅ | ✅ |
| Multi-model routing | ❌ | ❌ | ✅ | ✅ |
| Real-time collab | ❌ | ❌ | ✅ | ✅ |
| **Agent teams** | Experimental | ❌ | ❌ | **✅ Production** |
| **Self-improving** | ❌ | ❌ | ❌ | **✅** |
| **Cross-repo intel** | ❌ | ❌ | ❌ | **✅** |
| **Predictive workflows** | ❌ | ❌ | ❌ | **✅** |
| **AI security scanner** | ❌ | ❌ | ❌ | **✅** |
| **NL database queries** | ❌ | ❌ | ❌ | **✅** |
| Web dashboard UI | ❌ | ✅ | ✅ | ✅ |
| Multi-LLM providers | ❌ | ✅ | ✅ | ✅ |
| A2A protocol | ❌ | ✅ | ✅ | ✅ |
| GitHub App auth | ❌ | ✅ | ✅ | ✅ |

**After Phase 1:** Feature parity (equal to Claude Code)
**After Phase 2:** Ecosystem superiority (better extensibility)
**After Phase 3:** Intelligence superiority (capabilities no competitor has)
