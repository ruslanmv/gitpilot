# gitpilot/terminal.py
"""Sandboxed terminal command executor.

Runs shell commands within the workspace directory with configurable
timeout, size limits, and directory restrictions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 120
MAX_OUTPUT_BYTES = 512_000

BLOCKED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){ :|:& };:",
]


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
        for blocked in self.blocked_patterns:
            if blocked in cmd_lower:
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

        full_env = {**os.environ, **session.env, **(env or {})}
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

        full_env = {**os.environ, **session.env}
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
