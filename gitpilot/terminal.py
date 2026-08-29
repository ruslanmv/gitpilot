# gitpilot/terminal.py
"""Sandboxed terminal command executor.

Runs shell commands within the workspace directory with configurable
timeout, size limits, and directory restrictions.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from .shell_safety import BLOCKED_PATTERNS as _SHARED_BLOCKED_PATTERNS
from .shell_safety import blocked_reason, strip_secret_env

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 120
MAX_OUTPUT_BYTES = 512_000

# Shared with the sandbox backends via :mod:`gitpilot.shell_safety` (Batch
# V4-0C).  This module used to carry its own shorter copy, so ``shutdown -h``
# was refused by the sandbox and accepted here.  Kept as a list because the
# name is part of this module's established surface.
BLOCKED_PATTERNS = list(_SHARED_BLOCKED_PATTERNS)


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
    """An active terminal session bound to a workspace."""

    workspace_path: Path
    env: Dict[str, str] = field(default_factory=dict)
    history: List[CommandResult] = field(default_factory=list)
    cwd: Optional[Path] = None

    def __post_init__(self):
        if self.cwd is None:
            self.cwd = self.workspace_path


class TerminalExecutor:
    """Execute shell commands safely within a workspace directory.

    Security:
    - Commands run via subprocess (never os.system)
    - Working directory locked to workspace
    - Configurable timeout with process-group kill
    - Output size capping
    - Blocked command patterns
    """

    def __init__(
        self,
        allowed_commands: Optional[List[str]] = None,
        blocked_patterns: Optional[List[str]] = None,
    ):
        self.allowed_commands = allowed_commands
        self.blocked_patterns = blocked_patterns or list(BLOCKED_PATTERNS)

    def _validate_command(self, command: str):
        cmd_lower = command.lower().strip()
        if blocked_reason(command, tuple(self.blocked_patterns)) is not None:
            raise PermissionError(f"Command blocked: {command}")
        if self.allowed_commands is not None:
            base_cmd = cmd_lower.split()[0] if cmd_lower else ""
            if base_cmd not in self.allowed_commands:
                raise PermissionError(f"Command not in allowlist: {base_cmd}")

    async def execute(
        self,
        session: TerminalSession,
        command: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """Execute a command and return captured output."""
        self._validate_command(command)

        resolved_cwd = session.cwd.resolve()
        ws_resolved = session.workspace_path.resolve()
        if not str(resolved_cwd).startswith(str(ws_resolved)):
            session.cwd = session.workspace_path

        # Inherited credentials are stripped (Batch V4-0C); anything the caller
        # or session passes explicitly is honoured.  Before this, every linter
        # and test suite the validation phase ran received GITHUB_TOKEN and
        # every provider API key.
        full_env = strip_secret_env(overrides={**session.env, **(env or {})})
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(session.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
                timed_out = False
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                stdout_bytes, stderr_bytes = b"", b""
                timed_out = True

            duration_ms = int((time.monotonic() - start) * 1000)

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
                command=command, exit_code=-1,
                stdout="", stderr=str(e),
                duration_ms=duration_ms,
            )

        session.history.append(result)
        return result

    async def execute_streaming(
        self,
        session: TerminalSession,
        command: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Execute command and yield output lines as they arrive."""
        self._validate_command(command)

        # Same workspace clamp :meth:`execute` applies — the two paths had
        # drifted, and this one runs the validation phase's test commands.
        resolved_cwd = (session.cwd or session.workspace_path).resolve()
        ws_resolved = session.workspace_path.resolve()
        if not str(resolved_cwd).startswith(str(ws_resolved)):
            session.cwd = session.workspace_path

        full_env = strip_secret_env(overrides=session.env)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(session.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=full_env,
        )

        start = time.monotonic()
        try:
            while True:
                if time.monotonic() - start > timeout:
                    proc.kill()
                    yield {"type": "error", "data": "Command timed out"}
                    break
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                yield {
                    "type": "stdout",
                    "data": line.decode("utf-8", errors="replace"),
                }
        finally:
            await proc.wait()
            yield {
                "type": "exit",
                "exit_code": proc.returncode,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
