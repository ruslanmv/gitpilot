"""Tests for gitpilot.terminal module."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.terminal import (
    BLOCKED_PATTERNS,
    CommandResult,
    TerminalExecutor,
    TerminalSession,
)


# ---------------------------------------------------------------------------
# TerminalSession
# ---------------------------------------------------------------------------

class TestTerminalSession:
    def test_default_cwd_is_workspace(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        assert session.cwd == tmp_path

    def test_custom_cwd(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        session = TerminalSession(workspace_path=tmp_path, cwd=sub)
        assert session.cwd == sub

    def test_empty_history(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        assert session.history == []


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------

class TestCommandValidation:
    def test_blocked_rm_rf_root(self):
        executor = TerminalExecutor()
        with pytest.raises(PermissionError, match="Command blocked"):
            executor._validate_command("rm -rf /")

    def test_blocked_mkfs(self):
        executor = TerminalExecutor()
        with pytest.raises(PermissionError, match="Command blocked"):
            executor._validate_command("mkfs /dev/sda1")

    def test_blocked_fork_bomb(self):
        executor = TerminalExecutor()
        with pytest.raises(PermissionError, match="Command blocked"):
            executor._validate_command(":(){ :|:& };:")

    def test_blocked_dd(self):
        executor = TerminalExecutor()
        with pytest.raises(PermissionError, match="Command blocked"):
            executor._validate_command("dd if=/dev/zero of=/dev/sda")

    def test_allowed_command_passes(self):
        executor = TerminalExecutor()
        # Should not raise for a safe command
        executor._validate_command("ls -la")

    def test_allowlist_blocks_unlisted(self):
        executor = TerminalExecutor(allowed_commands=["ls", "cat"])
        with pytest.raises(PermissionError, match="not in allowlist"):
            executor._validate_command("rm file.txt")

    def test_allowlist_permits_listed(self):
        executor = TerminalExecutor(allowed_commands=["ls", "cat"])
        executor._validate_command("ls -la")  # should not raise

    def test_custom_blocked_patterns(self):
        executor = TerminalExecutor(blocked_patterns=["danger"])
        with pytest.raises(PermissionError, match="Command blocked"):
            executor._validate_command("danger --now")


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

class TestExecute:
    async def test_successful_command(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"hello\n", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await executor.execute(session, "echo hello")

        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.timed_out is False
        assert result.truncated is False
        assert len(session.history) == 1

    async def test_command_timeout(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await executor.execute(session, "sleep 999", timeout=1)

        assert result.timed_out is True
        assert result.exit_code == -1

    async def test_blocked_command_raises(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        with pytest.raises(PermissionError):
            await executor.execute(session, "rm -rf /")

    async def test_output_truncation(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        # 600KB of output (exceeds 512KB limit)
        big_output = b"x" * 600_000
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (big_output, b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await executor.execute(session, "cat bigfile")

        assert result.truncated is True
        assert len(result.stdout) == 512_000

    async def test_cwd_reset_on_traversal(self, tmp_path):
        """If session.cwd escapes workspace, it should be reset."""
        session = TerminalSession(workspace_path=tmp_path, cwd=Path("/tmp"))
        executor = TerminalExecutor()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            await executor.execute(session, "pwd")

        # cwd should have been reset to workspace_path
        assert session.cwd == tmp_path

    async def test_history_appended(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            await executor.execute(session, "echo a")
            await executor.execute(session, "echo b")

        assert len(session.history) == 2
        assert session.history[0].command == "echo a"
        assert session.history[1].command == "echo b"

    async def test_exception_returns_error_result(self, tmp_path):
        session = TerminalSession(workspace_path=tmp_path)
        executor = TerminalExecutor()

        with patch("asyncio.create_subprocess_shell", side_effect=OSError("fail")):
            result = await executor.execute(session, "echo test")

        assert result.exit_code == -1
        assert "fail" in result.stderr
